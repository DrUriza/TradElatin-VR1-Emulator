"""Top-level TradELATIN runtime orchestration for all eight frozen families.

The orchestrator owns cross-family dependencies and filesystem publication.
It does not calculate indicators and it does not reinterpret provider payloads.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from processing_signals.classification.classification_pipeline import run_classification_pipeline
from processing_signals.classification.contract_builder_pipeline import run_contract_builder_pipeline
from processing_signals.input.input_pipeline import run_input_pipeline
from processing_signals.processing.processing_pipeline import run_processing_pipeline
from processing_signals.runtime.source_router import build_provider_router
from processing_signals.runtime.acquisition import AcquisitionManager
from processing_signals.runtime.contract_validator import validate_contracts_against_golden
from processing_signals.runtime.acquisition import plan_dirty_windows, recompute_plan
from processing_signals.runtime.emulator.overlay import EmulatorOverlay

FAMILY_ORDER = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)

SCREEN_FILENAMES = {
    "prices_ohlcv": "prices_screen.json",
    "cvd_volume_orderflow": "cvd_volume_orderflow_screen.json",
    "open_interest_and_funding": "open_interest_and_funding_screen.json",
    "etf_exchange_flows": "etf_exchange_flows_screen.json",
    "on_chain_miners": "on_chain_miners_screen.json",
    "volatility_market_regimes": "volatility_market_regimes_screen.json",
    "long_short_liquidations": "long_short_liquidations_screen.json",
    "liquidity_microstructure": "liquidity_microstructure_screen.json",
}

# The fixture library is intentionally heterogeneous in timestamp origin.  These
# values identify the native reference carried by each frozen provider fixture.
SYNTHETIC_REFERENCE_TIMESTAMPS = {
    "prices_ohlcv": 1786136400,
    "cvd_volume_orderflow": 1786136400,
    "open_interest_and_funding": 1786150800,
    "etf_exchange_flows": 1786147200,
    "on_chain_miners": 1786060800,
    "volatility_market_regimes": 1786143600,
    "long_short_liquidations": 1740000000,
    "liquidity_microstructure": 1786150800,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()


def _strict_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=False) + "\n"


def _atomic_write_json(path: Path, value: Any) -> Path:
    serialized = _strict_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return path
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def build_input_arguments(
    router: Any, *, source: str, reference_timestamp: int | None = None,
    requested_mode: str = "bootstrap", existing_inputs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the eight-family Input request set for emulator or live providers."""
    normalized_source = str(source).strip().lower()
    if normalized_source not in {"emulator", "real"}:
        raise ValueError("source must be 'emulator' or 'real'")
    synthetic = normalized_source == "emulator"
    now = int(reference_timestamp or datetime.now(tz=UTC).timestamp())
    refs = dict(SYNTHETIC_REFERENCE_TIMESTAMPS) if synthetic else {family: now for family in FAMILY_ORDER}
    if synthetic and reference_timestamp is not None:
        refs = {family: int(reference_timestamp) for family in FAMILY_ORDER}
    data_mode = "synthetic" if synthetic else "live"
    is_demo = synthetic
    pairs = {"Binance": "BTCUSDT", "OKX": "BTCUSDT", "Bybit": "BTCUSDT", "Hyperliquid": "BTCUSDT"}
    previous = existing_inputs or {}
    return {
        "prices_ohlcv": {
            "fetcher": router.for_family("prices_ohlcv"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("prices_ohlcv"),
            "bootstrap_limit": 500,
        },
        "etf_exchange_flows": {
            "fetcher": router.for_family("etf_exchange_flows"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("etf_exchange_flows"),
            "include_secondary": True,
            "data_mode": data_mode,
            "is_demo": is_demo,
            "exchange_scope": "all_exchange",
            "symbol": "BTC",
            "now": refs["etf_exchange_flows"],
        },
        "liquidity_microstructure": {
            "fetcher": router.for_family("liquidity_microstructure"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("liquidity_microstructure"),
            "reference_timestamp": refs["liquidity_microstructure"],
            "execution_timestamp": refs["liquidity_microstructure"] + (5 if synthetic else 0),
            "data_mode": data_mode,
            "is_demo": is_demo,
            "history_limit": 100,
            "hourly_history_limit": 20_000 if requested_mode == "bootstrap" else 100,
        },
        "long_short_liquidations": {
            "fetcher": router.for_family("long_short_liquidations"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("long_short_liquidations"),
            "reference_timestamp": refs["long_short_liquidations"],
            "clock": lambda ref=refs["long_short_liquidations"]: ref + (5 if synthetic else 0),
            "exchange_pairs": pairs,
            "history_hours": 72,
        },
        "on_chain_miners": {
            "fetcher": router.for_family("on_chain_miners"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("on_chain_miners"),
            "reference_timestamp": refs["on_chain_miners"],
            "execution_timestamp": refs["on_chain_miners"] + (5 if synthetic else 0),
            "data_mode": data_mode,
            "is_demo": is_demo,
        },
        "open_interest_and_funding": {
            "fetcher": router.for_family("open_interest_and_funding"),
            "requested_mode": requested_mode,
            "existing_state": previous.get("open_interest_and_funding"),
            "reference_timestamp": refs["open_interest_and_funding"],
            "execution_timestamp": refs["open_interest_and_funding"] + (5 if synthetic else 0),
            "data_mode": data_mode,
            "is_demo": is_demo,
        },
        "volatility_market_regimes": {
            "fetcher": router.for_family("volatility_market_regimes"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("volatility_market_regimes"),
            "reference_timestamp": refs["volatility_market_regimes"],
            "clock": lambda ref=refs["volatility_market_regimes"]: ref + (5 if synthetic else 0),
        },
        "cvd_volume_orderflow": {
            "fetcher": router.for_family("cvd_volume_orderflow"),
            "requested_mode": requested_mode,
            "existing_input": previous.get("cvd_volume_orderflow"),
            "reference_timestamp": refs["cvd_volume_orderflow"],
            "clock": lambda ref=refs["cvd_volume_orderflow"]: ref + (5 if synthetic else 0),
            "data_mode": data_mode,
            "is_demo": is_demo,
        },
    }


def _latest_spot_close(prices_processing: Mapping[str, Any]) -> dict[str, Any]:
    try:
        records = prices_processing["markets"]["spot"]["timeframes"]["1m"]["records"]
    except (KeyError, TypeError) as exc:
        raise ValueError("prices_ohlcv spot 1m records are required for cross-family current_price") from exc
    if not isinstance(records, list) or not records:
        raise ValueError("prices_ohlcv spot 1m records are empty")
    record = max(records, key=lambda item: int(item["timestamp"]))
    value, timestamp = record.get("close"), record.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("prices_ohlcv spot close is invalid")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise ValueError("prices_ohlcv spot timestamp is invalid")
    return {"value": float(value), "timestamp": timestamp}


def build_liquidations_reference_price_context(
    prices_processing: Mapping[str, Any], *, target_timestamp: int,
    synthetic_replay_alignment: bool,
) -> dict[str, Any]:
    """Build the only allowed cross-family price dependency for Liquidations.

    The production semantic source is Prices/Spot/1m/close.  The frozen synthetic
    fixture sets were created at different epochs, so a full synthetic replay may
    align the observation timestamp to the target family's reference while keeping
    the original fixture timestamp explicitly in provenance.  Live operation must
    never rebase timestamps.
    """
    latest = _latest_spot_close(prices_processing)
    timestamp = int(target_timestamp) if synthetic_replay_alignment else int(latest["timestamp"])
    context = {
        "source_family": "prices_ohlcv",
        "source_market": "spot",
        "source_timeframe": "1m",
        "price_field": "close",
        "is_closed_bar": True,
        "value": latest["value"],
        "timestamp": timestamp,
    }
    if synthetic_replay_alignment:
        context.update({
            "source_fixture_timestamp": int(latest["timestamp"]),
            "timestamp_alignment": "synthetic_fixture_rebased_to_target_reference",
        })
    return context


def _builder_arguments(processing: Mapping[str, Mapping[str, Any]], *, data_mode: str, is_demo: bool) -> dict[str, dict[str, Any]]:
    """Build Contract-Builder arguments only for families present in this run.

    This keeps Main genuinely family-isolatable; a Prices- or Volatility-only audit
    must not require unrelated Processing contracts.
    """
    result: dict[str, dict[str, Any]] = {}
    if "prices_ohlcv" in processing:
        result["prices_ohlcv"] = {"cvd_processing_context": processing.get("cvd_volume_orderflow")}
    if "etf_exchange_flows" in processing:
        result["etf_exchange_flows"] = {
            "selected_range": "30d",
            "generated_at": _iso(int(processing["etf_exchange_flows"]["data_as_of"])),
        }
    if "liquidity_microstructure" in processing:
        liquidity = processing["liquidity_microstructure"]
        result["liquidity_microstructure"] = {"runtime_context": {
            "data_mode": data_mode, "is_demo": is_demo,
            "generated_at": _iso(int(liquidity["execution_timestamp"])),
            "updated_at": _iso(int(liquidity["reference_timestamp"])),
            "connection_status": "not_reported", "cache_status": "not_reported",
            "latency_ms": None, "refresh_interval_seconds": None, "cache_ttl_seconds": None,
        }}
    if "long_short_liquidations" in processing:
        liquidations = processing["long_short_liquidations"]
        result["long_short_liquidations"] = {
            "context": {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT",
                        "market": "futures", "price_precision": 2},
            "runtime_context": {"generated_at": int(liquidations["reference_timestamp"]) + 10,
                                "updated_at": int(liquidations["reference_timestamp"]),
                                "data_mode": data_mode, "is_demo": is_demo, "cache_status": "disabled"},
        }
    if "open_interest_and_funding" in processing:
        result["open_interest_and_funding"] = {"selected_timeframe": "1h"}
    if "volatility_market_regimes" in processing:
        volatility = processing["volatility_market_regimes"]
        result["volatility_market_regimes"] = {
            "runtime_context": {
                "data_mode": data_mode, "is_demo": is_demo,
                "generated_at": _iso(int(volatility["context"]["input_execution_timestamp"])),
                "updated_at": _iso(int(volatility["context"]["reference_timestamp"])),
            },
            "selected_range": "30d",
        }
    if "cvd_volume_orderflow" in processing:
        result["cvd_volume_orderflow"] = {"selected_market": "spot", "selected_timeframe": "15m"}
    return result


def run_all(
    *, source: str = "emulator", input_raw_root: str | Path | None = None,
    enabled_families: Sequence[str] = FAMILY_ORDER, reference_timestamp: int | None = None,
    requested_mode: str = "bootstrap", existing_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    acquisition_manager: AcquisitionManager | None = None,
    overlay_root: str | Path | None = None,
    dirty_timeframes: Mapping[str, Sequence[str]] | None = None,
    existing_processing: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Execute all configured stages using either fixture emulator or live APIs."""
    families = tuple(enabled_families)
    unknown = [family for family in families if family not in FAMILY_ORDER]
    if unknown:
        raise ValueError(f"unsupported families: {unknown}")

    normalized_source = str(source).strip().lower()
    synthetic = normalized_source == "emulator"
    router = build_provider_router(normalized_source, input_raw_root=input_raw_root, overlay_root=overlay_root)
    if acquisition_manager is not None:
        router = acquisition_manager.wrap_router(router)
    all_arguments = build_input_arguments(
        router, source=normalized_source, reference_timestamp=reference_timestamp,
        requested_mode=requested_mode, existing_inputs=existing_inputs,
    )
    inputs = run_input_pipeline(
        enabled_families=families,
        family_arguments={family: all_arguments[family] for family in families},
    )

    processing: dict[str, Any] = {}
    now_timestamp = (
        SYNTHETIC_REFERENCE_TIMESTAMPS["prices_ohlcv"] + 5
        if synthetic
        else int(reference_timestamp or datetime.now(tz=UTC).timestamp())
    )
    if "cvd_volume_orderflow" in families:
        prices_input = inputs.get("prices_ohlcv") or (existing_inputs or {}).get("prices_ohlcv")
        price_history_by_market_timeframe = {}
        if isinstance(prices_input, Mapping):
            for market in ("spot", "futures"):
                tf_map = prices_input.get("markets", {}).get(market, {}).get("timeframes", {})
                if isinstance(tf_map, Mapping):
                    price_history_by_market_timeframe[market] = {
                        tf: block.get("records", []) for tf, block in tf_map.items() if isinstance(block, Mapping)
                    }
        processing.update(run_processing_pipeline(
            input_contracts=inputs,
            enabled_families=("cvd_volume_orderflow",),
            now_timestamp=now_timestamp,
            family_arguments={"cvd_volume_orderflow": {
                "clock": lambda ref=now_timestamp: ref,
                "price_history_by_market_timeframe": price_history_by_market_timeframe,
            }},
            existing_processing=existing_processing,
        ))

    if "prices_ohlcv" in families:
        processing.update(run_processing_pipeline(
            input_contracts=inputs,
            enabled_families=("prices_ohlcv",),
            now_timestamp=now_timestamp,
            family_arguments={"prices_ohlcv": {
                "dirty_timeframes": list((dirty_timeframes or {}).get("prices_ohlcv", ())),
                "cvd_processing_context": processing.get("cvd_volume_orderflow") or (existing_processing or {}).get("cvd_volume_orderflow"),
            }},
            existing_processing=existing_processing,
        ))

    remaining = tuple(family for family in families if family not in {"prices_ohlcv", "cvd_volume_orderflow"})
    processing_arguments: dict[str, dict[str, Any]] = {}
    prices_input = inputs.get("prices_ohlcv") or (existing_inputs or {}).get("prices_ohlcv")
    if "open_interest_and_funding" in remaining and isinstance(prices_input, Mapping):
        spot_tfs = prices_input.get("markets", {}).get("spot", {}).get("timeframes", {})
        processing_arguments["open_interest_and_funding"] = {
            "price_history_by_timeframe": {tf: block.get("records", []) for tf, block in spot_tfs.items() if isinstance(block, Mapping)}
        }
    if "etf_exchange_flows" in remaining and isinstance(prices_input, Mapping):
        daily = prices_input.get("markets", {}).get("spot", {}).get("timeframes", {}).get("1d", {})
        processing_arguments["etf_exchange_flows"] = {"price_history_daily": daily.get("records", []) if isinstance(daily, Mapping) else []}
    if "long_short_liquidations" in remaining:
        prices_context = processing.get("prices_ohlcv") or (existing_processing or {}).get("prices_ohlcv")
        if not isinstance(prices_context, Mapping):
            raise ValueError("long_short_liquidations requires current or persisted prices_ohlcv Processing context")
        target_timestamp = int(inputs["long_short_liquidations"]["reference_timestamp"])
        price_history = []
        if isinstance(prices_input, Mapping):
            block = prices_input.get("markets", {}).get("spot", {}).get("timeframes", {}).get("1h", {})
            if isinstance(block, Mapping):
                price_history = block.get("records", [])
        vol_input = inputs.get("volatility_market_regimes") or (existing_inputs or {}).get("volatility_market_regimes")
        positioning_history = []
        if isinstance(vol_input, Mapping):
            positioning_history = (
                vol_input.get("providers", {}).get("coinglass", {}).get("top_position_ratio", {}).get("records", [])
                or vol_input.get("top_position_ratio", {}).get("records", [])
                or []
            )
        processing_arguments["long_short_liquidations"] = {
            "reference_price_context": build_liquidations_reference_price_context(
                prices_context,
                target_timestamp=target_timestamp,
                synthetic_replay_alignment=synthetic,
            ),
            "price_history": price_history,
            "positioning_history": positioning_history,
        }
    if "liquidity_microstructure" in remaining:
        prices_input = inputs.get("prices_ohlcv") or (existing_inputs or {}).get("prices_ohlcv")
        if not isinstance(prices_input, Mapping):
            raise ValueError("liquidity_microstructure requires normalized prices_ohlcv Input context")
        processing_arguments["liquidity_microstructure"] = {"prices_input_context": prices_input}
    if remaining:
        remaining_now = max(SYNTHETIC_REFERENCE_TIMESTAMPS.values()) + 5 if synthetic else now_timestamp
        processing.update(run_processing_pipeline(
            input_contracts=inputs,
            enabled_families=remaining,
            now_timestamp=remaining_now,
            family_arguments=processing_arguments,
            existing_processing=existing_processing,
        ))

    processing = {family: processing[family] for family in families}

    classification_arguments: dict[str, dict[str, Any]] = {}
    if "cvd_volume_orderflow" in processing:
        cvd_reference = int(processing["cvd_volume_orderflow"]["context"]["reference_timestamp"])
        classification_arguments["cvd_volume_orderflow"] = {"clock": lambda ref=cvd_reference: ref}
    classification = run_classification_pipeline(
        processing_contracts=processing,
        enabled_families=families,
        family_arguments=classification_arguments,
    )
    data_mode = "synthetic" if synthetic else "live"
    contracts = run_contract_builder_pipeline(
        processing_contracts=processing,
        classification_contracts=classification,
        enabled_families=families,
        family_arguments=_builder_arguments(processing, data_mode=data_mode, is_demo=synthetic),
    )
    result = {
        "input": inputs,
        "processing": processing,
        "classification": classification,
        "hmi_contract": contracts,
    }
    _strict_json(result)
    return result


def _contract_quality(contract: Mapping[str, Any]) -> str:
    quality = contract.get("quality")
    if isinstance(quality, Mapping) and isinstance(quality.get("status"), str):
        return str(quality["status"])
    return "unknown"


def export_all_runtime_json(
    runtime_output: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *, contracts_root: str | Path | None = None, source: str = "emulator",
    mode: str = "bootstrap", acquisition: Mapping[str, Any] | None = None,
    contract_validation: Mapping[str, Any] | None = None,
    dirty_execution: Mapping[str, Any] | None = None,
    reused_families: Sequence[str] = (),
    reused_quality: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one JSON per family/stage and a compact run manifest."""
    root = Path(contracts_root) if contracts_root is not None else _repo_root() / "runtime" / "contracts"
    stage_dirs = {
        "input": root / "input",
        "processing": root / "processing",
        "classification": root / "classification",
        "hmi_contract": root / "hmi_contract",
    }
    for directory in stage_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, str]] = {stage: {} for stage in stage_dirs}
    for family in FAMILY_ORDER:
        if family not in runtime_output.get("input", {}):
            continue
        for stage in ("input", "processing", "classification"):
            path = stage_dirs[stage] / f"{family}.json"
            _atomic_write_json(path, runtime_output[stage][family])
            written[stage][family] = str(path.relative_to(root))
        screen_name = SCREEN_FILENAMES[family]
        screen_path = stage_dirs["hmi_contract"] / screen_name
        _atomic_write_json(screen_path, runtime_output["hmi_contract"][family])
        written["hmi_contract"][family] = str(screen_path.relative_to(root))
    for family in reused_families:
        if family in written["hmi_contract"]:
            continue
        for stage in ("input", "processing", "classification"):
            path = stage_dirs[stage] / f"{family}.json"
            if path.is_file():
                written[stage][family] = str(path.relative_to(root))
        path = stage_dirs["hmi_contract"] / SCREEN_FILENAMES[family]
        if path.is_file():
            written["hmi_contract"][family] = str(path.relative_to(root))

    manifest = {
        "schema": {"id": "trad_elatin.runtime.run_manifest.v1", "version": "1.0.0"},
        "mode": mode,
        "data_mode": "synthetic" if source == "emulator" else "live",
        "data_source": source,
        "families": [family for family in FAMILY_ORDER if family in runtime_output.get("input", {}) or family in reused_families],
        "paths": written,
        "quality": {
            family: {
                "input": (
                    runtime_output["input"][family].get("quality", {}).get("status")
                    or ("ok" if family == "prices_ohlcv" and all(
                        runtime_output["input"][family].get("quality", {}).get(market) == "ok"
                        for market in ("spot", "futures")
                    ) else None)
                ),
                "processing": runtime_output["processing"][family].get("quality", {}).get("status"),
                "classification": runtime_output["classification"][family].get("quality", {}).get("status"),
                "hmi_contract": _contract_quality(runtime_output["hmi_contract"][family]),
            }
            for family in FAMILY_ORDER if family in runtime_output.get("input", {})
        },
        "notes": {
            "input_raw": "Provider-shaped fixture files remain under runtime/contracts/input_raw and are not rewritten by Main.",
            "liquidations_current_price": "prices_ohlcv/spot/1m/close",
            "timestamp_alignment": (
                "Liquidations price context is rebased only during emulator replay because frozen fixture families were generated at different epochs."
                if source == "emulator" else "Live timestamps are never rebased."
            ),
        },
    }
    if reused_quality:
        manifest["quality"].update(dict(reused_quality))
    if acquisition is not None:
        manifest["acquisition"] = dict(acquisition)
    if contract_validation is not None:
        manifest["contract_validation"] = dict(contract_validation)
    if dirty_execution is not None:
        manifest["dirty_execution"] = dict(dirty_execution)
    manifest_path = root / "run_manifest.json"
    _atomic_write_json(manifest_path, manifest)
    return {"root": str(root), "written": written, "manifest": str(manifest_path), "quality": manifest["quality"]}


def run_and_export_all(
    *, source: str = "emulator", input_raw_root: str | Path | None = None,
    contracts_root: str | Path | None = None,
    reference_timestamp: int | None = None,
    state_root: str | Path | None = None,
    golden_root: str | Path | None = None,
    overlay_root: str | Path | None = None,
    fail_before_publication: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(contracts_root) if contracts_root is not None else _repo_root() / "runtime" / "contracts"
    raw_root = Path(input_raw_root) if input_raw_root is not None else root / "input_raw"
    persistent_root = Path(state_root) if state_root is not None else root.parent / "state"
    runtime_overlay_root = Path(overlay_root) if overlay_root is not None else persistent_root / "emulator_overlay"
    manager = AcquisitionManager(persistent_root, input_raw_root=raw_root)
    try:
        existing_inputs: dict[str, Any] = {}
        previous_processing: dict[str, Any] = {}
        if not manager.cold_start:
            for family in FAMILY_ORDER:
                path = root / "input" / f"{family}.json"
                if not path.is_file():
                    raise RuntimeError(f"warm start preregistration is incomplete: {path}")
                existing_inputs[family] = json.loads(path.read_text(encoding="utf-8"))
                processing_path = root / "processing" / f"{family}.json"
                if not processing_path.is_file():
                    raise RuntimeError(f"warm start Processing state is incomplete: {processing_path}")
                previous_processing[family] = json.loads(processing_path.read_text(encoding="utf-8"))
        references = Path(golden_root) if golden_root is not None else _repo_root() / "runtime" / "contracts" / "hmi"
        overlay = EmulatorOverlay(runtime_overlay_root) if source == "emulator" else None
        pending_before = manager.pending_dirty_windows()
        changed = overlay is not None and manager.overlay_changed(overlay.version)
        if not manager.cold_start and not changed and not pending_before:
            output = {stage: {} for stage in ("input", "processing", "classification", "hmi_contract")}
            for family in FAMILY_ORDER:
                for stage in ("input", "processing", "classification"):
                    output[stage][family] = json.loads((root / stage / f"{family}.json").read_text(encoding="utf-8"))
                output["hmi_contract"][family] = json.loads((root / "hmi_contract" / SCREEN_FILENAMES[family]).read_text(encoding="utf-8"))
            validation = validate_contracts_against_golden(output["hmi_contract"], golden_root=references)
            acquisition = manager.summary()
            previous_manifest_path = root / "run_manifest.json"
            previous_quality = (json.loads(previous_manifest_path.read_text(encoding="utf-8")).get("quality", {})
                                if previous_manifest_path.is_file() else {})
            dirty_execution = {
                "families": [], "source_timeframes": [], "derived_timeframes": [], "windows": [],
                "processing": {"families_executed": [], "records_recomputed": 0, "indicator_points_recomputed": 0},
                "classification": {"families_executed": []},
                "contracts_rebuilt": [], "contracts_reused": [SCREEN_FILENAMES[family] for family in FAMILY_ORDER],
                "rebuilt_count": 0, "validator": {"validated_count": 8, "reused_count": 8},
            }
            publication = export_all_runtime_json(
                {stage: {} for stage in output}, contracts_root=root, source=source, mode="incremental",
                acquisition=acquisition, contract_validation=validation, dirty_execution=dirty_execution,
                reused_families=FAMILY_ORDER,
                reused_quality=previous_quality,
            )
            return output, publication
        affected = tuple(family for family in FAMILY_ORDER if manager.cold_start or overlay is None or family in overlay.families)
        if not affected:
            affected = FAMILY_ORDER
        effective_reference = reference_timestamp
        if overlay is not None and overlay.maximum_timestamp is not None:
            effective_reference = max(int(reference_timestamp or 0), overlay.maximum_timestamp + 60)
        output = run_all(
            source=source, input_raw_root=raw_root, reference_timestamp=effective_reference,
            requested_mode=manager.mode, existing_inputs=existing_inputs,
            acquisition_manager=manager, enabled_families=affected, overlay_root=runtime_overlay_root,
            dirty_timeframes={family: overlay.source_timeframes for family in affected} if overlay is not None else {},
            existing_processing=previous_processing,
        )
        pending = manager.pending_dirty_windows()
        windows = plan_dirty_windows(pending, reference_timestamp=int(effective_reference or max(SYNTHETIC_REFERENCE_TIMESTAMPS.values())))
        plan = recompute_plan(windows)
        combined = dict(output["hmi_contract"])
        reused = tuple(family for family in FAMILY_ORDER if family not in affected)
        for family in reused:
            combined[family] = json.loads((root / "hmi_contract" / SCREEN_FILENAMES[family]).read_text(encoding="utf-8"))
        validation = validate_contracts_against_golden(combined, golden_root=references)
        if validation["status"] != "passed":
            raise RuntimeError(f"VR1 contract validation failed: {validation}")
        if fail_before_publication:
            raise RuntimeError("controlled failure before publication")
        manager.mark_complete()
        acquisition = manager.summary()
        actual_recomputed = sum(
            int(contract.get("incremental_execution", {}).get("indicator_records_recomputed", 0))
            for contract in output["processing"].values()
        )
        dirty_execution = {
            "families": list(affected),
            "source_timeframes": sorted({item.source_timeframe for item in windows}),
            "derived_timeframes": sorted({item.target_timeframe for item in windows if item.target_timeframe != item.source_timeframe}),
            "windows": [item.as_dict() for item in windows],
            "processing": {"families_executed": list(affected), "records_recomputed": actual_recomputed,
                           "indicator_points_recomputed": actual_recomputed,
                           "required_lookback_points": plan["required_lookback_points"]},
            "classification": {"families_executed": list(affected)},
            "contracts_rebuilt": [SCREEN_FILENAMES[family] for family in affected],
            "contracts_reused": [SCREEN_FILENAMES[family] for family in reused],
            "rebuilt_count": len(affected), "validator": {"validated_count": 8, "reused_count": len(reused)},
        }
        previous_quality = (json.loads((root / "run_manifest.json").read_text(encoding="utf-8")).get("quality", {})
                            if (root / "run_manifest.json").is_file() else {})
        publication = export_all_runtime_json(
            output, contracts_root=root, source=source, mode=manager.mode,
            acquisition=acquisition, contract_validation=validation, dirty_execution=dirty_execution,
            reused_families=reused,
            reused_quality={family: previous_quality[family] for family in reused if family in previous_quality},
        )
        manager.mark_dirty_clean([row["id"] for row in pending])
        if overlay is not None:
            manager.mark_overlay_processed(overlay.version)
        return output, publication
    finally:
        manager.close()
