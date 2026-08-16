from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from processing_signals.classification.volatility_market_regimes.volatility_market_regimes_classifier import (
    classify_volatility_market_regimes,
)
from processing_signals.classification.volatility_market_regimes.volatility_market_regimes_contract_builder import (
    DISPLAY_RANGE_OPTIONS,
    SCREEN_SCHEMA_VERSION,
    build_market_regime_table,
    build_positioning_ratio_chart,
    build_visible_regime_events,
    build_volatility_market_regimes_screen,
    validate_runtime_context,
    validate_volatility_market_regimes_builder_inputs,
)
from test_volatility_market_regimes_classification import _processing


ROOT = Path(__file__).parents[1]
RUNTIME = {
    "data_mode": "synthetic",
    "is_demo": True,
    "generated_at": "2027-01-15T08:00:00Z",
    "updated_at": "2027-01-15T08:01:00+00:00",
}


def _contracts(mode: str = "bootstrap") -> tuple[dict, dict]:
    processing = _processing(mode=mode)
    classification = classify_volatility_market_regimes(processing)
    return processing, classification


def _screen(mode: str = "bootstrap", runtime: dict | None = None, selected_range: str = "7d") -> dict:
    processing, classification = _contracts(mode)
    return build_volatility_market_regimes_screen(
        processing,
        classification,
        runtime_context=runtime or RUNTIME,
        selected_range=selected_range,
    )


@pytest.mark.parametrize(
    "side,field,value",
    [
        ("processing", "family", "other"),
        ("processing", "stage", "other"),
        ("processing", "version", "9"),
        ("classification", "family", "other"),
        ("classification", "stage", "other"),
        ("classification", "version", "9"),
    ],
)
def test_contract_identity_validation(side, field, value):
    processing, classification = _contracts()
    target = processing if side == "processing" else classification
    target[field] = value
    with pytest.raises(ValueError):
        validate_volatility_market_regimes_builder_inputs(processing, classification, RUNTIME)
    assert build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME
    )["quality"]["status"] == "invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "incremental"),
        ("reference_timestamp", 1),
        ("input_execution_timestamp", 1),
        ("asset", "ETH"),
        ("symbol", "ETHUSDT"),
        ("exchange", "Other"),
        ("base_interval", "4h"),
    ],
)
def test_cross_contract_mismatches_are_invalid(field, value):
    processing, classification = _contracts()
    if field == "mode":
        classification[field] = value
    else:
        classification["context"][field] = value
    result = build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME
    )
    assert result["quality"]["status"] == "invalid"
    assert result["quality"]["errors"]


@pytest.mark.parametrize(
    "runtime",
    [
        {"data_mode": "synthetic", "is_demo": False, "generated_at": "2027-01-01T00:00:00Z", "updated_at": "2027-01-01T00:00:00Z"},
        {"data_mode": "live", "is_demo": True, "generated_at": "2027-01-01T00:00:00Z", "updated_at": "2027-01-01T00:00:00Z"},
        {"data_mode": "live", "is_demo": False, "generated_at": "2027-01-01T00:00:00", "updated_at": "2027-01-01T00:00:00Z"},
    ],
)
def test_runtime_context_rejects_incoherent_or_naive_values(runtime):
    with pytest.raises(ValueError):
        validate_runtime_context(runtime)


def test_contract_root_schema_badges_and_selector_are_realized_only():
    screen = _screen()
    assert screen["family"] == "volatility_market_regimes"
    assert screen["screen"] == "volatility_market_regimes"
    assert screen["schema_version"] == SCREEN_SCHEMA_VERSION == "0.2.0"
    assert screen["badges"] == [{"badge_id": "demo", "text": "DEMO", "status": "active"}]
    assert screen["selectors"]["display_range"]["selected"] == "7d"
    assert tuple(screen["selectors"]["display_range"]["options"]) == DISPLAY_RANGE_OPTIONS
    assert screen["quality"]["status"] == "ok"


def test_live_runtime_has_no_demo_badge():
    live = {
        "data_mode": "live",
        "is_demo": False,
        "generated_at": "2027-01-15T08:00:00Z",
        "updated_at": "2027-01-15T08:01:00+00:00",
    }
    assert _screen(runtime=live)["badges"] == []


def test_invalid_display_range_is_controlled_invalid_contract():
    result = _screen(selected_range="90d")
    assert result["quality"]["status"] == "invalid"
    assert result["quality"]["contract_complete"] is False


def test_kpis_have_stable_realized_only_order_and_source_semantics():
    screen = _screen()
    items = screen["kpis"]["items"]
    assert [item["metric_id"] for item in items] == [
        "current_regime",
        "confidence",
        "realized_volatility",
        "positioning_ratio",
        "persistence",
    ]
    by_id = {item["metric_id"]: item for item in items}
    assert by_id["realized_volatility"]["unit"] == "percent"
    assert by_id["positioning_ratio"]["unit"] == "ratio"
    assert all(item["status"] == "available" for item in items)


def test_positioning_chart_joins_processing_numeric_with_classification_semantics():
    processing, classification = _contracts()
    raw = processing["features"]["positioning"]["records"][-1]
    semantic = classification["classifications"]["positioning"]["records"][-1]
    semantic["positioning_state"] = "short_bias"
    chart = build_positioning_ratio_chart(
        processing["features"]["positioning"],
        classification["classifications"]["positioning"],
    )
    assert chart["records"][-1]["long_short_ratio"] == raw["long_short_ratio"]
    assert chart["records"][-1]["positioning_state"] == "short_bias"
    assert chart["reference_lines"] == [{"value": 1.0, "label": "Balanced"}]


def test_charts_copy_realized_daily_and_distribution_without_reclassification():
    processing, classification = _contracts()
    screen = build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME, selected_range="30d"
    )
    realized = screen["charts"]["realized_volatility"]
    assert realized["records"][-1]["realized_volatility_percent"] == processing["features"]["realized_volatility"]["records"][-1]["realized_volatility_percent"]
    timeline = screen["charts"]["regime_timeline"]
    assert timeline["records"][-1]["confidence_score"] == classification["classifications"]["daily_regimes"]["records"][-1]["confidence_score"]
    distribution = screen["charts"]["regime_distribution"]
    assert distribution["selected_basis"] == "trailing_30d"
    assert distribution["selected"] == classification["summaries"]["regime_distribution"]["trailing_30d"]
    assert "probability" not in json.dumps(distribution).lower()


def test_table_has_stable_rows_and_copies_episode_statistics():
    _, classification = _contracts()
    table = build_market_regime_table(classification["summaries"]["regime_statistics"])
    assert [row["regime"] for row in table["rows"]] == ["low_vol", "normal", "high_vol"]
    assert table["rows"][1]["episode_count"] == classification["summaries"]["regime_statistics"][1]["episode_count"]


def test_source_widget_has_only_current_real_sources_and_internal():
    providers = [item["provider_id"] for item in _screen()["widgets"]["source_status"]["items"]]
    assert providers == ["coinglass", "glassnode", "internal"]


def test_history_is_presentation_windowed_without_changing_upstream_history():
    screen = _screen(selected_range="7d")
    assert screen["charts"]["realized_volatility"]["records_available"] == 120
    assert 1 <= screen["charts"]["realized_volatility"]["records_returned"] <= 8
    assert screen["charts"]["realized_volatility"]["history_truncated"] is True
    assert screen["context"]["history_policy"] == {
        "calculation": "upstream_full_history",
        "presentation": "selected_range_only",
    }


def test_event_filter_preserves_ids_and_filters_outside_window():
    source = {
        "by_id": {"e": {"event_id": "e", "timestamp": 10}},
        "regime_transition_ids": ["e"],
    }
    assert build_visible_regime_events(source, 11, 20) == {"by_id": {}, "regime_transition_ids": []}
    assert build_visible_regime_events(source, 1, 20)["by_id"]["e"]["event_id"] == "e"


def test_broken_or_duplicate_event_references_are_invalid():
    processing, classification = _contracts()
    event_id = "volatility_market_regimes:1:regime_transition:normal:high_vol"
    classification["interpreted_events"]["regime_transition_ids"] = [event_id]
    classification["interpreted_events"]["by_id"] = {}
    assert build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME
    )["quality"]["status"] == "invalid"

    classification["interpreted_events"]["by_id"] = {
        event_id: {"event_id": event_id, "timestamp": 1}
    }
    classification["interpreted_events"]["regime_transition_ids"] = [event_id, event_id]
    assert build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME
    )["quality"]["status"] == "invalid"


def test_unavailable_required_realized_source_degrades_without_fabrication():
    processing, classification = _contracts()
    processing["features"]["realized_volatility"].update(
        status="unavailable", reason="source_not_available", current=None, records=[]
    )
    screen = build_volatility_market_regimes_screen(
        processing, classification, runtime_context=RUNTIME
    )
    assert screen["quality"]["status"] == "partial"
    metric = next(item for item in screen["kpis"]["items"] if item["metric_id"] == "realized_volatility")
    assert metric["status"] == "unavailable" and metric["value"] is None


def test_builder_is_immutable_deterministic_and_strict_json():
    processing, classification = _contracts()
    runtime = copy.deepcopy(RUNTIME)
    originals = copy.deepcopy((processing, classification, runtime))
    first = build_volatility_market_regimes_screen(
        processing, classification, runtime_context=runtime
    )
    second = build_volatility_market_regimes_screen(
        processing, classification, runtime_context=runtime
    )
    assert first == second
    assert (processing, classification, runtime) == originals
    json.dumps(second, ensure_ascii=False, allow_nan=False)


def test_all_supported_modes_build_same_visual_contract_for_same_data():
    screens = [_screen(mode) for mode in ("bootstrap", "incremental", "recovery")]
    assert screens[0] == screens[1] == screens[2]


def test_contract_and_builder_source_have_no_removed_provider_or_series_vocabulary():
    screen_text = json.dumps(_screen(), ensure_ascii=False).lower()
    source = (
        ROOT
        / "src/processing_signals/classification/volatility_market_regimes/volatility_market_regimes_contract_builder.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("deribit", "implied_volatility", "spread_volatility"):
        assert forbidden not in screen_text
        assert forbidden not in source


def test_ast_has_no_input_math_or_recalculation_imports():
    path = ROOT / "src/processing_signals/classification/volatility_market_regimes/volatility_market_regimes_contract_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("processing_signals.input" in item or "processing.math" in item for item in imports)
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for forbidden in (
        "calculate_spread",
        "calculate_confidence",
        "calculate_persistence",
        "calculate_percentile",
        "classify_positioning",
        "build_transition_events",
    ):
        assert forbidden not in names
