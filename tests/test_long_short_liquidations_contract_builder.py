from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

from processing_signals.classification.long_short_liquidations.long_short_liquidations_classifier import classify_long_short_liquidations
from processing_signals.classification.long_short_liquidations.long_short_liquidations_contract_builder import (
    LongShortLiquidationsContractBuilder, build_long_short_liquidations_contract,
    export_long_short_liquidations_contract,
)
from processing_signals.processing.long_short_liquidations.long_short_liquidations_processor import process_long_short_liquidations

SPEC = importlib.util.spec_from_file_location("processing_vertical", Path(__file__).with_name("test_long_short_liquidations_processing_vertical.py"))
PV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PV)
T = PV.T
CONTEXT = {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "market": "futures", "price_precision": 2}
RUNTIME = {"generated_at": T+10, "updated_at": T, "data_mode": "synthetic", "is_demo": True, "cache_status": "disabled"}


def _contracts():
    history = [PV._record(T-100*3600+index*3600, 10+index % 7, 5+index % 5) for index in range(100)]
    events = {"Binance": PV._dataset(values=[PV._event()], provenance={"params": {
        "start_time": (T-86400)*1000, "end_time": T*1000}})}
    source = PV._contract(history=history, events=events)
    source["providers"]["coinglass"]["exchange_snapshot"] = PV._dataset(values=[{
        "exchange": "Binance", "exchange_key": "binance", "liquidation_usd": 100,
        "long_liquidation_usd": 60, "short_liquidation_usd": 40}], snapshot_observed_at=T, source_data_as_of=T)
    levels = [{**PV._level(-30, 4), "leverage_ratio": 10}, {**PV._level(-20, 6), "leverage_ratio": 25},
              {**PV._level(20, 5), "leverage_ratio": 50}, {**PV._level(30, 5), "leverage_ratio": 100}]
    source["providers"]["coinglass"]["aggregated_map"] = PV._dataset(collection="levels", values=levels, snapshot_observed_at=T)
    source["providers"]["coinglass"]["pair_maps"] = {"Binance": PV._dataset(collection="levels", values=levels)}
    source["providers"]["coinglass"]["max_pain"] = PV._dataset(values=[{
        "provider_price": 100, "long_max_pain_liquidation_price": 98, "long_max_pain_liquidation_level": 1,
        "short_max_pain_liquidation_price": 102, "short_max_pain_liquidation_level": 1}])
    processing = process_long_short_liquidations(source, reference_price_context=PV._price_context())
    return processing, classify_long_short_liquidations(processing)


def _build(*, processing=None, classification=None, context=None, runtime=None, selection=None, config=None):
    default_processing, default_classification = _contracts()
    return build_long_short_liquidations_contract(processing or default_processing, classification or default_classification,
        context=context or CONTEXT, runtime_context=runtime or RUNTIME, selection=selection, config=config)


def _kpi(contract, identifier):
    return next(item for item in contract["kpis"] if item["id"] == identifier)


SIDE_IDS = ["pressure_score", "selected_realized_side", "selected_realized_imbalance", "realized_side_24h",
    "realized_imbalance_24h", "estimated_side", "estimated_imbalance", "exchange_concentration",
    "aggregate_map_concentration", "event_activity_15m", "selected_window_largest_event",
    "nearest_estimated_long_cluster", "nearest_estimated_short_cluster", "provider_confirmations", "max_pain",
    "screen_quality_summary"]


@pytest.mark.parametrize("case", range(1, 101), ids=[f"contract_smoke_{index:02d}" for index in range(1, 101)])
def test_contract_smoke(case):
    if case == 1:
        assert _build()["context"]["symbol"] == "BTCUSDT"
    elif case == 2:
        assert _build()["mode"] == "synthetic"
    elif case == 3:
        runtime = {**RUNTIME, "data_mode": "live", "is_demo": False}
        assert _build(runtime=runtime)["mode"] == "live"
    elif case in {4, 5, 6}:
        runtime = deepcopy(RUNTIME)
        if case == 4:
            runtime["is_demo"] = False
        elif case == 5:
            runtime["generated_at"] = True
        else:
            runtime["updated_at"] = runtime["generated_at"] + 1
        with pytest.raises(ValueError, match="invalid_contract_input:runtime_context"):
            _build(runtime=runtime)
    elif case in {7, 8, 9}:
        processing, classification = _contracts()
        if case == 7:
            processing["family"] = "bad"
        elif case == 8:
            classification["stage"] = "processing"
        else:
            classification["reference_timestamp"] += 1
        with pytest.raises(ValueError, match="invalid_contract_input"):
            _build(processing=processing, classification=classification)
    elif case == 10:
        assert _build()["selectors"]["interval"]["selected"] == "1h"
    elif 11 <= case <= 16:
        interval = ("1m", "5m", "15m", "1h", "4h", "1d")[case-11]
        result = _build(selection={"interval": interval, "exchange": "aggregate", "map": "aggregate"})
        assert result["selectors"]["interval"]["selected"] == interval
    elif case == 17:
        with pytest.raises(ValueError, match="invalid_selection:interval"):
            _build(selection={"interval": "12h", "exchange": "aggregate", "map": "aggregate"})
    elif case == 18:
        assert _kpi(_build(), "current_price")["display_value"] == "100.00"
    elif case in {19, 20}:
        processing, classification = _contracts()
        processing["maps"]["reference_price"] = {"status": "unavailable", "reason": "stale_reference_price" if case == 19 else "missing_reference_price", "value": 999}
        result = _build(processing=processing, classification=classification)
        assert _kpi(result, "current_price")["value"] is None
    elif case in {21, 22, 23}:
        identifier = ("total_liquidations_24h", "long_liquidations_24h", "short_liquidations_24h")[case-21]
        assert isinstance(_kpi(_build(), identifier)["value"], (int, float))
    elif case in {24, 25}:
        text = json.dumps(_build())
        assert ("long_share_24h" if case == 24 else "short_share_24h") not in text
    elif case in {26, 27, 28}:
        processing, classification = _contracts()
        status = {26: "available", 27: "partial", 28: "unavailable"}[case]
        processing["pressure"]["status"] = status
        classification["classifications"]["pressure"]["status"] = status
        result = _kpi(_build(processing=processing, classification=classification), "pressure_score")
        assert result["status"] == status and (result["value"] is None) == (status == "unavailable")
    elif case == 29:
        assert _kpi(_build(), "realized_side_24h")["classification"] is not None
    elif case in {30, 31}:
        interval = "1h" if case == 30 else "4h"
        result = _build(selection={"interval": interval, "exchange": "aggregate", "map": "aggregate"})
        assert result["side_panel"]["items"][1]["status"] in {"available", "partial"}
    elif case == 32:
        result = _build(selection={"interval": "15m", "exchange": "aggregate", "map": "aggregate"})
        assert result["side_panel"]["items"][1]["reason"] == "realized_window_not_available_for_selection"
    elif case == 33:
        assert _build()["side_panel"]["items"][5]["classification"] is not None
    elif case == 34:
        processing, classification = _contracts()
        processing["maps"]["aggregated"]["estimated_side_imbalance"] = {"value": 1, "status": "unavailable", "reason": "missing_reference_price"}
        classification["classifications"]["estimated_side"].update(status="unavailable", classification=None)
        assert _build(processing=processing, classification=classification)["side_panel"]["items"][6]["value"] is None
    elif 35 <= case <= 42:
        interval = {35: "1m", 36: "5m", 37: "15m", 38: "15m", 39: "1h", 40: "1h", 41: "4h", 42: "1d"}[case]
        result = _build(selection={"interval": interval, "exchange": "aggregate", "map": "aggregate"})
        event = result["side_panel"]["items"][10]
        if case == 38:
            assert result["side_panel"]["items"][9]["classification"] is not None
        elif case == 40:
            assert result["selectors"]["interval"]["options"][3]["classification_paths"] == []
        else:
            assert event is None or isinstance(event, dict)
    elif case == 43:
        assert _build()["side_panel"]["items"][10]["value"]["event_id"] == "event"
    elif case in {44, 45}:
        processing, classification = _contracts()
        if case == 44:
            processing["events"]["aggregate"]["1h"]["is_lower_bound"] = True
        else:
            processing["events"]["provenance"]["truncation_detected"] = True
        result = _build(processing=processing, classification=classification)
        badge = next(item for item in result["badges"] if item["id"] == ("lower_bound" if case == 44 else "truncated_events"))
        assert badge["status"] == "active"
    elif case in {46, 47}:
        table = _build()["tables"]["exchange_distribution"]
        assert table["rows"] and (table["rows"][0]["provider_difference_usd"] == 0 if case == 47 else True)
    elif case == 48:
        assert _build()["tables"]["exchange_distribution"]["concentration"]["classification"] is not None
    elif case in {49, 50, 51, 52, 53, 54, 55, 56}:
        chart = _build()["charts"]["aggregate_map"]
        assertions = {49: chart["status"] == "available", 50: chart["unit"] == "provider_level",
            51: isinstance(chart["buckets"], list), 52: chart["reference_price"]["value"] == 100,
            53: isinstance(chart["bar_series"], list), 54: chart["chart_id"] == "aggregate_liquidation_map",
            55: chart["visual_contract"]["hmi_calculation"] is False,
            56: isinstance(chart["central_region"]["items"], list)}
        assert assertions[case]
    elif case in {57, 58, 59}:
        chart = _build()["charts"]["aggregate_map"]
        if case == 57:
            assert chart["clusters"]["source"]["estimated_long"]
        elif case == 58:
            assert chart["clusters"]["source"]["estimated_short"]
        else:
            processing, classification = _contracts()
            processing["maps"]["aggregated"]["clusters"] = {"estimated_long": [], "estimated_short": []}
            classification["classifications"]["clusters"].update(classification="no_spatial_clusters")
            assert not _build(processing=processing, classification=classification)["charts"]["aggregate_map"]["clusters"]["source"]["estimated_long"]
    elif case in {60, 61}:
        series = _build()["charts"]["aggregate_map"]["series_by_exchange"]
        assert bool(series) if case == 60 else all(item["exchange"] != "OKX" for item in series)
    elif case in {62, 63}:
        hyper = _build()["charts"]["hyperliquid_map"]
        assert hyper["status"] == "unavailable" if case == 62 else hyper["proxy"] is False
    elif case in {64, 65, 66, 67, 68}:
        result = _build(config={"binance_exchange_key": "Missing"}) if case == 65 else _build()
        chart = result["charts"]["binance_leverage_map"]
        if case == 64:
            assert chart["exchange_key"] == "Binance" and chart["status"] == "available"
        elif case == 65:
            assert chart["status"] == "unavailable"
        elif case == 66:
            assert chart["buckets"]
        elif case == 67:
            assert [item["series_id"] for item in chart["bar_series"]] == ["10x", "25x", "50x", "100x"]
        else:
            assert chart["visual_contract"]["hmi_calculation"] is False
    elif case == 69:
        assert "provider_price" in _build()["side_panel"]["items"][14]
    elif 70 <= case <= 73:
        processing, classification = _contracts()
        atom = classification["classifications"]["confirmations"]["cryptoquant"]
        if case in {70, 71, 72}:
            label = {70: "provider_aligned", 71: "provider_mixed", 72: "provider_divergent"}[case]
            atom.update(classification=label, status="available", confidence=.8)
        rows = _build(processing=processing, classification=classification)["tables"]["provider_confirmations"]["rows"]
        row = next(item for item in rows if item["provider"] == "cryptoquant")
        assert row["classification"] == ({70: "provider_aligned", 71: "provider_mixed", 72: "provider_divergent"}.get(case))
        if case == 72:
            assert next(item for item in _build(processing=processing, classification=classification)["badges"] if item["id"] == "provider_divergence")["status"] == "active"
    elif case == 74:
        assert _build()["quality"]["status"] == "available"
    elif case == 75:
        processing, classification = _contracts()
        processing["pressure"].update(status="unavailable", score=None)
        classification["classifications"]["pressure"].update(status="unavailable", classification=None)
        assert _build(processing=processing, classification=classification)["quality"]["status"] == "partial"
    elif case == 76:
        assert _build()["timestamps"]["data_as_of"] == T
    elif case == 77:
        processing, classification = _contracts()
        processing["exchange_distribution"]["provenance"].update(source_data_as_of=None, snapshot_observed_at=None)
        result = _build(processing=processing, classification=classification)
        assert result["quality"]["status"] == "partial" and result["timestamps"]["data_as_of"] is None
    elif case == 78:
        json.dumps(_build(), ensure_ascii=False, allow_nan=False)
    elif case == 79:
        processing, classification = _contracts()
        before = deepcopy((processing, classification, CONTEXT, RUNTIME))
        _build(processing=processing, classification=classification)
        assert (processing, classification, CONTEXT, RUNTIME) == before
    elif case == 80:
        with tempfile.TemporaryDirectory(dir=".") as directory:
            target = Path(directory) / "screen.json"
            assert export_long_short_liquidations_contract(_build(), target) == target
            assert json.loads(target.read_text(encoding="utf-8"))["stage"] == "contract" and target.read_bytes().endswith(b"\n")
    elif case in {81, 82, 83}:
        processing, classification = _contracts()
        status = "invalid" if case == 82 else "partial" if case == 83 else "unavailable"
        processing["pressure"].update(status=status, score=999999)
        classification["classifications"]["pressure"].update(status=status, classification="extreme_pressure", confidence=.99)
        processing["pressure"]["components"]["realized_intensity"].update(value=999, status="unavailable")
        pressure = _kpi(_build(processing=processing, classification=classification), "pressure_score")
        if case == 83:
            assert pressure["value"] == 999999 and pressure["components"]["realized_intensity"]["value"] is None
        else:
            assert pressure["value"] is None and pressure["components"] == [] and pressure["classification"] is None
    elif case in {84, 85, 86}:
        processing, classification = _contracts()
        status = "invalid" if case == 85 else "unavailable"
        classification["classifications"]["clusters"].update(status=status,
            classification="no_spatial_clusters" if case == 86 else "bilateral_clusters", confidence=.99)
        processing["maps"]["aggregated"]["clusters"] = {"estimated_long": [{"total_level": 999}],
            "estimated_short": [{"total_level": 999}]}
        chart = _build(processing=processing, classification=classification)["charts"]["aggregate_map"]["clusters"]
        assert chart["source"] == {"estimated_long": [], "estimated_short": []}
        assert chart["classification"]["classification"] is None
    elif case in {87, 88}:
        processing, classification = _contracts()
        processing["maps"]["reference_price"].update(status="invalid" if case == 88 else "unavailable", value=999999)
        result = _build(processing=processing, classification=classification)
        assert _kpi(result, "current_price")["value"] is None
        assert result["charts"]["aggregate_map"]["reference_price"]["value"] is None
        assert result["charts"]["aggregate_map"]["central_region"]["items"] == []
    elif case in {89, 90, 91, 92}:
        processing, classification = _contracts()
        value = {89: True, 90: 0, 91: -1, 92: RUNTIME["generated_at"] + 1}[case]
        processing["realized"]["windows"]["24h"]["window_end"] = value
        result = _build(processing=processing, classification=classification)
        assert _kpi(result, "total_liquidations_24h")["timestamp"] is None
        assert result["timestamps"]["data_as_of"] is None and result["quality"]["status"] == "partial"
        if case == 92:
            assert any(item.startswith("future_required_timestamp:") for item in result["warnings"])
    elif case == 93:
        processing, classification = _contracts()
        processing["maps"]["reference_price"]["timestamp"] = T-9
        assert _build(processing=processing, classification=classification)["timestamps"]["data_as_of"] == T-9
    elif case == 94:
        processing, classification = _contracts()
        processing["events"]["aggregate"]["1h"]["window_end"] = "bad"
        result = _build(processing=processing, classification=classification)
        assert result["quality"]["status"] == "available" and result["side_panel"]["items"][10]["status"] == "available"
    elif case in {95, 96, 97}:
        processing, classification = _contracts()
        selection = {"interval": "1m", "exchange": "aggregate", "map": "aggregate"} if case == 97 else None
        if case == 97:
            classification["classifications"]["clusters"].update(status="unavailable", classification=None)
        ids = [item["id"] for item in _build(processing=processing, classification=classification,
                                             selection=selection)["side_panel"]["items"]]
        assert ids == SIDE_IDS and len(ids) == len(set(ids)) == 16
    else:
        context = deepcopy(CONTEXT)
        if case == 98:
            context.update(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDT")
        else:
            context.update(symbol="NOT_THE_PAIR" if case == 100 else "SOLUSDC", base_asset="SOL", quote_asset="USDC")
        chart = _build(context=context)["charts"]["binance_leverage_map"]
        pair = f"{context['base_asset']}/{context['quote_asset']}"
        assert pair in chart["title"] and pair in chart["leverage_title"]
        assert "BTC/USDT" not in chart["title"] + chart["leverage_title"]


@pytest.mark.parametrize("hostile", [float("nan"), float("inf"), {1: "bad"}, {"bad": object()}])
def test_hostile_inputs_are_controlled(hostile):
    context = deepcopy(CONTEXT)
    context["hostile"] = hostile
    with pytest.raises(ValueError, match="invalid_contract_input"):
        _build(context=context)


def test_formats_tokens_warnings_class_and_facade():
    processing, classification = _contracts()
    processing["quality"]["warnings"] = ["same", "same"]
    classification["quality"]["warnings"] = ["same", "other"]
    direct = _build(processing=processing, classification=classification)
    instance = LongShortLiquidationsContractBuilder(context=CONTEXT, runtime_context=RUNTIME).build(processing, classification)
    assert direct["warnings"] == ["same", "other"] and direct == instance
    pressure = _kpi(direct, "pressure_score")
    assert pressure["display_value"].count(".") == 1 and pressure["color_token"].startswith("pressure_")
    assert not any(token in json.dumps(direct).lower() for token in ("#fff", "rgb(", "n/a"))


def test_confirmation_unavailable_metric_residual_is_not_exposed():
    processing, classification = _contracts()
    processing["realized"]["confirmations"]["cryptoquant"]["coverage_ratio"]["value"] = .99
    row = next(item for item in _build(processing=processing, classification=classification)["tables"]["provider_confirmations"]["rows"]
               if item["provider"] == "cryptoquant")
    assert row["coverage_ratio"] is None


@pytest.mark.parametrize("quality_source", ["processing", "classification"])
def test_invalid_upstream_quality_invalidates_screen(quality_source):
    processing, classification = _contracts()
    target = processing if quality_source == "processing" else classification
    target["quality"]["status"] = "invalid"
    assert _build(processing=processing, classification=classification)["quality"]["status"] == "invalid"


def test_export_failure_preserves_previous_and_removes_temporary(monkeypatch):
    with tempfile.TemporaryDirectory(dir=".") as directory:
        root, target = Path(directory), Path(directory) / "screen.json"
        target.write_text("previous\n", encoding="utf-8")
        monkeypatch.setattr("os.replace", lambda source, destination: (_ for _ in ()).throw(OSError("replace failed")))
        with pytest.raises(OSError, match="replace failed"):
            export_long_short_liquidations_contract(_build(), target)
        assert target.read_text(encoding="utf-8") == "previous\n"
        assert list(root.glob("*.tmp")) == []


@pytest.mark.parametrize("case", range(1, 23), ids=[f"contract_high_regression_{index:02d}" for index in range(1, 23)])
def test_contract_high_regressions(case):
    processing, classification = _contracts()
    before = deepcopy((processing, classification))
    if case == 1:
        processing["maps"]["reference_price"].update(status="partial", value=100)
        assert _build(processing=processing, classification=classification)["charts"]["aggregate_map"]["reference_price"]["value"] == 100
    elif case == 2:
        processing["pressure"]["status"] = "partial"
        classification["classifications"]["pressure"]["status"] = "partial"
        processing["pressure"]["components"]["event_intensity"].update(status="invalid", value=999)
        components = _kpi(_build(processing=processing, classification=classification), "pressure_score")["components"]
        assert components["event_intensity"]["value"] is None and components["map_proximity"]["value"] is not None
    elif case == 3:
        classification["classifications"]["clusters"]["status"] = "partial"
        processing["maps"]["aggregated"]["clusters"]["estimated_long"] = {
            "status": "unavailable", "items": [{"total_level": 999}]}
        source = _build(processing=processing, classification=classification)["charts"]["aggregate_map"]["clusters"]["source"]
        assert source["estimated_long"] == [] and source["estimated_short"]
    elif case in {4, 5, 6}:
        result = _build(processing=processing, classification=classification)
        branch = {4: _kpi(result, "pressure_score")["components"],
                  5: result["charts"]["aggregate_map"]["clusters"]["source"],
                  6: result["charts"]["aggregate_map"]["reference_price"]}[case]
        branch["audit_mutation"] = 999
        assert (processing, classification) == before
    elif case in {7, 8, 9}:
        value = {7: 1.5, 8: "1800000000", 9: RUNTIME["generated_at"] + 1}[case]
        processing["realized"]["windows"]["24h"]["window_end"] = value
        result = _build(processing=processing, classification=classification)
        assert result["timestamps"]["data_as_of"] is None and result["timestamps"]["realized_data_as_of"] is None
    elif case == 10:
        processing["realized"]["windows"]["24h"]["window_end"] = False
        warnings = _build(processing=processing, classification=classification)["warnings"]
        assert sum("realized" in item or "liquidations_24h" in item for item in warnings) == 1
    elif case == 11:
        processing["maps"]["reference_price"].update(status="unavailable", timestamp=None, value=999)
        result = _build(processing=processing, classification=classification)
        assert not any("current_price" in item for item in result["quality"]["warnings"])
    elif case == 12:
        processing["events"]["aggregate"]["15m"]["window_end"] = None
        result = _build(processing=processing, classification=classification)
        assert result["quality"]["status"] == "partial" and result["timestamps"]["data_as_of"] is None
    elif case in {13, 14}:
        processing["quality"]["status"] = "invalid" if case == 13 else "unavailable"
        processing["events"]["aggregate"]["15m"]["window_end"] = True
        assert _build(processing=processing, classification=classification)["quality"]["status"] == (
            "invalid" if case == 13 else "unavailable")
    elif case == 15:
        result = _build(processing=processing, classification=classification)
        assert result["header"]["status"] == result["quality"]["status"]
    elif case == 16:
        classification["classifications"]["clusters"].update(status="unavailable", classification=None)
        item = _build(processing=processing, classification=classification)["side_panel"]["items"][11]
        assert item["id"] == "nearest_estimated_long_cluster" and item["status"] == "unavailable"
    elif case in {17, 18}:
        item = _build(processing=processing, classification=classification)["side_panel"]["items"][13 if case == 17 else 15]
        assert item["id"] == ("provider_confirmations" if case == 17 else "screen_quality_summary")
    elif case == 19:
        context = {**CONTEXT, "symbol": "WRONG", "base_asset": "ETH", "quote_asset": "EUR"}
        assert "ETH/EUR" in _build(context=context)["charts"]["binance_leverage_map"]["title"]
    elif case == 20:
        context = deepcopy(CONTEXT)
        _build(context=context)
        assert context == CONTEXT
    elif case == 21:
        json.dumps(_build(processing=processing, classification=classification), allow_nan=False)
    else:
        processing["pressure"].update(status="unavailable", score=999999)
        classification["classifications"]["pressure"].update(status="unavailable", classification="extreme_pressure")
        processing["maps"]["reference_price"].update(status="unavailable", value=999999)
        result = _build(processing=processing, classification=classification)
        assert _kpi(result, "pressure_score")["value"] is None
        assert result["charts"]["aggregate_map"]["reference_price"]["value"] is None
