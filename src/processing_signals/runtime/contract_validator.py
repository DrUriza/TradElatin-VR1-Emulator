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
    contracts: Mapping[str, Mapping[str, Any]], *, golden_root: str | Path,
) -> dict[str, Any]:
    root = Path(golden_root)
    results = {}
    for family, filename in GOLDEN_FILENAMES.items():
        errors: list[str] = []
        generated = contracts.get(family)
        if not isinstance(generated, Mapping):
            errors.append("runtime contract missing")
            results[family] = {"status": "failed", "score_percent": 0.0, "errors": errors}
            continue
        reference = json.loads((root / filename).read_text(encoding="utf-8"))
        if _identity(generated) != _identity(reference):
            errors.append(f"identity mismatch: {_identity(generated)!r} != {_identity(reference)!r}")
        missing = set(reference) - set(generated)
        if missing:
            errors.append(f"missing top-level keys: {sorted(missing)}")
        for key in set(reference) & set(generated):
            if reference[key] is not None and not isinstance(generated[key], type(reference[key])):
                errors.append(f"$.{key}: top-level type mismatch")
        _semantic_checks(family, generated, errors)
        results[family] = {
            "status": "passed" if not errors else "failed",
            "score_percent": 100.0 if not errors else 0.0,
            "errors": errors[:50],
        }
    passed = sum(result["status"] == "passed" for result in results.values())
    return {"status": "passed" if passed == len(GOLDEN_FILENAMES) else "failed",
            "passed": passed, "total": len(GOLDEN_FILENAMES), "families": results}
