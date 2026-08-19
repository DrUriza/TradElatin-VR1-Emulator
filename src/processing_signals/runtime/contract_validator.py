from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any


GOLDEN_FILENAMES = {
    "prices_ohlcv": "prices_VR1_FINAL.json",
    "cvd_volume_orderflow": "cvd_volume_orderflow_VR1_FINAL.json",
    "open_interest_and_funding": "open_interest_and_funding_VR1_FINAL.json",
    "etf_exchange_flows": "etf_exchange_flows_VR1_FINAL.json",
    "on_chain_miners": "on_chain_miners_VR1_FINAL.json",
    "volatility_market_regimes": "volatility_market_regimes_VR1_FINAL.json",
    "long_short_liquidations": "long_short_liquidations_VR1_FINAL.json",
    "liquidity_microstructure": "liquidity_microstructure_VR1_FINAL.json",
}

CANONICAL_TEMPLATE_PATHS = {
    "prices_ohlcv": "src/processing_signals/classification/prices_ohlcv/prices_screen_sp_v1_9.json",
    "cvd_volume_orderflow": "src/processing_signals/classification/cvd_volume_orderflow/cvd_screen_sp_v1_5.json",
    "open_interest_and_funding": "src/processing_signals/classification/open_interest_and_funding/open_interest_and_funding_screen_sp_v1_12.json",
    "etf_exchange_flows": "src/processing_signals/classification/etf_exchange_flows/etf_exchange_flows_screen_sp_v1_4.json",
    "on_chain_miners": "src/processing_signals/classification/on_chain_miners/on_chain_miners_screen_sp_v2_0.json",
    "volatility_market_regimes": "src/processing_signals/classification/volatility_market_regimes/volatility_market_regimes_screen_sp_v2_0.json",
    "long_short_liquidations": "src/processing_signals/classification/long_short_liquidations/long_short_liquidations_screen_sp_v1_3.json",
    "liquidity_microstructure": "src/processing_signals/classification/liquidity_microstructure/liquidity_microstructure_screen_sp_v1_4.json",
}

IDENTITY_KEYS = (
    "metric_id", "kpi_id", "widget_id", "chart_id", "table_id", "badge_id",
    "id", "role", "family", "group", "indicator_id", "first_series", "event_type",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_reference(family: str, golden_root: str | Path | None) -> dict[str, Any]:
    if golden_root is not None:
        path = Path(golden_root) / GOLDEN_FILENAMES[family]
    else:
        path = _repo_root() / CANONICAL_TEMPLATE_PATHS[family]
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"reference contract is not an object: {path}")
    return value


def _identity(value: Mapping[str, Any]) -> tuple[Any, ...]:
    schema = value.get("schema") if isinstance(value.get("schema"), Mapping) else {}
    screen = value.get("screen") if isinstance(value.get("screen"), Mapping) else {}
    return (
        value.get("family", screen.get("family")),
        value.get("screen_id", value.get("screen") if isinstance(value.get("screen"), str) else screen.get("id")),
        value.get("schema_version", value.get("contract_version", schema.get("version"))),
        value.get("stage"),
    )


def _walk(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        children = value[:2] + value[-1:] if len(value) > 2 else value
        for index, child in enumerate(children):
            yield from _walk(child, f"{path}[{index}]")


def _records(value: Any):
    for path, child in _walk(value):
        if isinstance(child, list) and child and all(isinstance(item, Mapping) and "timestamp" in item for item in child):
            yield path, child


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _reference_list_item(reference: list[Any], item: Any, index: int) -> Any:
    if isinstance(item, Mapping):
        for key in IDENTITY_KEYS:
            if key not in item:
                continue
            for ref_item in reference:
                if isinstance(ref_item, Mapping) and ref_item.get(key, object()) == item[key]:
                    return ref_item
    return reference[index] if index < len(reference) else reference[0]


def _structure_checks(reference: Any, candidate: Any, errors: list[str], path: str = "$") -> None:
    """Compare contract form recursively while treating runtime records/IDs as data.

    Static dictionaries must have exactly the same keys. Dynamic event IDs,
    timestamp maps and record-list lengths may differ because values are runtime
    data, but every emitted element must conform to a canonical prototype.
    """
    if isinstance(reference, Mapping):
        if not isinstance(candidate, Mapping):
            errors.append(f"{path}: expected object, got {type(candidate).__name__}")
            return
        if path == "$.events.by_id":
            prototypes = [row for row in reference.values() if isinstance(row, Mapping)]
            for uid, event in candidate.items():
                if not isinstance(event, Mapping):
                    errors.append(f"{path}.{uid}: expected object")
                    continue
                prototype = next((row for row in prototypes if row.get("event_type") == event.get("event_type") and row.get("event_group") == event.get("event_group")), None)
                prototype = prototype or next((row for row in prototypes if row.get("event_type") == event.get("event_type")), None)
                if prototype is None:
                    errors.append(f"{path}.{uid}: no canonical event prototype")
                else:
                    _structure_checks(prototype, event, errors, f"{path}.*")
            return
        if path.endswith(".by_timestamp"):
            for key, value in candidate.items():
                if not isinstance(value, list):
                    errors.append(f"{path}.{key}: expected list")
            return
        ref_keys, cand_keys = set(reference), set(candidate)
        if ref_keys != cand_keys:
            missing = sorted(ref_keys - cand_keys)
            extra = sorted(cand_keys - ref_keys)
            if missing:
                errors.append(f"{path}: missing keys {missing}")
            if extra:
                errors.append(f"{path}: extra keys {extra}")
        for key in sorted(ref_keys & cand_keys):
            _structure_checks(reference[key], candidate[key], errors, f"{path}.{key}")
        return

    if isinstance(reference, list):
        if not isinstance(candidate, list):
            errors.append(f"{path}: expected list, got {type(candidate).__name__}")
            return
        if not reference:
            return
        if path == "$.badges":
            # DEMO badge presence is data-mode dependent.
            for index, item in enumerate(candidate):
                _structure_checks(reference[0], item, errors, f"{path}[{index}]")
            return
        if all(not isinstance(item, (dict, list)) for item in reference):
            prototype = next((item for item in reference if item is not None), None)
            if prototype is None:
                return
            for index, item in enumerate(candidate):
                if _numeric(prototype) and _numeric(item):
                    continue
                if item is not None and type(item) is not type(prototype):
                    errors.append(f"{path}[{index}]: type {type(item).__name__} != {type(prototype).__name__}")
            return
        # Time-series record lists are dynamic in length/content.
        if path.endswith(".records") and all(isinstance(item, Mapping) and "timestamp" in item for item in reference if isinstance(item, Mapping)) and any(isinstance(item, Mapping) and "timestamp" in item for item in reference):
            prototypes = [item for item in reference if isinstance(item, Mapping) and "timestamp" in item]
            by_keyset = {frozenset(item): item for item in prototypes}
            for index, item in enumerate(candidate):
                if not isinstance(item, Mapping):
                    errors.append(f"{path}[{index}]: expected object record")
                    continue
                prototype = by_keyset.get(frozenset(item))
                if prototype is None:
                    # Fall back to the closest canonical record shape.
                    prototype = min(prototypes, key=lambda row: len(set(row) ^ set(item)))
                _structure_checks(prototype, item, errors, f"{path}[{index}]")
            return
        # Static semantic lists must contain the same identities.
        identity_key = next((key for key in IDENTITY_KEYS if reference and all(not isinstance(item, Mapping) or key in item for item in reference) and any(isinstance(item, Mapping) and key in item for item in reference)), None)
        if identity_key and all(isinstance(item, Mapping) for item in reference):
            ref_ids = [item.get(identity_key) for item in reference]
            cand_ids = [item.get(identity_key) for item in candidate if isinstance(item, Mapping)]
            if set(ref_ids) != set(cand_ids):
                errors.append(f"{path}: {identity_key} identities differ")
        for index, item in enumerate(candidate):
            _structure_checks(_reference_list_item(reference, item, index), item, errors, f"{path}[{index}]")
        return

    if reference is None or candidate is None:
        return
    if _numeric(reference):
        if not _numeric(candidate):
            errors.append(f"{path}: expected numeric, got {type(candidate).__name__}")
        return
    if type(reference) is not type(candidate):
        errors.append(f"{path}: type {type(candidate).__name__} != {type(reference).__name__}")


def _semantic_checks(family: str, generated: Mapping[str, Any], errors: list[str]) -> None:
    for path, records in _records(generated):
        if ".series" not in path and ".calculation_history.records" not in path and ".ohlc" not in path:
            continue
        timestamps = [record["timestamp"] for record in records]
        if timestamps != sorted(timestamps):
            errors.append(f"{path}: timestamps are not ordered")
        for record in records:
            if not {"open", "high", "low", "close"} <= set(record):
                continue
            values = [record[key] for key in ("open", "high", "low", "close")]
            if any(value is None for value in values):
                continue
            open_, high, low, close = values
            if high < max(open_, close, low) or low > min(open_, close, high):
                errors.append(f"{path}: invalid OHLC")
                break
    unavailable = {"partial", "unavailable", "invalid", "warming_up", "error"}
    walked = tuple(_walk(generated))
    for path, value in walked:
        if isinstance(value, Mapping) and "status" in value and "reason" in value:
            if value["status"] in unavailable and not (isinstance(value["reason"], str) and value["reason"]):
                errors.append(f"{path}: unavailable status requires reason")
    if any(path.endswith(".hmi_calculation") and value is True for path, value in walked):
        errors.append("HMI calculation is enabled")
    if family == "liquidity_microstructure":
        technical = generated.get("technical_analysis")
        if technical is not None and technical.get("enabled") is not False:
            errors.append("liquidity technical analysis must be disabled")
    if family in {"prices_ohlcv", "open_interest_and_funding"}:
        technical = generated.get("technical_analysis", {})
        if technical.get("canonical_location") != "technical_analysis" or technical.get("enabled") is not True:
            errors.append("canonical technical analysis contract missing")
    if family == "long_short_liquidations":
        badge = generated.get("quality", {}).get("extensions", {}).get("event_badge_contract_v1", {})
        if set(badge) != {"truncated_events", "lower_bound"}:
            errors.append("event badge contract mismatch")


def validate_contracts_against_golden(
    contracts: Mapping[str, Mapping[str, Any]], *, golden_root: str | Path | None = None,
) -> dict[str, Any]:
    results = {}
    for family in GOLDEN_FILENAMES:
        errors: list[str] = []
        generated = contracts.get(family)
        if not isinstance(generated, Mapping):
            errors.append("runtime contract missing")
            results[family] = {"status": "failed", "score_percent": 0.0, "errors": errors}
            continue
        reference = _load_reference(family, golden_root)
        if _identity(generated) != _identity(reference):
            errors.append(f"identity mismatch: {_identity(generated)!r} != {_identity(reference)!r}")
        _structure_checks(reference, generated, errors)
        _semantic_checks(family, generated, errors)
        results[family] = {
            "status": "passed" if not errors else "failed",
            "score_percent": 100.0 if not errors else 0.0,
            "errors": errors[:100],
        }
    passed = sum(result["status"] == "passed" for result in results.values())
    return {
        "status": "passed" if passed == len(GOLDEN_FILENAMES) else "failed",
        "passed": passed,
        "total": len(GOLDEN_FILENAMES),
        "families": results,
    }
