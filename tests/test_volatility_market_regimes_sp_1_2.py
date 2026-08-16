from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from processing_signals.main.runtime_orchestrator import run_all

ROOT = Path(__file__).parents[1]
SP = ROOT / "src/processing_signals/classification/volatility_market_regimes/volatility_market_regimes_screen_sp_v1_2.json"


def _run() -> dict[str, Any]:
    return run_all(source="emulator", enabled_families=("volatility_market_regimes",))


def _shape_variants(value: list[Any]) -> set[tuple[str, ...]]:
    return {tuple(sorted(item)) for item in value if isinstance(item, Mapping)}


def _assert_shape(reference: Any, candidate: Any, path: str = "root") -> None:
    if isinstance(reference, Mapping):
        assert isinstance(candidate, Mapping), path
        # Event IDs are runtime data, so compare event value shapes rather than fixture keys.
        if path.endswith("events.by_id"):
            ref_shapes = {tuple(sorted(v)) for v in reference.values() if isinstance(v, Mapping)}
            cand_shapes = {tuple(sorted(v)) for v in candidate.values() if isinstance(v, Mapping)}
            assert cand_shapes <= ref_shapes, path
            return
        assert set(candidate) == set(reference), path
        for key in reference:
            _assert_shape(reference[key], candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), path
        if reference and isinstance(reference[0], Mapping):
            assert _shape_variants(candidate) <= _shape_variants(reference), path
            if candidate:
                # Compare each candidate item to a compatible reference variant.
                for index, item in enumerate(candidate):
                    target = next((ref for ref in reference if isinstance(ref, Mapping) and set(ref) == set(item)), reference[0])
                    _assert_shape(target, item, f"{path}[{index}]")
        return


def test_volatility_vertical_uses_frozen_sp_and_is_family_isolatable() -> None:
    runtime = _run()
    assert set(runtime["input"]) == {"volatility_market_regimes"}
    screen = runtime["hmi_contract"]["volatility_market_regimes"]
    assert screen["schema_version"] == "1.2.0-no-regime-timeline"
    assert screen["context"]["default_display_range"] == "30d"
    assert screen["selectors"]["display_range"]["options"] == ["7D", "30D", "90D", "360D"]


def test_dvol_and_rv_spread_are_real_vertical_features_not_hmi_calculations() -> None:
    runtime = _run()
    inp = runtime["input"]["volatility_market_regimes"]
    processing = runtime["processing"]["volatility_market_regimes"]
    classification = runtime["classification"]["volatility_market_regimes"]
    screen = runtime["hmi_contract"]["volatility_market_regimes"]

    assert set(inp["providers"]["glassnode"]) == {"realized_volatility", "dvol"}
    assert processing["features"]["dvol"]["status"] == "available"
    assert processing["features"]["volatility_spread"]["basis"] == "realized_minus_implied"
    assert classification["classifications"]["volatility_context"]["status"] == "available"
    assert screen["technical_analysis"]["recalculate_in_hmi"] is False
    assert screen["history_contract"]["hmi_recalculation"] is False

    kpis = {item["metric_id"]: item for item in screen["kpis"]["items"]}
    assert list(kpis) == ["current_regime", "confidence", "spread_7d", "persistence", "dvol"]
    assert kpis["dvol"]["status"] == "available"
    assert kpis["dvol"]["source"]["provider"] == "glassnode"
    assert kpis["dvol"]["provenance"]["integration_state"] == "runtime_feed"
    assert kpis["spread_7d"]["metadata"]["basis"] == "realized_minus_implied"
    assert kpis["spread_7d"]["metadata"]["records_used"] == 168


def test_realized_volatility_candlestick_is_preserved_and_processing_owned() -> None:
    screen = _run()["hmi_contract"]["volatility_market_regimes"]
    chart = screen["charts"]["volatility_comparison"]
    assert chart["chart_type"] == "candlestick"
    assert len(chart["candles"]) == 730
    assert chart["source"]["provider"] == "glassnode"
    assert chart["source"]["endpoint_id"] == "realized_volatility_1_week"
    assert chart["supporting_reference"]["implied_volatility_provider"] == "glassnode"
    assert chart["supporting_reference"]["implied_volatility_endpoint_id"] == "dvol_ohlc"
    assert chart["ohlc_contract"]["native_provider_ohlc"] is False
    assert chart["ohlc_contract"]["hmi_must_reconstruct_ohlc"] is False
    assert chart["ohlc_contract"]["line_fallback_allowed"] is False
    assert all(set(candle) == {"timestamp", "open", "high", "low", "close", "unit"} for candle in chart["candles"])


def test_positioning_and_full_history_are_preserved() -> None:
    screen = _run()["hmi_contract"]["volatility_market_regimes"]
    assert len(screen["charts"]["positioning_ratio"]["records"]) == 730
    assert screen["charts"]["positioning_ratio"]["source"]["provider"] == "coinglass"
    assert screen["history_contract"]["calculation_records"] == 730
    assert screen["history_contract"]["all_visible_moving_averages_warm"] is True
    assert screen["history_contract"]["technical_indicators_precomputed"] is True


def test_generated_screen_is_structurally_projected_to_final_sp_and_strict_json() -> None:
    screen = _run()["hmi_contract"]["volatility_market_regimes"]
    reference = json.loads(SP.read_text(encoding="utf-8"))
    _assert_shape(reference, screen)
    json.dumps(screen, ensure_ascii=False, allow_nan=False)
