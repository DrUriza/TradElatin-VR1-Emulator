"""Static VR1 checks for the eight contracts published by Main.

These tests deliberately compare contract semantics, never runtime values.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest


ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT / "runtime" / "contracts"
REFERENCE = CONTRACTS / "hmi"
GENERATED = CONTRACTS / "hmi_contract"
FAMILIES = {
    "prices_ohlcv": ("prices_VR1_FINAL.json", "prices_screen.json"),
    "cvd_volume_orderflow": ("cvd_volume_orderflow_VR1_FINAL.json", "cvd_volume_orderflow_screen.json"),
    "open_interest_and_funding": ("open_interest_and_funding_VR1_FINAL.json", "open_interest_and_funding_screen.json"),
    "etf_exchange_flows": ("etf_exchange_flows_VR1_FINAL.json", "etf_exchange_flows_screen.json"),
    "on_chain_miners": ("on_chain_miners_VR1_FINAL.json", "on_chain_miners_screen.json"),
    "volatility_market_regimes": ("volatility_market_regimes_VR1_FINAL.json", "volatility_market_regimes_screen.json"),
    "long_short_liquidations": ("long_short_liquidations_VR1_FINAL.json", "long_short_liquidations_screen.json"),
    "liquidity_microstructure": ("liquidity_microstructure_VR1_FINAL.json", "liquidity_microstructure_screen.json"),
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="session", params=tuple(FAMILIES))
def contracts(request: pytest.FixtureRequest) -> tuple[str, dict[str, Any], dict[str, Any]]:
    family = str(request.param)
    reference_name, generated_name = FAMILIES[family]
    return family, _load(REFERENCE / reference_name), _load(GENERATED / generated_name)


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        children = value[:2] + value[-1:] if len(value) > 2 else value
        for index, child in enumerate(children):
            yield from _walk(child, f"{path}[{index}]")


def _identity(value: dict[str, Any]) -> tuple[Any, ...]:
    schema = value.get("schema") if isinstance(value.get("schema"), dict) else {}
    screen = value.get("screen") if isinstance(value.get("screen"), dict) else {}
    return (
        value.get("family", screen.get("family")),
        value.get("screen_id", value.get("screen") if isinstance(value.get("screen"), str) else screen.get("id")),
        value.get("schema_version", value.get("contract_version", schema.get("version"))),
        value.get("stage"),
    )


def _records(value: Any) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    def visit(child: Any, path: str) -> Iterator[tuple[str, list[dict[str, Any]]]]:
        if isinstance(child, dict):
            for key, nested in child.items():
                yield from visit(nested, f"{path}.{key}")
        elif isinstance(child, list) and child:
            if all(isinstance(item, dict) and "timestamp" in item for item in child):
                yield path, child
                return
            samples = child[:2] + child[-1:] if len(child) > 2 else child
            for index, nested in enumerate(samples):
                yield from visit(nested, f"{path}[{index}]")
    yield from visit(value, "$")


def test_identity_and_required_top_level_contract(contracts: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    family, reference, generated = contracts
    assert _identity(generated) == _identity(reference), family
    assert set(reference) <= set(generated), family
    for key in set(reference) & set(generated):
        assert isinstance(generated[key], type(reference[key])) or reference[key] is None, f"{family}:{key}"


def test_series_are_ordered_and_records_have_valid_timestamps(contracts: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    family, _, generated = contracts
    for path, records in _records(generated):
        if ".series" not in path and ".calculation_history.records" not in path:
            continue
        timestamps = [record["timestamp"] for record in records]
        assert all(isinstance(ts, (int, float, str)) and not isinstance(ts, bool) for ts in timestamps), f"{family}:{path}"
        assert timestamps == sorted(timestamps), f"{family}:{path}"


def test_candlestick_records_have_valid_ohlc(contracts: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    family, _, generated = contracts
    for path, records in _records(generated):
        if ".series" not in path and ".calculation_history.records" not in path and ".ohlc" not in path:
            continue
        for record in records:
            if not {"open", "high", "low", "close"} <= set(record):
                continue
            o, h, low, c = (record[key] for key in ("open", "high", "low", "close"))
            if any(value is None for value in (o, h, low, c)):
                continue
            assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (o, h, low, c)), f"{family}:{path}"
            assert h >= max(o, c, low) and low <= min(o, c, h), f"{family}:{path}"


def test_status_reason_consistency(contracts: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    family, _, generated = contracts
    unavailable = {"partial", "unavailable", "invalid", "warming_up", "error"}
    for path, value in _walk(generated):
        if not isinstance(value, dict) or "status" not in value or "reason" not in value:
            continue
        status, reason = value["status"], value["reason"]
        if status in unavailable:
            assert isinstance(reason, str) and reason, f"{family}:{path}"


def test_hmi_computation_policy_and_family_exceptions(contracts: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    family, _, generated = contracts
    walked = tuple(_walk(generated))
    assert not any(path.endswith(".hmi_calculation") and value is True for path, value in walked)
    keys = {path.rsplit(".", 1)[-1].lower() for path, _ in walked}
    if family == "liquidity_microstructure":
        technical = generated.get("technical_analysis")
        assert technical is None or technical.get("enabled") is False
    if family == "long_short_liquidations":
        assert "price_vs_vwap" not in keys
        badge_contract = generated["quality"]["extensions"]["event_badge_contract_v1"]
        assert set(badge_contract) == {"truncated_events", "lower_bound"}
        assert all(item["processing_must_populate"] is True for item in badge_contract.values())
    if family == "prices_ohlcv":
        assert generated["technical_analysis"].get("canonical_location") == "technical_analysis"
        assert generated["technical_analysis"].get("enabled") is True
    if family == "open_interest_and_funding":
        assert generated["technical_analysis"].get("canonical_location") == "technical_analysis"
        assert generated["technical_analysis"].get("enabled") is True


def test_manifest_publishes_exactly_eight_outputs() -> None:
    manifest = _load(CONTRACTS / "run_manifest.json")
    published = manifest["paths"]["hmi_contract"]
    assert set(published) == set(FAMILIES)
    assert len(set(published.values())) == 8
    for relative_path in published.values():
        assert (CONTRACTS / relative_path).is_file()
