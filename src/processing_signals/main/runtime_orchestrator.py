"""Top-level TradELATIN runtime orchestration for all eight frozen families.

The orchestrator owns cross-family dependencies and filesystem publication.
It does not calculate indicators and it does not reinterpret provider payloads.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
import json
import math
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


def _validate_json_tree(value: Any) -> None:
    """Validate JSON finiteness/types without serializing the whole runtime twice.

    ``run_all`` can hold hundreds of MB of order-book history across Input,
    Processing and Classification.  Building an indented JSON string only to
    discard it made cold bootstrap unnecessarily slow.  Atomic publication
    still uses ``_strict_json`` per artifact; this pass only rejects NaN/Inf or
    non-JSON values before publication.
    """
    stack = [("root", value)]
    while stack:
        path, item = stack.pop()
        if item is None or isinstance(item, (str, int, bool)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"non_finite_json_number:{path}")
            continue
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, (str, int, float, bool)) and key is not None:
                    raise TypeError(f"non_json_mapping_key:{path}:{type(key).__name__}")
                stack.append((f"{path}.{key}", child))
            continue
        if isinstance(item, (list, tuple)):
            stack.extend((f"{path}[{index}]", child) for index, child in enumerate(item))
            continue
        raise TypeError(f"non_json_value:{path}:{type(item).__name__}")


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
            # Confirmations are worth paying during cold bootstrap, not on every
            # one-minute runtime cycle.  ETF Input has its own hourly/daily TTLs.
            # ETF confirmations duplicate canonical CoinGlass/CryptoQuant
            # primitives and are not required by the final HMI contract.
            "include_secondary": False,
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
            # Liquidity uses native 1m for the live screen and a 1h bootstrap
            # seed for the 730-hour analytical window.  5m/15m and depth 1%/5%
            # are derived/obsolete for the final VR1 contract and are not paid.
            "history_limit": 240,
            "hourly_history_limit": 1000,
            "footprint_limit": 240,
        },
        "long_short_liquidations": {
            "fetcher": router.for_family("long_short_liquidations"),
            "requested_mode": requested_mode,
            "existing_contract": previous.get("long_short_liquidations"),
            "reference_timestamp": refs["long_short_liquidations"],
            "clock": lambda ref=refs["long_short_liquidations"]: ref + (5 if synthetic else 0),
            "exchange_pairs": pairs,
            "history_hours": 730,
            # Liquidation provider confirmations are optional diagnostics and
            # no longer part of the paid default acquisition path.
            "include_confirmations": False,
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
            # CoinGlass CVD/Footprint are canonical. Duplicate CQ/GN
            # confirmations are removed from the default paid path.
            "include_cryptoquant_confirmation": False,
            "include_glassnode_confirmation": False,
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
    liquidations_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only allowed cross-family price dependency for Liquidations.

    The production semantic source is Prices/Spot/1m/close.  The frozen synthetic
    fixture sets were created at different epochs, so a full synthetic replay may
    align the observation timestamp to the target family's reference while keeping
    the original fixture timestamp explicitly in provenance.  Live operation must
    never rebase timestamps.
    """
    latest = _latest_spot_close(prices_processing)
    if synthetic_replay_alignment and isinstance(liquidations_input, Mapping):
        cg = liquidations_input.get("providers", {}).get("coinglass", {})
        max_pain = cg.get("max_pain", {}) if isinstance(cg, Mapping) else {}
        records = max_pain.get("records", []) if isinstance(max_pain, Mapping) else []
        btc = next((row for row in records if isinstance(row, Mapping) and row.get("symbol") == "BTC"
                    and isinstance(row.get("provider_price"), (int, float)) and not isinstance(row.get("provider_price"), bool)
                    and float(row.get("provider_price")) > 0), None)
        map_snapshot = cg.get("aggregated_map", {}).get("snapshot_observed_at") if isinstance(cg, Mapping) else None
        if btc is not None and type(map_snapshot) is int:
            return {
                "source_family": "long_short_liquidations",
                "source_market": "futures",
                "source_timeframe": "snapshot",
                "price_field": "provider_price",
                "is_closed_bar": True,
                "value": float(btc["provider_price"]),
                "timestamp": int(map_snapshot),
                "provider": "coinglass",
                "source_dataset": "coinglass.max_pain.provider_price",
                "synthetic_fixture_alignment": True,
                "source_fixture_timestamp": int(map_snapshot),
            }
    timestamp = int(latest["timestamp"])
    context = {
        "source_family": "prices_ohlcv",
        "source_market": "spot",
        "source_timeframe": "1m",
        "price_field": "close",
        "is_closed_bar": True,
        "value": latest["value"],
        "timestamp": timestamp,
    }
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
    progress_root: str | Path | None = None,
    prebuilt_inputs: Mapping[str, Mapping[str, Any]] | None = None,
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
    progress = Path(progress_root) if progress_root is not None else None

    # Input contracts are produced by Main; they are not preregistration files.
    # Persist each family as soon as its Input stage completes so a clean runtime
    # tree is populated progressively during execution.
    inputs: dict[str, Any] = {
        family: deepcopy(value)
        for family, value in (prebuilt_inputs or {}).items()
        if family in families
    }
    for family in families:
        if family not in inputs:
            family_output = run_input_pipeline(
                enabled_families=(family,),
                family_arguments={family: all_arguments[family]},
            )
            inputs[family] = family_output[family]
        if progress is not None:
            _atomic_write_json(progress / "input" / f"{family}.json", inputs[family])

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
        if progress is not None:
            _atomic_write_json(progress / "processing" / "cvd_volume_orderflow.json", processing["cvd_volume_orderflow"])

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
        if progress is not None:
            _atomic_write_json(progress / "processing" / "prices_ohlcv.json", processing["prices_ohlcv"])

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
    if "volatility_market_regimes" in remaining and isinstance(prices_input, Mapping):
        daily = prices_input.get("markets", {}).get("spot", {}).get("timeframes", {}).get("1d", {})
        processing_arguments["volatility_market_regimes"] = {"price_history_daily": daily.get("records", []) if isinstance(daily, Mapping) else []}
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
        processing_arguments["long_short_liquidations"] = {
            "reference_price_context": build_liquidations_reference_price_context(
                prices_context,
                target_timestamp=target_timestamp,
                synthetic_replay_alignment=synthetic,
                liquidations_input=inputs.get("long_short_liquidations"),
            ),
            "price_history": price_history,
        }
    if "liquidity_microstructure" in remaining:
        prices_input = inputs.get("prices_ohlcv") or (existing_inputs or {}).get("prices_ohlcv")
        if not isinstance(prices_input, Mapping):
            raise ValueError("liquidity_microstructure requires normalized prices_ohlcv Input context")
        cvd_processing = processing.get("cvd_volume_orderflow") or (existing_processing or {}).get("cvd_volume_orderflow")
        processing_arguments["liquidity_microstructure"] = {
            "prices_input_context": prices_input,
            # Deep executed-flow history is already computed by CVD.  Liquidity
            # consumes it instead of paying a second 730-hour footprint history.
            "cvd_processing_context": cvd_processing if isinstance(cvd_processing, Mapping) else None,
        }
    if remaining:
        remaining_now = max(SYNTHETIC_REFERENCE_TIMESTAMPS.values()) + 5 if synthetic else now_timestamp
        for family in remaining:
            family_processing = run_processing_pipeline(
                input_contracts=inputs,
                enabled_families=(family,),
                now_timestamp=remaining_now,
                family_arguments={family: processing_arguments.get(family, {})},
                existing_processing=existing_processing,
            )
            processing[family] = family_processing[family]
            if progress is not None:
                _atomic_write_json(progress / "processing" / f"{family}.json", processing[family])

    processing = {family: processing[family] for family in families}

    classification_arguments: dict[str, dict[str, Any]] = {}
    if "cvd_volume_orderflow" in processing:
        cvd_reference = int(processing["cvd_volume_orderflow"]["context"]["reference_timestamp"])
        classification_arguments["cvd_volume_orderflow"] = {"clock": lambda ref=cvd_reference: ref}
    classification: dict[str, Any] = {}
    for family in families:
        family_classification = run_classification_pipeline(
            processing_contracts=processing,
            enabled_families=(family,),
            family_arguments={family: classification_arguments.get(family, {})},
        )
        classification[family] = family_classification[family]
        if progress is not None:
            _atomic_write_json(progress / "classification" / f"{family}.json", classification[family])
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
        "hmi": contracts,
    }
    # Per-artifact atomic publication performs strict JSON serialization with
    # allow_nan=False. Avoid a second full-tree validation pass here: the runtime
    # contains large order-book histories and validating the entire duplicated
    # Input/Processing/Classification/HMI tree before export is prohibitively
    # expensive and redundant.
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
        "hmi": root / "hmi",
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
            written[stage][family] = path.relative_to(root).as_posix()
        screen_name = SCREEN_FILENAMES[family]
        screen_path = stage_dirs["hmi"] / screen_name
        _atomic_write_json(screen_path, runtime_output["hmi"][family])
        written["hmi"][family] = screen_path.relative_to(root).as_posix()
    for family in reused_families:
        if family in written["hmi"]:
            continue
        for stage in ("input", "processing", "classification"):
            path = stage_dirs[stage] / f"{family}.json"
            if path.is_file():
                written[stage][family] = path.relative_to(root).as_posix()
        path = stage_dirs["hmi"] / SCREEN_FILENAMES[family]
        if path.is_file():
            written["hmi"][family] = path.relative_to(root).as_posix()

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
                "hmi": _contract_quality(runtime_output["hmi"][family]),
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



def poll_and_export_families(
    *, families: Sequence[str], source: str = "emulator",
    input_raw_root: str | Path | None = None, contracts_root: str | Path | None = None,
    reference_timestamp: int | None = None, state_root: str | Path | None = None,
    golden_root: str | Path | None = None, overlay_root: str | Path | None = None,
    force_recompute: Sequence[str] = (),
) -> dict[str, Any]:
    """Poll only ``families`` and rebuild only families whose inputs changed.

    Acquisition is allowed to run at a faster cadence than Processing.  A family
    reaches Processing/Classification/HMI only when Acquisition records NEW,
    REVISION or LATE_ARRIVAL data, or when a dependency change explicitly marks
    the family for recomputation via ``force_recompute``.
    """
    requested = tuple(dict.fromkeys(str(f) for f in families))
    unknown = [family for family in requested if family not in FAMILY_ORDER]
    if unknown:
        raise ValueError(f"unsupported families: {unknown}")
    if not requested:
        return {"polled_families": [], "processed_families": [], "changed_families": [], "noop": True}

    root = Path(contracts_root) if contracts_root is not None else _repo_root() / "runtime" / "contracts"
    raw_root = Path(input_raw_root) if input_raw_root is not None else root / "input_raw"
    persistent_root = Path(state_root) if state_root is not None else root.parent / "state"
    runtime_overlay_root = Path(overlay_root) if overlay_root is not None else persistent_root / "emulator_overlay"

    # Selective refresh assumes the initial eight-family bootstrap has already
    # published a coherent baseline.  If not, let the normal bootstrap create it.
    missing_baseline = [
        family for family in FAMILY_ORDER
        if not (root / "input" / f"{family}.json").is_file()
        or not (root / "processing" / f"{family}.json").is_file()
        or not (root / "classification" / f"{family}.json").is_file()
        or not (root / "hmi" / SCREEN_FILENAMES[family]).is_file()
    ]
    if missing_baseline:
        output, publication = run_and_export_all(
            source=source, input_raw_root=raw_root, contracts_root=root,
            reference_timestamp=reference_timestamp, state_root=persistent_root,
            golden_root=golden_root, overlay_root=runtime_overlay_root,
        )
        return {
            "polled_families": list(FAMILY_ORDER),
            "changed_families": list(FAMILY_ORDER),
            "processed_families": list(FAMILY_ORDER),
            "noop": False,
            "bootstrap": True,
            "publication": publication,
        }

    existing_inputs = {
        family: json.loads((root / "input" / f"{family}.json").read_text(encoding="utf-8"))
        for family in FAMILY_ORDER
    }
    existing_processing = {
        family: json.loads((root / "processing" / f"{family}.json").read_text(encoding="utf-8"))
        for family in FAMILY_ORDER
    }
    previous_quality = {}
    manifest_path = root / "run_manifest.json"
    if manifest_path.is_file():
        previous_quality = json.loads(manifest_path.read_text(encoding="utf-8")).get("quality", {})

    manager = AcquisitionManager(persistent_root, input_raw_root=raw_root)
    try:
        normalized_source = str(source).strip().lower()
        router = build_provider_router(normalized_source, input_raw_root=raw_root, overlay_root=runtime_overlay_root)
        router = manager.wrap_router(router)
        input_arguments = build_input_arguments(
            router, source=normalized_source, reference_timestamp=reference_timestamp,
            requested_mode=manager.mode, existing_inputs=existing_inputs,
        )

        refreshed_inputs: dict[str, Any] = {}
        for family in requested:
            family_output = run_input_pipeline(
                enabled_families=(family,),
                family_arguments={family: input_arguments[family]},
            )
            refreshed_inputs[family] = family_output[family]

        changed: list[str] = []
        changed_by_raw: set[str] = set()
        forced = set(force_recompute)
        for family in requested:
            metric = manager.metrics.get(family)
            changed_records = 0 if metric is None else (
                int(metric.records_new) + int(metric.records_revised) + int(metric.late_arrivals)
            )
            if changed_records > 0:
                changed_by_raw.add(family)
            if changed_records > 0 or family in forced:
                changed.append(family)

        if not changed:
            manager.mark_complete()
            return {
                "polled_families": list(requested),
                "changed_families": [],
                "processed_families": [],
                "noop": True,
                "acquisition": manager.summary(),
            }

        # Persist Input only when Acquisition actually changed that family's
        # source records. Dependency-only recomputes reuse the last canonical
        # Input contract and rebuild Processing/Classification/HMI from the
        # newer dependency state. This prevents timer polls from rewriting
        # stage JSONs when nothing changed.
        selected_inputs: dict[str, Any] = {}
        for family in changed:
            if family in changed_by_raw:
                selected_inputs[family] = refreshed_inputs[family]
                _atomic_write_json(root / "input" / f"{family}.json", refreshed_inputs[family])
            else:
                selected_inputs[family] = existing_inputs[family]

        all_inputs = dict(existing_inputs)
        all_inputs.update(selected_inputs)
        overlay = EmulatorOverlay(runtime_overlay_root) if normalized_source == "emulator" else None
        effective_reference = reference_timestamp
        if overlay is not None and overlay.maximum_timestamp is not None:
            effective_reference = max(int(reference_timestamp or 0), overlay.maximum_timestamp + 60)

        output = run_all(
            source=normalized_source,
            input_raw_root=raw_root,
            reference_timestamp=effective_reference,
            requested_mode=manager.mode,
            existing_inputs=all_inputs,
            acquisition_manager=manager,
            enabled_families=tuple(changed),
            overlay_root=runtime_overlay_root,
            existing_processing=existing_processing,
            progress_root=root,
            prebuilt_inputs={family: selected_inputs[family] for family in changed},
        )

        combined = dict(output["hmi"])
        reused = tuple(family for family in FAMILY_ORDER if family not in changed)
        for family in reused:
            combined[family] = json.loads((root / "hmi" / SCREEN_FILENAMES[family]).read_text(encoding="utf-8"))
        validation = validate_contracts_against_golden(combined, golden_root=golden_root)
        if validation["status"] != "passed":
            raise RuntimeError(f"VR1 contract validation failed: {validation}")

        pending = manager.pending_dirty_windows()
        manager.mark_complete()
        acquisition = manager.summary()
        dirty_execution = {
            "families": list(changed),
            "source_timeframes": sorted({str(row.get("source_timeframe")) for row in pending if row.get("source_timeframe")}),
            "derived_timeframes": [],
            "windows": list(pending),
            "processing": {"families_executed": list(changed)},
            "classification": {"families_executed": list(changed)},
            "contracts_rebuilt": [SCREEN_FILENAMES[family] for family in changed],
            "contracts_reused": [SCREEN_FILENAMES[family] for family in reused],
            "rebuilt_count": len(changed),
            "validator": {"validated_count": 8, "reused_count": len(reused)},
        }
        publication = export_all_runtime_json(
            output, contracts_root=root, source=normalized_source, mode=manager.mode,
            acquisition=acquisition, contract_validation=validation,
            dirty_execution=dirty_execution, reused_families=reused,
            reused_quality={family: previous_quality[family] for family in reused if family in previous_quality},
        )
        manager.mark_dirty_clean([row["id"] for row in pending])
        if overlay is not None:
            manager.mark_overlay_processed(overlay.version)
        return {
            "polled_families": list(requested),
            "changed_families": list(changed),
            "processed_families": list(changed),
            "noop": False,
            "publication": publication,
            "acquisition": acquisition,
        }
    finally:
        manager.close()

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
        # Runtime JSON artifacts are outputs, never startup prerequisites.
        # A preserved SQLite acquisition state may outlive a deleted/cleaned
        # runtime/contracts tree.  Load persisted JSON opportunistically and
        # rebuild whatever is missing instead of aborting before the pipeline
        # has a chance to regenerate it.
        existing_inputs: dict[str, Any] = {}
        previous_processing: dict[str, Any] = {}
        for family in FAMILY_ORDER:
            input_path = root / "input" / f"{family}.json"
            if input_path.is_file():
                existing_inputs[family] = json.loads(input_path.read_text(encoding="utf-8"))
            processing_path = root / "processing" / f"{family}.json"
            if processing_path.is_file():
                previous_processing[family] = json.loads(processing_path.read_text(encoding="utf-8"))

        references = Path(golden_root) if golden_root is not None else None
        overlay = EmulatorOverlay(runtime_overlay_root) if source == "emulator" else None
        pending_before = manager.pending_dirty_windows()
        changed = overlay is not None and manager.overlay_changed(overlay.version)

        missing_runtime: dict[str, list[str]] = {}
        for family in FAMILY_ORDER:
            missing_stages: list[str] = []
            if family not in existing_inputs:
                missing_stages.append("input")
            if family not in previous_processing:
                missing_stages.append("processing")
            if not (root / "classification" / f"{family}.json").is_file():
                missing_stages.append("classification")
            if not (root / "hmi" / SCREEN_FILENAMES[family]).is_file():
                missing_stages.append("hmi")
            if missing_stages:
                missing_runtime[family] = missing_stages

        # A true warm NOOP is valid only when both acquisition state and all
        # published/runtime artifacts are complete.  Missing generated JSONs
        # trigger regeneration; they never prevent startup.
        if (
            not manager.cold_start
            and not manager.raw_inventory_changed
            and not changed
            and not pending_before
            and not missing_runtime
        ):
            output = {stage: {} for stage in ("input", "processing", "classification", "hmi")}
            for family in FAMILY_ORDER:
                for stage in ("input", "processing", "classification"):
                    output[stage][family] = json.loads((root / stage / f"{family}.json").read_text(encoding="utf-8"))
                output["hmi"][family] = json.loads((root / "hmi" / SCREEN_FILENAMES[family]).read_text(encoding="utf-8"))
            validation = validate_contracts_against_golden(output["hmi"], golden_root=references)
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
        missing_families = set(missing_runtime)
        affected = tuple(
            family for family in FAMILY_ORDER
            if manager.cold_start
            or manager.raw_inventory_changed
            or family in missing_families
            or overlay is None
            or family in overlay.families
        )
        if not affected:
            affected = FAMILY_ORDER

        # If a generated Input/Processing artifact is missing, rebuild the
        # affected execution from provider-shaped RAW/API data in bootstrap
        # mode.  Existing JSONs may accelerate a normal warm cycle, but are
        # never required to start Main.
        needs_runtime_bootstrap = manager.cold_start or any(
            stage in {"input", "processing"}
            for family in affected
            for stage in missing_runtime.get(family, ())
        )
        execution_mode = "bootstrap" if needs_runtime_bootstrap else manager.mode

        effective_reference = reference_timestamp
        if overlay is not None and overlay.maximum_timestamp is not None:
            effective_reference = max(int(reference_timestamp or 0), overlay.maximum_timestamp + 60)
        output = run_all(
            source=source, input_raw_root=raw_root, reference_timestamp=effective_reference,
            requested_mode=execution_mode, existing_inputs=existing_inputs,
            acquisition_manager=manager, enabled_families=affected, overlay_root=runtime_overlay_root,
            dirty_timeframes={family: overlay.source_timeframes for family in affected} if overlay is not None else {},
            existing_processing=previous_processing,
            progress_root=root,
        )
        pending = manager.pending_dirty_windows()
        windows = plan_dirty_windows(pending, reference_timestamp=int(effective_reference or max(SYNTHETIC_REFERENCE_TIMESTAMPS.values())))
        plan = recompute_plan(windows)
        combined = dict(output["hmi"])
        reused = tuple(family for family in FAMILY_ORDER if family not in affected)
        for family in reused:
            combined[family] = json.loads((root / "hmi" / SCREEN_FILENAMES[family]).read_text(encoding="utf-8"))
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
            output, contracts_root=root, source=source, mode=execution_mode,
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
