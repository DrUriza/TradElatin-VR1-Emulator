from copy import deepcopy
import json

import pytest

from processing_signals.processing.long_short_liquidations.long_short_liquidations_feature_builder import (
    PRESSURE_WEIGHTS, aggregate_regular_window, build_event_intensity, build_event_window, build_exchange_distribution,
    build_map_features, build_pressure_score, bucket_map_levels, concentration, confirmation, realized_imbalance, variation,
)
from processing_signals.processing.long_short_liquidations.long_short_liquidations_processor import (
    process_long_short_liquidations, validate_reference_price_context,
)

T = 1_800_000_000


def _record(timestamp, long=10, short=5):
    return {"timestamp": timestamp, "long_liquidation_usd": long, "short_liquidation_usd": short}


def test_exact_half_open_windows_and_coverage_boundaries():
    records = [_record(T - 4 * 3600 + i * 3600) for i in range(5)]
    complete = aggregate_regular_window(records, window_end=T, window_seconds=4 * 3600)
    assert complete["observed_count"] == 4
    assert complete["status"] == "available"
    assert complete["event_count"] == 4
    partial = aggregate_regular_window(records[:3], window_end=T, window_seconds=4 * 3600)
    assert partial["coverage_ratio"] == .75 and partial["status"] == "partial"
    assert aggregate_regular_window(records[:2], window_end=T, window_seconds=4 * 3600)["status"] == "unavailable"


def test_variation_previous_zero_and_zero_imbalance_denominator():
    current = aggregate_regular_window([_record(T - 3600)], window_end=T, window_seconds=3600)
    previous = aggregate_regular_window([_record(T - 7200, 0, 0)], window_end=T - 3600, window_seconds=3600)
    changed = variation(current, previous)
    assert changed["total"]["absolute_change"] == 15
    assert changed["total"]["relative_change"] is None
    assert realized_imbalance(0, 0) == {"value": None, "status": "unavailable", "reason": "zero_total_liquidation"}


def test_event_zero_rule_and_deterministic_maximum():
    assert build_event_window([], window_end=T, window_seconds=60, coverage_complete=True)["event_count"] == 0
    assert build_event_window([], window_end=T, window_seconds=60, coverage_complete=False)["status"] == "unavailable"
    events = [{"event_id": "a", "timestamp": T - 2, "usd_value": 10},
              {"event_id": "b", "timestamp": T - 1, "usd_value": 10}]
    assert build_event_window(events, window_end=T, window_seconds=60, coverage_complete=True)["max_event"]["event_id"] == "b"


def test_bucketing_central_no_interpolation_accumulation_and_hhi():
    levels = [{"price_level": 99.8, "provider_liquidation_level": 2},
              {"price_level": 100, "provider_liquidation_level": 3},
              {"price_level": 100.2, "provider_liquidation_level": 5}]
    buckets = bucket_map_levels(levels, 100)
    assert len(buckets) == 3
    assert any(bucket["region"] == "central" for bucket in buckets)
    features = build_map_features(levels, 100)
    assert features["curves"]["estimated_short"][-1]["cumulative_level"] == 5
    result = concentration([1, 1])
    assert result["hhi"] == .5 and result["effective_bucket_count"] == 2


def test_clusters_and_reference_absence():
    levels = [{"price_level": p, "provider_liquidation_level": 10} for p in (98.0, 98.1, 99.0)]
    features = build_map_features(levels, 100)
    assert features["clusters"]["estimated_long"]
    missing = build_map_features(levels, None)
    assert missing["concentration"]["complete_map"]["status"] == "available"
    assert missing["spatial"]["status"] == "unavailable"


def test_pressure_complete_partial_and_unavailable():
    all_components = {key: .5 for key in ("realized_intensity", "realized_acceleration", "event_intensity",
                                           "map_proximity", "map_concentration", "imbalance_magnitude")}
    assert build_pressure_score(all_components)["status"] == "available"
    partial = dict(all_components)
    partial["imbalance_magnitude"] = None
    assert build_pressure_score(partial)["status"] == "partial"
    unavailable = dict(all_components)
    unavailable["map_proximity"] = None
    assert build_pressure_score(unavailable)["status"] == "unavailable"


def test_processor_is_immutable_and_json_strict_with_independent_max_pain():
    source = {"family": "long_short_liquidations", "stage": "input", "reference_timestamp": T,
              "providers": {"coinglass": {
                  "aggregated_history": {"status": "available", "records": [_record(T - 3600)], "warnings": []},
                  "exchange_snapshot": {"status": "available", "records": []}, "pair_history": {}, "events": {},
                  "aggregated_map": {"status": "available", "levels": [{"price_level": 99, "provider_liquidation_level": 2}],
                                     "snapshot_observed_at": T - 1, "range": "1d", "warnings": []},
                  "pair_maps": {}, "max_pain": {"status": "available", "records": [{"provider_price": 100,
                      "long_max_pain_liquidation_price": 98, "long_max_pain_liquidation_level": 1,
                      "short_max_pain_liquidation_price": 102, "short_max_pain_liquidation_level": 1}]}}},
              "quality": {"status": "available", "warnings": [], "errors": []}}
    price = {"value": 100, "timestamp": T - 2, "source_family": "prices_ohlcv", "source_market": "spot",
             "source_timeframe": "1m", "price_field": "close", "is_closed_bar": True}
    before_source, before_price = deepcopy(source), deepcopy(price)
    output = process_long_short_liquidations(source, reference_price_context=price)
    assert source == before_source and price == before_price
    assert output["maps"]["max_pain"]["long_distance_bps"] == pytest.approx(-200)
    json.dumps(output, allow_nan=False)


def _dataset(status="available", collection="records", values=None, reason=None, **extra):
    return {"status": status, "reason": reason, collection: values or [], "provenance": {}, "warnings": [], "errors": [], **extra}


def _contract(*, history=None, events=None, pair_maps=None, quality="available"):
    return {"family": "long_short_liquidations", "stage": "input", "reference_timestamp": T,
            "providers": {"coinglass": {"aggregated_history": _dataset(values=history or [_record(T - 3600)]),
                "exchange_snapshot": _dataset(), "pair_history": {}, "events": events or {},
                "aggregated_map": _dataset(collection="levels", values=[{"price_level": 99, "provider_liquidation_level": 2}],
                                           snapshot_observed_at=T),
                "pair_maps": pair_maps or {}, "max_pain": _dataset()}},
            "quality": {"status": quality, "warnings": [], "errors": []}}


def _level(bps, level=1):
    return {"price_level": round(100 * (1 + bps / 10000), 10), "provider_liquidation_level": level}


def _event(**changes):
    return {"event_id": "event", "timestamp": T - 1, "exchange": "Binance", "symbol": "BTCUSDT", "base_asset": "BTC",
            "price": 100, "usd_value": 10, "raw_side": 1, "order_side": "buy", **changes}


def _price_context(timestamp=T):
    return {"value": 100, "timestamp": timestamp, "source_family": "prices_ohlcv", "source_market": "spot",
            "source_timeframe": "1m", "price_field": "close", "is_closed_bar": True}


def _is_contract_error(source):
    try:
        process_long_short_liquidations(source)
    except ValueError as exc:
        return str(exc).startswith("invalid_input_contract:")
    return False


@pytest.mark.parametrize("case", range(1, 61), ids=[f"processing_smoke_{index:02d}" for index in range(1, 61)])
def test_processing_smoke(case):
    records = [_record(T - 14400 + index * 3600) for index in range(4)]
    window = aggregate_regular_window(records + [_record(T)], window_end=T, window_seconds=14400)
    if case in {1, 2}:
        assert window["observed_count"] == 4
    elif case == 3:
        assert window["coverage_ratio"] == 1 and window["status"] == "available"
    elif case == 4:
        assert aggregate_regular_window(records[:3], window_end=T, window_seconds=14400)["status"] == "partial"
    elif case == 5:
        assert aggregate_regular_window(records[:2], window_end=T, window_seconds=14400)["status"] == "unavailable"
    elif case == 6:
        assert aggregate_regular_window(records + [records[0]], window_end=T, window_seconds=14400)["observed_count"] == 4
    elif case == 7:
        assert window["total_usd"] == 60
    elif case in {8, 9}:
        changed = variation({"status": "available", "long_total_usd": 2, "short_total_usd": 4, "total_usd": 6},
                            {"status": "available", "long_total_usd": 1, "short_total_usd": 2, "total_usd": 3})
        assert changed["total"]["absolute_change" if case == 8 else "relative_change"] == (3 if case == 8 else 1)
    elif case == 10:
        changed = variation({"status": "available", "long_total_usd": 1, "short_total_usd": 0, "total_usd": 1},
                            {"status": "available", "long_total_usd": 0, "short_total_usd": 0, "total_usd": 0})
        assert changed["total"]["relative_change_reason"] == "zero_previous_value"
    elif case in {11, 12, 13, 14}:
        values = {11: (1, 0, 1), 12: (0, 1, -1), 13: (1, 1, 0), 14: (0, 0, None)}[case]
        assert realized_imbalance(values[0], values[1])["value"] == values[2]
    elif case in {15, 16, 17}:
        result = build_exchange_distribution([{"exchange": key, "exchange_key": key, "liquidation_usd": 1,
            "long_liquidation_usd": 1, "short_liquidation_usd": 0} for key in ("a", "b")])
        expected = {15: 1, 16: .5, 17: 2}[case]
        actual = sum(item["exchange_share"] for item in result["exchanges"]) if case == 15 else result["concentration"][
            "hhi" if case == 16 else "effective_exchange_count"]
        assert actual == expected
    elif case == 18:
        assert build_exchange_distribution([])["reason"] == "zero_exchange_total"
    elif case == 19:
        events = [{"event_id": "a", "timestamp": T - 1, "usd_value": 2}, {"event_id": "b", "timestamp": T - 1, "usd_value": 2}]
        assert build_event_window(events, window_end=T, window_seconds=60, coverage_complete=True)["max_event"]["event_id"] == "b"
    elif case in {20, 21}:
        result = build_event_window([], window_end=T, window_seconds=60, coverage_complete=case == 20)
        assert result["status"] == ("available" if case == 20 else "unavailable")
    elif case in {22, 23, 24}:
        age = {22: 120, 23: 121, 24: -1}[case]
        context = {"value": 100, "timestamp": T - age, "source_family": "prices_ohlcv", "source_market": "spot",
                   "source_timeframe": "1m", "price_field": "close", "is_closed_bar": True}
        assert validate_reference_price_context(context, T)[0] == (100 if case == 22 else None)
    elif case in {25, 26}:
        assert bucket_map_levels([_level(10 if case == 25 else -10)], 100)[0]["bucket_index"] == (1 if case == 25 else -1)
    elif case == 27:
        assert bucket_map_levels([_level(0)], 100)[0]["region"] == "central"
    elif case == 28:
        assert len(bucket_map_levels([_level(20)], 100)) == 1
    elif case in {29, 30}:
        curves = build_map_features([_level(-30), _level(-20), _level(20), _level(30)], 100)["curves"]
        points = curves["estimated_long" if case == 29 else "estimated_short"]
        assert points[0]["price"] > points[1]["price"] if case == 29 else points[0]["price"] < points[1]["price"]
    elif case == 31:
        assert build_map_features([_level(-20), _level(20)], 100)["estimated_side_imbalance"]["value"] == 0
    elif case == 32:
        assert concentration([1, 1, 1, 1])["top3_share"] == .75
    elif case in {33, 34}:
        levels = [_level(-30), _level(-20 if case == 33 else -10)]
        assert bool(build_map_features(levels, 100)["clusters"]["estimated_long"]) == (case == 33)
    elif case == 35:
        assert "max_pain" not in build_map_features([_level(20)], 100)
    elif case in {36, 37}:
        count = 23 if case == 36 else 24
        left = [_record(index, 1, 1) for index in range(1, count + 1)]
        right = [{"timestamp": index, "long_liquidations_usd": 1, "short_liquidations_usd": 1} for index in range(1, count + 1)]
        expected = "insufficient_aligned_points" if case == 36 else "zero_variance"
        assert confirmation(left, right)["pearson_correlation"]["reason"] == expected
    elif case in {38, 39, 40, 50}:
        components = {key: .5 for key in PRESSURE_WEIGHTS}
        if case == 39:
            components["imbalance_magnitude"] = None
        if case == 40:
            components["map_proximity"] = None
        result = build_pressure_score(components)
        assert (result["status"] == {38: "available", 39: "partial", 40: "unavailable"}[case]) if case != 50 else 0 <= result["score"] <= 100
    elif case in {41, 42, 43, 44}:
        source, reference, config = _contract(), {"value": 100, "timestamp": T, "source_family": "prices_ohlcv", "source_market": "spot",
            "source_timeframe": "1m", "price_field": "close", "is_closed_bar": True}, {"custom": [1]}
        before = deepcopy((source, reference, config))
        output = process_long_short_liquidations(source, reference_price_context=reference, config=config)
        assert (source, reference, config) == before if case != 44 else json.dumps(output, allow_nan=False)
    elif case in {45, 46}:
        text = json.dumps(process_long_short_liquidations(_contract())).lower()
        assert ("bullish" not in text and "bearish" not in text) if case == 45 else "hmi" not in text
    elif case == 47:
        source = _contract()
        source["providers"]["coinglass"]["aggregated_history"] = _dataset("unavailable", reason="not_requested")
        assert process_long_short_liquidations(source)["source_selection"]["realized_aggregate"]["fallback_applied"] is False
    elif case == 48:
        output = process_long_short_liquidations(_contract())
        assert output["maps"]["aggregated"] is not output["maps"]["aligned_exchanges"]
    elif case == 49:
        assert build_map_features([_level(0)], 100)["estimated_side_imbalance"]["value"] is None
    elif case == 51:
        source = _contract()
        source["providers"]["coinglass"]["exchange_snapshot"] = _dataset(values=[{
            "exchange": "A", "exchange_key": "a", "liquidation_usd": True, "long_liquidation_usd": 1, "short_liquidation_usd": 0}])
        assert _is_contract_error(source)
    elif case == 52:
        source = _contract()
        source["providers"]["coinglass"]["pair_history"] = {"A": _dataset(values=[_record("bad")])}
        assert _is_contract_error(source)
    elif case == 53:
        source = _contract()
        source["providers"]["coinglass"]["events"] = {"A": _dataset(values=[_event(timestamp="bad")])}
        assert _is_contract_error(source)
    elif case == 54:
        source = _contract()
        source["providers"]["coinglass"]["pair_maps"] = {"A": _dataset(collection="levels", values=[_level(20, True)])}
        assert _is_contract_error(source)
    elif case == 55:
        source = _contract()
        source["providers"]["coinglass"]["max_pain"] = _dataset(values=[{
            "provider_price": True, "long_max_pain_liquidation_price": 99, "long_max_pain_liquidation_level": 1,
            "short_max_pain_liquidation_price": 101, "short_max_pain_liquidation_level": 1}])
        assert _is_contract_error(source)
    elif case == 56:
        source = _contract()
        source["providers"]["cryptoquant"] = {"aggregate_history": _dataset(values=[{
            "timestamp": "bad", "long_liquidations_usd": 1, "short_liquidations_usd": 1}], interval="1h")}
        assert _is_contract_error(source)
    elif case in {57, 58}:
        reference = None if case == 57 else _price_context(T - 121)
        buckets = process_long_short_liquidations(_contract(), reference_price_context=reference)["maps"]["aggregated"]["buckets"]
        assert buckets == {"status": "unavailable", "reason": "missing_reference_price" if case == 57 else "stale_reference_price", "items": []}
    elif case == 59:
        buckets = process_long_short_liquidations(_contract(), reference_price_context=_price_context())["maps"]["aggregated"]["buckets"]
        assert buckets["status"] in {"available", "partial"} and isinstance(buckets["items"], list)
    elif case == 60:
        source, reference, config = _contract(), _price_context(), {"audit": [1]}
        before = deepcopy((source, reference, config))
        output = process_long_short_liquidations(source, reference_price_context=reference, config=config)
        assert (source, reference, config) == before and json.dumps(output, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("reference", [None, True, 0, -1])
def test_invalid_reference_timestamp_is_controlled(reference):
    source = _contract()
    source["reference_timestamp"] = reference
    with pytest.raises(ValueError, match="invalid_input_contract:reference_timestamp"):
        process_long_short_liquidations(source)


@pytest.mark.parametrize("hostile", [float("nan"), float("inf"), float("-inf"), {1: "bad"}, {"bad": object()}])
def test_hostile_json_is_controlled(hostile):
    source = _contract()
    source["hostile"] = hostile
    with pytest.raises(ValueError, match="invalid_input_contract"):
        process_long_short_liquidations(source)


def test_input_quality_invalid_blocks_residual_records():
    output = process_long_short_liquidations(_contract(quality="invalid"))
    assert output["quality"]["status"] == "invalid" and output["realized"]["series"] == []


def test_invalid_dataset_residual_records_are_not_processed():
    source = _contract()
    source["providers"]["coinglass"]["aggregated_history"] = _dataset("invalid", values=[_record(T - 3600)], reason="bad")
    output = process_long_short_liquidations(source)
    assert output["realized"]["series"] == [] and output["quality"]["status"] == "invalid"


def test_off_grid_and_long_coverage_thresholds():
    records = [_record(T - 86400 + index * 3600) for index in range(24)]
    assert aggregate_regular_window(records[:18], window_end=T, window_seconds=86400)["status"] == "partial"
    assert aggregate_regular_window(records[:17], window_end=T, window_seconds=86400)["status"] == "unavailable"
    result = aggregate_regular_window(records + [_record(T - 1)], window_end=T, window_seconds=86400)
    assert result["observed_count"] == 24 and result["misaligned_timestamps"] == [T - 1]


def test_event_intensity_32_and_31_bins():
    assert build_event_intensity([], current_end=T, coverage_checker=lambda start, end: end >= T - 32 * 900)["status"] == "available"
    assert build_event_intensity([], current_end=T, coverage_checker=lambda start, end: end >= T - 31 * 900)["status"] == "unavailable"


def test_confirmation_zero_reference_mape_and_invalid_concentration():
    left = [_record(index, 0, 0) for index in range(1, 25)]
    right = [{"timestamp": index, "long_liquidations_usd": 1, "short_liquidations_usd": 1} for index in range(1, 25)]
    assert confirmation(left, right)["median_absolute_percentage_error"]["reason"] == "no_nonzero_reference_points"
    assert concentration([1, None])["status"] == "partial"
    assert concentration([None, True])["status"] == "invalid"


def test_missing_reference_branches_and_aligned_partial_and_metadata():
    pair_maps = {"A": _dataset(collection="levels", values=[_level(20)]), "B": _dataset("invalid", collection="levels", reason="bad")}
    output = process_long_short_liquidations(_contract(pair_maps=pair_maps))
    spatial = output["maps"]["aggregated"]
    assert spatial["map_proximity"]["status"] == "unavailable" and spatial["curves"]["estimated_long"]["status"] == "unavailable"
    assert output["maps"]["aligned_exchanges"]["status"] == "unavailable"
    assert len(output["source_selection"]) == 12 and output["maps"]["aggregated"]["provenance"]["bucket_width_bps"] == 10


def test_informational_warning_does_not_degrade_quality_when_required_available():
    history = [_record(T - 86400 + index * 3600) for index in range(24)]
    source = _contract(history=history)
    source["quality"]["warnings"] = ["information_only"]
    source["providers"]["coinglass"]["events"] = {"A": _dataset(values=[], provenance={"params": {"start_time": (T-86400)*1000, "end_time": T*1000}})}
    source["providers"]["coinglass"]["exchange_snapshot"] = _dataset(values=[{"exchange": "A", "exchange_key": "a", "liquidation_usd": 1,
        "long_liquidation_usd": 1, "short_liquidation_usd": 0}])
    output = process_long_short_liquidations(source)
    assert output["quality"]["status"] == "available"


@pytest.mark.parametrize(("field", "value"), [("liquidation_usd", True), ("long_liquidation_usd", float("nan")),
                                               ("short_liquidation_usd", -1), ("exchange", "")])
def test_exchange_snapshot_deep_validation(field, value):
    record = {"exchange": "A", "exchange_key": "a", "liquidation_usd": 1, "long_liquidation_usd": 1, "short_liquidation_usd": 0}
    record[field] = value
    source = _contract()
    source["providers"]["coinglass"]["exchange_snapshot"] = _dataset(values=[record])
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.exchange_snapshot\.records\[0\]"):
        process_long_short_liquidations(source)


@pytest.mark.parametrize(("field", "value"), [("timestamp", "bad"), ("timestamp", True),
                                               ("long_liquidation_usd", True), ("short_liquidation_usd", float("inf"))])
def test_pair_history_deep_validation(field, value):
    record = _record(T - 3600)
    record[field] = value
    source = _contract()
    source["providers"]["coinglass"]["pair_history"] = {"A": _dataset(values=[record])}
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.pair_history\.A\.records\[0\]"):
        process_long_short_liquidations(source)


def test_pair_history_records_must_be_list():
    source = _contract()
    source["providers"]["coinglass"]["pair_history"] = {"A": _dataset(values={"bad": "shape"})}
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.pair_history\.A\.records"):
        process_long_short_liquidations(source)


@pytest.mark.parametrize(("field", "value"), [("timestamp", "bad"), ("timestamp", True), ("price", "bad"),
                                               ("usd_value", True), ("event_id", "")])
def test_event_deep_validation(field, value):
    event = _event()
    event[field] = value
    source = _contract()
    source["providers"]["coinglass"]["events"] = {"Binance": _dataset(values=[event])}
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.events\.Binance\.records\[0\]"):
        process_long_short_liquidations(source)


def test_event_record_mapping_and_builder_defense():
    source = _contract()
    source["providers"]["coinglass"]["events"] = {"Binance": _dataset(values=["bad"])}
    with pytest.raises(ValueError, match="invalid_input_contract:"):
        process_long_short_liquidations(source)
    with pytest.raises(ValueError, match="invalid_event_record:timestamp"):
        build_event_window([{"event_id": "x", "timestamp": "bad", "usd_value": 1}], window_end=T, window_seconds=60, coverage_complete=True)


@pytest.mark.parametrize(("field", "value"), [("price_level", "bad"), ("provider_liquidation_level", True),
                                               ("provider_liquidation_level", float("nan")), ("leverage_ratio", float("inf"))])
def test_pair_map_deep_validation(field, value):
    level = {**_level(20), "leverage_ratio": 2}
    level[field] = value
    source = _contract()
    source["providers"]["coinglass"]["pair_maps"] = {"A": _dataset(collection="levels", values=[level])}
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.pair_maps\.A\.levels\[0\]"):
        process_long_short_liquidations(source)


def test_pair_map_level_must_be_mapping():
    source = _contract()
    source["providers"]["coinglass"]["pair_maps"] = {"A": _dataset(collection="levels", values=["bad"])}
    with pytest.raises(ValueError, match="invalid_input_contract:"):
        process_long_short_liquidations(source)


@pytest.mark.parametrize(("field", "value"), [("provider_price", True), ("long_max_pain_liquidation_price", float("nan")),
                                               ("short_max_pain_liquidation_level", -1)])
def test_max_pain_deep_validation(field, value):
    record = {"provider_price": 100, "long_max_pain_liquidation_price": 99, "long_max_pain_liquidation_level": 1,
              "short_max_pain_liquidation_price": 101, "short_max_pain_liquidation_level": 1}
    record[field] = value
    source = _contract()
    source["providers"]["coinglass"]["max_pain"] = _dataset(values=[record])
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.coinglass\.max_pain\.records\[0\]"):
        process_long_short_liquidations(source)


def test_max_pain_record_must_be_mapping():
    source = _contract()
    source["providers"]["coinglass"]["max_pain"] = _dataset(values=["bad"])
    with pytest.raises(ValueError, match="invalid_input_contract:"):
        process_long_short_liquidations(source)


def test_confirmation_datasets_deep_validation():
    source = _contract()
    source["providers"]["cryptoquant"] = {"aggregate_history": _dataset(
        values=[{"timestamp": "bad", "long_liquidations_usd": 1, "short_liquidations_usd": 1}], interval="1h")}
    with pytest.raises(ValueError, match=r"invalid_input_contract:providers\.cryptoquant\.aggregate_history\.records\[0\]"):
        process_long_short_liquidations(source)
    source = _contract()
    source["providers"]["glassnode"] = {name: _dataset(values=[{"timestamp": T, "value": True}],
        unit="percent" if name == "long_liquidation_dominance" else "USD", interval="1h") for name in
        ("long_liquidations", "short_liquidations", "total_liquidations", "long_liquidation_dominance")}
    with pytest.raises(ValueError, match="invalid_input_contract:providers.glassnode"):
        process_long_short_liquidations(source)


@pytest.mark.parametrize(("context", "reason"), [(None, "missing_reference_price"), (_price_context(T - 121), "stale_reference_price"),
                                                   (_price_context(T + 1), "future_reference_price"),
                                                   ({"value": 100}, "invalid_reference_price_context")])
def test_bucket_envelope_without_valid_reference(context, reason):
    output = process_long_short_liquidations(_contract(), reference_price_context=context)
    assert output["maps"]["aggregated"]["buckets"] == {"status": "unavailable", "reason": reason, "items": []}
    assert output["maps"]["aggregated"]["estimated_side_imbalance"]["reason"] == reason


def test_bucket_envelope_with_reference_and_json_inmutability():
    source, reference = _contract(), _price_context()
    before = deepcopy((source, reference))
    output = process_long_short_liquidations(source, reference_price_context=reference)
    assert output["maps"]["aggregated"]["buckets"]["status"] in {"available", "partial"}
    assert isinstance(output["maps"]["aggregated"]["buckets"]["items"], list)
    assert (source, reference) == before
    json.dumps(output, ensure_ascii=False, allow_nan=False)
