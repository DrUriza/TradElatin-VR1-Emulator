from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from processing_signals.classification.cvd_volume_orderflow.cvd_volume_orderflow_contract_builder import (
    CvdVolumeOrderflowContractBuilder,
    build_cvd_volume_orderflow_contract,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = 2_000_000_000
MARKETS = ("spot", "futures")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


def metric(value, status="available", reason=None):
    return {"value": value, "status": status, "reason": reason}


def record(timestamp, *, partial=False):
    return {"timestamp": timestamp, "taker_buy_volume_usd": 100.0, "taker_sell_volume_usd": 80.0,
        "total_volume_usd": 180.0, "volume_delta_usd": 999.0, "buy_sell_ratio": metric(1.10),
        "buy_share": metric(.55), "sell_share": metric(.45), "order_flow_imbalance": metric(.18),
        "delta_ma_21_usd": 7.0, "flow_efficiency": metric(.68),
        "cvd_ohlc_usd": {"open": 100.0, "high": 110.0, "low": 90.0, "close": 95.0},
        "coverage_complete": not partial, "is_partial": partial,
        "continuity_status": "complete", "provider_cvd_reference_usd": 123456.0}


def summary(status="available", reason=None):
    return {"taker_buy_volume_usd": 100.0, "taker_sell_volume_usd": 80.0, "total_volume_usd": 180.0,
        "volume_delta_usd": 999.0, "buy_sell_ratio": metric(1.10), "buy_share": metric(.55),
        "sell_share": metric(.45), "order_flow_imbalance": metric(.18), "flow_efficiency": metric(.68),
        "records_expected": 4, "records_used": 4, "coverage_complete": status == "available",
        "first_timestamp": NOW - 3600, "last_timestamp": NOW, "status": status, "reason": reason}


def atom(state, direction="positive", value=1.0):
    return {"state": state, "direction": direction, "value": value, "unit": "decimal",
        "source_path": "markets.spot", "source_status": "available", "threshold_id": "frozen",
        "availability": {"status": "available", "reason": None}}


def atoms():
    return {"delta_state": atom("positive"), "buy_sell_pressure_state": atom("buying"),
        "order_flow_state": atom("buying"), "cvd_direction_state": atom("falling", "negative"),
        "flow_efficiency_state": atom("high", "neutral"), "continuity_state": atom("complete", "neutral"),
        "coverage_state": atom("complete", "neutral")}


def make_bundle(points=220, *, data_mode="synthetic", is_demo=True):
    processing_markets = {}
    classified_markets = {}
    for market in MARKETS:
        timeframes = {}
        classified_timeframes = {}
        for timeframe in TIMEFRAMES:
            rows = [record(NOW - (points - index - 1) * 60, partial=index == points - 1 and timeframe == "15m") for index in range(points)]
            timeframes[timeframe] = {"status": "available", "reason": None, "records_available": len(rows),
                "records": rows, "current": rows[-1] if rows else None}
            classified_timeframes[timeframe] = {"timestamp": rows[-1]["timestamp"] if rows else None,
                "atoms": atoms(), "availability": {"status": "available", "reason": None}}
        footprint = {"vwap_usd": 96.0, "base_volume": 10.0, "quote_volume": 960.0, "records_used": 4,
            "levels_used": 8, "calculation_basis": "available_normalized_footprint_levels",
            "aggregation_scope": "complete_input_scope", "status": "available", "reason": None}
        processing_markets[market] = {"timeframes": timeframes, "window_summaries": {"1h": summary(), "24h": summary()},
            "footprint_summaries": {"1h": footprint}, "price_vs_vwap": {"value": .0042, "status": "available",
                "reason": None, "price_timestamp": NOW, "price_usd": 100.0}, "availability": {}}
        classified_markets[market] = {"timeframes": classified_timeframes,
            "window_summaries": {window: {"timestamp": NOW, "atoms": atoms(), "availability": {"status": "available", "reason": None}} for window in ("1h", "24h")},
            "price_vs_vwap": atom("above"), "availability": {"status": "available", "reason": None}}
    context = {"base_asset": "BTC", "pair_symbol": "BTCUSDT", "markets": list(MARKETS), "timeframes": list(TIMEFRAMES),
        "data_mode": data_mode, "is_demo": is_demo, "reference_timestamp": NOW, "processing_timestamp": NOW}
    processing = {"family": "cvd_volume_orderflow", "stage": "processing", "version": "0.1.0", "mode": "bootstrap",
        "context": {**context, "base_timeframes": ["1m", "15m"], "available_timeframes": list(TIMEFRAMES)},
        "parameters": {"delta_ma_period": 21}, "markets": processing_markets,
        "quality": {"status": "ok", "warnings": [], "errors": []}}
    agreement = {"state": "confirmed_buying", "spot_state": "buying", "futures_state": "buying",
        "availability": {"status": "available", "reason": None}}
    temporal = {market: {"state": "persistent_buying", "one_hour_state": "buying", "twenty_four_hour_state": "buying",
        "availability": {"status": "available", "reason": None}} for market in MARKETS}
    classification = {"family": "cvd_volume_orderflow", "stage": "classification", "version": "0.1.0", "mode": "bootstrap",
        "context": {**context, "classification_timestamp": NOW + 1}, "parameters": {},
        "classifications": {"markets": classified_markets}, "snapshots": {"markets": {market: {} for market in MARKETS}},
        "confirmations": {"market_agreement_1h": agreement, "temporal_alignment": temporal},
        "interpreted_events": [{"event_id": "one", "event_type": "order_flow_transition", "timestamp": NOW,
            "market": "spot", "timeframe": "15m", "severity": "medium", "source_paths": ["processing.markets.spot.timeframes.15m.records"],
            "availability": {"status": "available", "reason": None}}],
        "availability": {"status": "available"}, "quality": {"status": "ok", "warnings": [], "errors": []}}
    return {"processing": processing, "classification": classification}


@pytest.mark.parametrize("value", [None, [], "bundle", 1])
def test_bundle_must_be_exact_mapping(value):
    with pytest.raises(ValueError):
        build_cvd_volume_orderflow_contract(value)
    bundle = make_bundle()
    bundle["extra"] = {}
    with pytest.raises(ValueError, match="root_keys"):
        build_cvd_volume_orderflow_contract(bundle)


@pytest.mark.parametrize(("side", "field", "value"), [
    ("processing", "family", "wrong"), ("processing", "stage", "input"), ("processing", "version", "9"),
    ("classification", "family", "wrong"), ("classification", "stage", "processing"), ("classification", "version", "9")])
def test_rejects_incompatible_contract_identity(side, field, value):
    bundle = make_bundle()
    bundle[side][field] = value
    with pytest.raises(ValueError, match="incompatible"):
        build_cvd_volume_orderflow_contract(bundle)


def test_rejects_mode_context_and_demo_mismatches():
    bundle = make_bundle()
    bundle["classification"]["mode"] = "recovery"
    with pytest.raises(ValueError, match="mode_mismatch"):
        build_cvd_volume_orderflow_contract(bundle)
    for key in ("base_asset", "pair_symbol", "data_mode", "is_demo", "reference_timestamp", "processing_timestamp"):
        bundle = make_bundle()
        bundle["classification"]["context"][key] = "different"
        with pytest.raises(ValueError, match="context_mismatch"):
            build_cvd_volume_orderflow_contract(bundle)
    with pytest.raises(ValueError, match="demo"):
        build_cvd_volume_orderflow_contract(make_bundle(is_demo=False))


def test_accepts_frozen_classification_market_order_and_preserves_visual_order():
    bundle = make_bundle()
    bundle["classification"]["context"]["markets"] = ["futures", "spot"]
    output = build_cvd_volume_orderflow_contract(bundle)
    assert output["context"]["markets"] == ["spot", "futures"]
    assert [item["id"] for item in output["selectors"]["market"]["options"]] == ["spot", "futures"]


@pytest.mark.parametrize("kwargs", [{"selected_market": "x"}, {"selected_timeframe": "2h"},
    {"display_point_limit": True}, {"display_point_limit": 0}, {"display_point_limit": 221}])
def test_rejects_invalid_selection(kwargs):
    with pytest.raises(ValueError):
        build_cvd_volume_orderflow_contract(make_bundle(), **kwargs)


def test_exact_root_identity_context_badges_and_selectors():
    output = build_cvd_volume_orderflow_contract(make_bundle())
    assert list(output) == ["schema", "screen", "stage", "mode", "context", "badges", "selectors", "operational_status",
        "kpis", "charts", "widgets", "tables", "drilldowns", "events", "technical_analysis", "history_contract", "availability", "quality"]
    assert output["schema"] == {"id": "trad_elatin.cvd_volume_orderflow.screen.v1", "version": "1.0.0"}
    assert output["screen"] == {"id": "cvd_volume_orderflow", "family": "cvd_volume_orderflow", "route": "/cvd-orderflow",
        "title": "CVD & ORDER FLOW", "subtitle": "Cumulative volume delta, trades & market microstructure"}
    assert output["context"]["data_as_of"] == NOW
    assert [item["id"] for item in output["badges"]] == ["demo", "data_quality"]
    assert [item["id"] for item in output["selectors"]["market"]["options"]] == list(MARKETS)
    assert [item["id"] for item in output["selectors"]["timeframe"]["options"]] == list(TIMEFRAMES)
    live = build_cvd_volume_orderflow_contract(make_bundle(data_mode="live", is_demo=False))
    assert "demo" not in {item["id"] for item in live["badges"]}


def test_six_kpis_are_direct_and_never_recalculated():
    output = build_cvd_volume_orderflow_contract(make_bundle())
    assert list(output["kpis"]) == ["delta_1h", "buy_sell_ratio_1h", "futures_vs_spot_volume_ratio_1h", "flow_efficiency_1h", "vwap_1h", "order_flow_imbalance_1h"]
    assert output["kpis"]["delta_1h"]["value"] == 999.0
    assert output["kpis"]["buy_sell_ratio_1h"]["value"] == 1.10
    assert output["kpis"]["buy_sell_ratio_1h"]["secondary_values"] == {
        "buy_share": {"value": .55, "unit": "decimal"}, "sell_share": {"value": .45, "unit": "decimal"}}
    assert output["kpis"]["futures_vs_spot_volume_ratio_1h"]["value"] == 1.0
    assert output["kpis"]["order_flow_imbalance_1h"]["value"] == .18
    assert output["kpis"]["flow_efficiency_1h"]["value"] == .68
    assert output["kpis"]["vwap_1h"]["value"] == 96.0
    assert output["kpis"]["delta_1h"]["classification"]["state"] == "positive"


def test_charts_copy_ohlc_delta_ma_six_timeframes_and_last_220():
    bundle = make_bundle(points=225)
    output = build_cvd_volume_orderflow_contract(bundle)
    for chart_id in ("cvd_spot", "cvd_futures"):
        chart = output["charts"][chart_id]
        assert set(chart["series_by_timeframe"]) == set(TIMEFRAMES)
        assert chart["native_ohlc"] is False
        assert chart["construction"] == "derived_from_interval_volume_delta_path"
        series = chart["series_by_timeframe"]["15m"]
        assert len(series["candles"]) == 220 and series["history_truncated"] is True
        assert series["candles"][0]["timestamp"] == bundle["processing"]["markets"][chart_id.removeprefix("cvd_")]["timeframes"]["15m"]["records"][5]["timestamp"]
        assert series["candles"][-1] == {"timestamp": NOW, "open": 100.0, "high": 110.0, "low": 90.0,
            "close": 95.0, "is_partial": True, "continuity_status": "complete"}
        assert chart["current"] == series["candles"][-1]
    delta = output["charts"]["delta_buy_sell_spot"]
    assert set(delta["series_by_timeframe"]) == set(TIMEFRAMES)
    assert delta["presentation"]["moving_average_period"] == 21
    assert delta["current"]["delta_buy_sell_usd"] == 20.0
    assert delta["current"]["delta_ma_21"] == 7.0
    assert "provider_cvd_reference_usd" not in json.dumps(output["charts"])


def test_short_history_is_partial_and_not_fabricated():
    output = build_cvd_volume_orderflow_contract(make_bundle(points=2))
    series = output["charts"]["cvd_spot"]["series_by_timeframe"]["1m"]
    assert len(series["candles"]) == 2
    assert series["status"] == "partial"
    assert series["reason"] == "insufficient_visual_history"
    assert output["quality"]["status"] == "partial"


def test_quality_ok_partial_and_invalid_are_source_driven():
    assert build_cvd_volume_orderflow_contract(make_bundle())["quality"]["status"] == "ok"
    partial = make_bundle()
    partial["processing"]["quality"]["status"] = "partial"
    partial_output = build_cvd_volume_orderflow_contract(partial)
    assert partial_output["quality"]["status"] == "partial"
    assert partial_output["operational_status"]["state"] == "degraded"
    invalid = make_bundle()
    invalid["classification"]["quality"]["status"] = "invalid"
    invalid_output = build_cvd_volume_orderflow_contract(invalid)
    assert invalid_output["quality"]["status"] == "invalid"
    assert invalid_output["operational_status"]["state"] == "blocked"


def test_widgets_tables_drilldowns_events_and_inventory():
    output = build_cvd_volume_orderflow_contract(make_bundle())
    assert set(output["widgets"]) == {"volume_by_side_1h", "volume_by_side_24h", "order_flow_imbalance_1h", "market_agreement_1h", "temporal_alignment"}
    assert output["widgets"]["order_flow_imbalance_1h"]["value"] == .18
    assert output["widgets"]["order_flow_imbalance_1h"]["state"] == "buying"
    assert len(output["tables"]["market_timeframe_overview"]["rows"]) == 12
    assert len(output["tables"]["window_summary_comparison"]["rows"]) == 4
    assert set(output["drilldowns"]) == {"current_market_detail", "market_agreement_detail", "temporal_alignment_detail", "footprint_vwap_scope", "classification_snapshots"}
    assert output["events"]["items"][0]["event_id"] == "one"
    assert len(output["availability"]["required"]) == 16
    assert len(output["availability"]["optional"]) == 9
    assert set(output["availability"]["markets"]) == set(MARKETS)
    assert set(output["availability"]["timeframes"]) == set(TIMEFRAMES)


def test_empty_events_available_and_null_current_row_preserved():
    bundle = make_bundle()
    bundle["classification"]["interpreted_events"] = []
    source = bundle["processing"]["markets"]["spot"]["timeframes"]["1m"]
    source.update(status="unavailable", reason="no_records", records=[], current=None)
    output = build_cvd_volume_orderflow_contract(bundle)
    assert output["events"] == {"id": "recent_events", "status": "available", "reason": None,
        "items": [], "source_path": "classification.interpreted_events", "by_id": {}, "technical_cross_ids": []}
    row = next(row for row in output["tables"]["market_timeframe_overview"]["rows"] if row["market"] == "spot" and row["timeframe"] == "1m")
    assert row["timestamp"] is None and row["volume_delta_usd"] is None
    assert row["status"] == "unavailable" and row["reason"] == "no_records"
    assert output["quality"]["contract_complete"] is True and output["quality"]["data_complete"] is False


def test_empty_available_source_is_unavailable_everywhere():
    bundle = make_bundle()
    source = bundle["processing"]["markets"]["spot"]["timeframes"]["1m"]
    source.update(status="available", reason=None, records=[], current=None)
    output = build_cvd_volume_orderflow_contract(bundle)
    series = output["charts"]["cvd_spot"]["series_by_timeframe"]["1m"]
    assert (series["status"], series["reason"], series["candles"]) == ("unavailable", "no_visual_records", [])
    assert output["charts"]["cvd_spot"]["status"] == "partial"
    assert output["availability"]["required"]["charts.cvd_spot"]["status"] == "partial"
    row = output["tables"]["market_timeframe_overview"]["rows"][0]
    assert (row["market"], row["timeframe"], row["status"], row["reason"]) == (
        "spot", "1m", "unavailable", "current_record_unavailable")
    assert output["quality"]["data_complete"] is False


@pytest.mark.parametrize("path", [r"C:\secret\token.txt", "/tmp/secret.json", "api_key.value", "file.json",
    "input.secret", "runtime.contract", "frontend.component", "https://example.test/value"])
def test_rejects_non_contract_source_paths(path):
    bundle = make_bundle()
    bundle["classification"]["interpreted_events"][0]["source_paths"] = [path]
    with pytest.raises(ValueError, match="source_path"):
        build_cvd_volume_orderflow_contract(bundle)


def test_relative_frozen_source_paths_are_qualified_to_the_real_layer():
    bundle = make_bundle()
    bundle["classification"]["interpreted_events"] = [
        {"event_id": "processing-source", "event_type": "continuity_break", "timestamp": NOW,
            "market": "spot", "timeframe": "15m", "severity": "high",
            "source_paths": ["markets.spot.timeframes.15m.current.continuity_status"],
            "availability": {"status": "available", "reason": None}},
        {"event_id": "classification-source", "event_type": "market_divergence", "timestamp": NOW,
            "market": "futures", "timeframe": "1h", "severity": "medium",
            "source_paths": ["confirmations.market_agreement_1h"],
            "availability": {"status": "available", "reason": None}},
    ]
    items = build_cvd_volume_orderflow_contract(bundle)["events"]["items"]
    assert items[0]["source_paths"] == ["processing.markets.spot.timeframes.15m.current.continuity_status"]
    assert items[1]["source_paths"] == ["classification.confirmations.market_agreement_1h"]


def test_event_wrapper_preserves_invalid_availability_and_reason():
    bundle = make_bundle()
    bundle["classification"]["interpreted_events"][0]["availability"] = {"status": "invalid", "reason": "bad_event"}
    output = build_cvd_volume_orderflow_contract(bundle)
    assert (output["events"]["status"], output["events"]["reason"]) == ("invalid", "bad_event")
    assert output["availability"]["optional"]["events.recent_events"]["status"] == "invalid"


def test_strict_json_nonfinite_bool_numeric_and_source_paths():
    output = build_cvd_volume_orderflow_contract(make_bundle())
    json.dumps(output, ensure_ascii=False, allow_nan=False)
    for value in (float("nan"), float("inf")):
        bundle = make_bundle()
        bundle["processing"]["markets"]["spot"]["window_summaries"]["1h"]["volume_delta_usd"] = value
        with pytest.raises(ValueError, match="non_finite"):
            build_cvd_volume_orderflow_contract(bundle)
    bundle = make_bundle()
    bundle["processing"]["markets"]["spot"]["window_summaries"]["1h"]["volume_delta_usd"] = True
    with pytest.raises(ValueError, match="numeric"):
        build_cvd_volume_orderflow_contract(bundle)
    bundle = make_bundle()
    bundle["classification"]["interpreted_events"][0]["source_paths"] = ["runtime.secret"]
    with pytest.raises(ValueError, match="source_path"):
        build_cvd_volume_orderflow_contract(bundle)


def test_bundle_immutable_no_aliases_and_deterministic():
    bundle = make_bundle()
    before = copy.deepcopy(bundle)
    first = build_cvd_volume_orderflow_contract(bundle)
    second = build_cvd_volume_orderflow_contract(bundle)
    assert bundle == before and first == second
    first["charts"]["cvd_spot"]["series_by_timeframe"]["1m"]["candles"][0]["close"] = -1
    first["drilldowns"]["classification_snapshots"]["value"]["markets"]["spot"]["changed"] = True
    assert bundle == before
    bundle["processing"]["markets"]["spot"]["timeframes"]["1m"]["records"][0]["cvd_ohlc_usd"]["close"] = -2
    assert second["charts"]["cvd_spot"]["series_by_timeframe"]["1m"]["candles"][0]["close"] == 95.0


def test_public_methods_and_no_execution_or_io_symbols():
    expected = {"validate_bundle", "validate_processing_contract", "validate_classification_contract", "validate_bundle_consistency",
        "build_context", "build_badges", "build_selectors", "build_operational_status", "build_kpis", "build_cvd_chart",
        "build_delta_chart", "build_charts", "build_widgets", "build_tables", "build_drilldowns", "build_events",
        "evaluate_availability", "evaluate_quality", "run"}
    assert expected <= set(dir(CvdVolumeOrderflowContractBuilder))
    source = (ROOT / "src/processing_signals/classification/cvd_volume_orderflow/cvd_volume_orderflow_contract_builder.py").read_text(encoding="utf-8")
    for forbidden in ("requests", "httpx", "urllib", "api_key", "run_cvd_volume_orderflow_input", "process_cvd_volume_orderflow",
        "classify_cvd_volume_orderflow", "input_pipeline", "processing_pipeline", "classification_pipeline", "main_pipeline",
        "write_text", "write_bytes", "json.dump("):
        assert forbidden not in source


def test_frozen_binary_hashes():
    expected = {
        "src/processing_signals/input/cvd_volume_orderflow/cvd_volume_orderflow_data_raw_extract.py": "e461826c4c4d067d0cbff2dea33dcb9f977caefec61cfc96699bb39b06a1f13e",
        "src/processing_signals/input/cvd_volume_orderflow/cvd_volume_orderflow_data_raw_preprocessing.py": "2d218f206cbb841cd0724ee757590594e47208b444901be737d3864e05196e38",
        "tests/test_cvd_volume_orderflow_input_vertical.py": "1ddd3587e7f5f218ac15df9b1d72b06f288b632b7f59a96770eb608d6d09e5b0",
    }
    assert {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in expected} == expected
