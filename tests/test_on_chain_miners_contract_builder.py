from __future__ import annotations

import copy
import json
import math

import pytest

from processing_signals.classification.on_chain_miners.on_chain_miners_classifier import classify_on_chain_miners
from processing_signals.classification.on_chain_miners.on_chain_miners_contract_builder import (
    CHART_IDS,
    DRILLDOWN_IDS,
    OPTIONAL_UNAVAILABLE,
    RANGE_DAYS,
    RANGE_OPTIONS,
    WIDGET_IDS,
    OnChainMinersContractBuilder,
    build_on_chain_miners_screen_contract,
    format_miner_reserve,
    format_currency,
    format_net_position,
    format_one_decimal,
    format_sopr,
)
from processing_signals.processing.on_chain_miners.on_chain_miners_processor import process_on_chain_miners
from test_on_chain_miners_processing_vertical import input_contract


DAY = 86_400


@pytest.fixture
def processing():
    return process_on_chain_miners(input_contract())


@pytest.fixture
def classification(processing):
    return classify_on_chain_miners(processing)


@pytest.fixture
def contract(processing, classification):
    return build_on_chain_miners_screen_contract(processing, classification)


def _keys(value):
    output = set()
    if isinstance(value, dict):
        output.update(value)
        for child in value.values():
            output.update(_keys(child))
    elif isinstance(value, list):
        for child in value:
            output.update(_keys(child))
    return output


@pytest.mark.parametrize(("path", "expected"), [
    (("schema", "id"), "trad_elatin.on_chain_miners.screen.v1"),
    (("schema", "version"), "1.0.0"),
    (("screen", "id"), "on_chain_miners"),
    (("screen", "route"), "/on-chain-miners"),
    (("screen", "title"), "ON-CHAIN & MINERS METRICS"),
    (("screen", "family"), "on_chain_miners"),
    (("stage",), "screen_contract"),
    (("mode",), "bootstrap"),
])
def test_contract_identity(path, expected, contract):
    value = contract
    for part in path:
        value = value[part]
    assert value == expected


@pytest.mark.parametrize("field", ["schema", "screen", "stage", "mode", "context", "range_selector", "operational_status", "charts", "widgets", "drilldowns", "quality"])
def test_top_level_structure(field, contract):
    assert field in contract


def test_exact_core_component_counts(contract):
    assert tuple(contract["charts"]) == CHART_IDS
    assert tuple(contract["widgets"]) == WIDGET_IDS
    assert tuple(contract["drilldowns"]) == DRILLDOWN_IDS


@pytest.mark.parametrize("forbidden", ["raw_response", "raw", "api_key", "authorization", "fetcher", "input_contract", "processing_contract",
                                               "classification_contract", "sopr_7d_calculated", "reserve_delta_calculated", "slope_calculated",
                                               "r_squared_calculated", "coverage_ratio_calculated", "hashrate_calculated", "difficulty_calculated"])
def test_forbidden_real_keys_are_absent(forbidden, contract):
    assert forbidden not in _keys(contract)


@pytest.mark.parametrize("field", ["asset", "data_mode", "is_demo", "reference_timestamp", "execution_timestamp", "generated_at"])
def test_context_preserves_processing_values(field, processing, contract):
    assert contract["context"][field] == processing["context"][field]


def test_context_data_as_of_fields(processing, classification, contract):
    assert contract["context"]["processing_data_as_of"] == processing["quality"]["data_as_of"]
    assert contract["context"]["classification_data_as_of"] == classification["quality"]["data_as_of"]
    assert contract["context"]["data_as_of"] == min(processing["quality"]["data_as_of"], classification["quality"]["data_as_of"])


@pytest.mark.parametrize("field", ["mode", "asset", "data_mode", "is_demo", "reference_timestamp", "execution_timestamp", "generated_at"])
def test_each_upstream_context_mismatch_is_explicit(field, processing, classification):
    if field == "mode":
        classification[field] = "recovery"
    else:
        classification["context"][field] = "different"
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"]["status"] == "invalid"
    assert f"upstream_context_mismatch:{field}" in result["quality"]["errors"]


def test_reference_and_execution_do_not_drive_data_as_of(processing, classification):
    expected = processing["quality"]["data_as_of"]
    processing["context"]["reference_timestamp"] = classification["context"]["reference_timestamp"] = expected + 100 * DAY
    processing["context"]["execution_timestamp"] = classification["context"]["execution_timestamp"] = expected + 200 * DAY
    assert build_on_chain_miners_screen_contract(processing, classification)["context"]["data_as_of"] == expected


def test_range_selector_is_daily_and_non_intraday(contract):
    assert contract["range_selector"] == {"options": [{"id": key, "days": RANGE_DAYS[key]} for key in RANGE_OPTIONS], "default": "30D",
                                           "source_resolution": "1D", "intraday_available": False}


@pytest.mark.parametrize("chart_id", CHART_IDS)
@pytest.mark.parametrize("range_id", RANGE_OPTIONS)
def test_every_chart_range_is_calendar_anchored(chart_id, range_id, contract):
    payload = contract["charts"][chart_id]["series_by_range"][range_id]
    days = RANGE_DAYS[range_id]
    anchor = contract["context"]["data_as_of"]
    assert payload["range_id"] == range_id and payload["days"] == days
    assert payload["from_timestamp"] == anchor - (days - 1) * DAY and payload["to_timestamp"] == anchor
    assert payload["expected_points"] == days
    if range_id == "360D":
        assert payload["actual_points"] <= days
        assert payload["coverage_ratio"] == payload["actual_points"] / days
        assert payload["status"] in {"available", "partial"}
    else:
        assert payload["actual_points"] == days
        assert payload["coverage_ratio"] == 1.0 and payload["status"] == "available"
    assert all(payload["from_timestamp"] <= point["timestamp"] <= anchor for point in payload["points"])
    assert len({point["timestamp"] for point in payload["points"]}) == len(payload["points"])


def test_calendar_range_does_not_select_last_n_records(processing, classification):
    records = processing["series"]["miner_reserve_btc"]["records"]
    records[-2]["timestamp"] -= 10 * DAY
    result = build_on_chain_miners_screen_contract(processing, classification)
    payload = result["charts"]["miner_reserve"]["series_by_range"]["7D"]
    assert payload["actual_points"] == 6 and payload["status"] == "partial" and payload["coverage_ratio"] == pytest.approx(6 / 7)


def test_ranges_do_not_fill_interpolate_or_repeat_gaps(processing, classification):
    records = processing["series"]["hashrate_eh_s"]["records"]
    removed = records.pop(-3)
    result = build_on_chain_miners_screen_contract(processing, classification)
    points = result["charts"]["hashrate"]["series_by_range"]["7D"]["points"]
    assert removed["timestamp"] not in {point["timestamp"] for point in points} and len(points) == 6


def test_coverage_ratio_is_capped_at_one(processing, classification):
    series = processing["series"]["miner_reserve_btc"]
    series["records"].append(copy.deepcopy(series["records"][-1]))
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["charts"]["miner_reserve"]["series_by_range"]["1D"]["coverage_ratio"] == 1.0


def test_null_data_as_of_empties_all_ranges(processing, classification):
    processing["quality"]["data_as_of"] = None
    result = build_on_chain_miners_screen_contract(processing, classification)
    for chart in result["charts"].values():
        for payload in chart["series_by_range"].values():
            assert payload["status"] == "unavailable" and payload["points"] == []
            assert payload["reason"] == "screen_data_as_of_unavailable"


def test_invalid_source_makes_all_its_ranges_invalid(processing, classification):
    processing["series"]["difficulty_t"]["status"] = "invalid"
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert all(payload["status"] == "invalid" for payload in result["charts"]["difficulty"]["series_by_range"].values())


@pytest.mark.parametrize(("chart_id", "series_id", "title", "subtitle", "chart_type", "unit", "provider"), [
    ("miner_reserve", "miner_reserve_btc", "Miner Reserve (BTC)", "Total miner-held BTC", "area", "BTC", "Glassnode"),
    ("sopr_7d", "sopr_7d", "SOPR (7D)", "Spent Output Profit Ratio", "line", "ratio", "CryptoQuant"),
    ("hashrate", "hashrate_eh_s", "Hashrate (EH/s)", "Network hash rate", "area", "EH/s", "Glassnode"),
    ("difficulty", "difficulty_t", "Difficulty (T)", "Network difficulty", "line", "T", "CryptoQuant"),
    ("miner_net_position_change", "miner_net_position_change", "Miner Net Position Change (BTC)", "Daily miner reserve delta", "bar", "BTC/day", "Derived"),
])
def test_chart_mapping_and_current_are_exact(chart_id, series_id, title, subtitle, chart_type, unit, provider, processing, contract):
    chart = contract["charts"][chart_id]
    assert (chart["title"], chart["subtitle"], chart["chart_type"], chart["unit"], chart["provider"]) == (title, subtitle, chart_type, unit, provider)
    assert chart["current"]["value"] == processing["series"][series_id]["current"]["value"]
    assert chart["current"]["timestamp"] == processing["series"][series_id]["current"]["timestamp"]


def test_sopr_reference_line_and_net_position_provenance(contract):
    assert contract["charts"]["sopr_7d"]["reference_lines"] == [{"value": 1.0, "label": "Breakeven", "token": "neutral"}]
    net = contract["charts"]["miner_net_position_change"]
    assert net["source_provider"] == "Glassnode" and net["calculation_source"] == "miner_reserve_btc"


def test_current_is_not_reconstructed_from_visible_points(processing, classification):
    series = processing["series"]["miner_reserve_btc"]
    series["current"]["value"] = 123.0
    series["records"][-1]["value"] = 999.0
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["charts"]["miner_reserve"]["current"]["value"] == 123.0
    assert result["charts"]["miner_reserve"]["series_by_range"]["1D"]["points"][0]["value"] == 999.0


def test_unavailable_current_is_not_zero(processing, classification):
    processing["series"]["difficulty_t"]["current"] = {"status": "unavailable", "value": None, "reason": "missing"}
    result = build_on_chain_miners_screen_contract(processing, classification)["charts"]["difficulty"]["current"]
    assert result["timestamp"] is None and result["value"] is None and result["display_value"] == "--"


@pytest.mark.parametrize(("formatter", "value", "expected"), [
    (format_miner_reserve, 1_874_853.91, "1.87M"), (format_miner_reserve, 874_853.9, "874,853.9"),
    (format_sopr, 1.0364, "1.036"), (format_one_decimal, 682.9, "682.9"), (format_one_decimal, 94.6, "94.6"),
    (format_net_position, 2242, "+2,242"), (format_net_position, -923, "-923"), (format_net_position, 0, "0"),
    (format_net_position, 10.25, "+10.25"),
])
def test_deterministic_display_formatting(formatter, value, expected):
    assert formatter(value) == expected


@pytest.mark.parametrize(("value", "token"), [(10, "positive"), (-5, "negative"), (0, "neutral")])
def test_net_position_bar_tokens_have_no_semantics(value, token, processing, classification):
    series = processing["series"]["miner_net_position_change"]
    series["records"][-1]["value"] = value
    point = build_on_chain_miners_screen_contract(processing, classification)["charts"]["miner_net_position_change"]["series_by_range"]["1D"]["points"][0]
    assert point == {"timestamp": series["records"][-1]["timestamp"], "value": value, "bar_token": token, "unit": "BTC/day"}
    assert "state" not in point and "signal" not in point


@pytest.mark.parametrize("widget_id", WIDGET_IDS)
@pytest.mark.parametrize("field", ["status", "state", "signal", "classification_label", "display_color_token", "source", "thresholds", "reason"])
def test_widgets_copy_classification_without_reclassification(widget_id, field, classification, contract):
    source_field = "display_label" if field == "classification_label" else field
    assert contract["widgets"][widget_id][field] == classification["classifications"][widget_id][source_field]


def test_net_position_widget_displays_numeric_classification_source(classification, processing):
    item = classification["classifications"]["net_position"]
    item["source"]["value"] = 10.25
    result = build_on_chain_miners_screen_contract(processing, classification)["widgets"]["net_position"]
    assert result["raw_value"] == 10.25 and result["display_value"] == "+10.25" and result["unit"] == "BTC/day"


@pytest.mark.parametrize("status", ["unavailable", "invalid"])
def test_unavailable_or_invalid_widget_never_becomes_neutral(status, processing, classification):
    item = classification["classifications"]["miner_pressure"]
    item.update({"status": status, "state": None, "signal": None})
    result = build_on_chain_miners_screen_contract(processing, classification)["widgets"]["miner_pressure"]
    assert result["display_value"] == "--" and result["state"] is None and result["signal"] is None


@pytest.mark.parametrize("widget_id", WIDGET_IDS)
def test_widget_tokens_are_approved_and_not_css(widget_id, contract):
    token = contract["widgets"][widget_id]["display_color_token"]
    assert token in {"positive", "negative", "warning", "neutral", "unavailable", "invalid"}
    assert not token.startswith(("#", "rgb", "rgba", "var("))


@pytest.mark.parametrize("drilldown_id", DRILLDOWN_IDS)
def test_drilldowns_are_real_and_available(drilldown_id, contract):
    item = contract["drilldowns"][drilldown_id]
    assert item["drilldown_id"] == drilldown_id and item["status"] == "available" and item["enabled"] is True
    serialized = json.dumps(item)
    assert "data_source_not_implemented" not in serialized and "optional_enrichment_not_available" not in serialized


def test_required_drilldowns_are_available_and_not_optional(contract):
    assert contract["quality"]["status"] == "ok"
    assert contract["quality"]["optional_unavailable"] == list(OPTIONAL_UNAVAILABLE) == []
    assert set(contract["quality"]["availability"]["drilldowns"]) == set(DRILLDOWN_IDS)


@pytest.mark.parametrize(("field", "expected"), [("connection_status", "not_reported"), ("cache_status", "not_reported")])
def test_operational_status_does_not_invent_connectivity(field, expected, contract):
    assert contract["operational_status"][field] == expected


@pytest.mark.parametrize("field", ["data_mode", "is_demo", "generated_at"])
def test_operational_status_preserves_context(field, contract):
    assert contract["operational_status"][field] == contract["context"][field]


def test_quality_ok_has_exact_availability(contract):
    assert contract["quality"]["status"] == "ok" and contract["quality"]["data_as_of"] is not None
    assert set(contract["quality"]["availability"]["charts"]) == set(CHART_IDS)
    assert set(contract["quality"]["availability"]["widgets"]) == set(WIDGET_IDS)
    assert not contract["quality"]["errors"]


@pytest.mark.parametrize("upstream", ["processing", "classification"])
def test_upstream_partial_bounds_screen_quality(upstream, processing, classification):
    target = processing if upstream == "processing" else classification
    target["quality"]["status"] = "partial"
    target["quality"]["warnings"] = ["upstream_partial"]
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"]["status"] == "partial" and result["quality"]["warnings"]


@pytest.mark.parametrize("upstream", ["processing", "classification"])
def test_upstream_invalid_returns_invalid_data_as_of(upstream, processing, classification):
    target = processing if upstream == "processing" else classification
    target["quality"]["status"] = "invalid"
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"]["status"] == "invalid" and result["quality"]["data_as_of"] is None


@pytest.mark.parametrize(("upstream", "field", "prefix"), [("processing", "warnings", "processing_warning:"),
                                                              ("processing", "errors", "processing_error:"),
                                                              ("classification", "warnings", "classification_warning:"),
                                                              ("classification", "errors", "classification_error:")])
def test_upstream_messages_are_prefixed(upstream, field, prefix, processing, classification):
    target = processing if upstream == "processing" else classification
    target["quality"][field] = ["message", "message"]
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"][field].count(f"{prefix}message") == 1


def test_records_change_points_without_changing_current(processing, classification):
    processing["series"]["hashrate_eh_s"]["records"][-1]["value"] = 999
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["charts"]["hashrate"]["series_by_range"]["1D"]["points"][0]["value"] == 999
    assert result["charts"]["hashrate"]["current"]["value"] != 999


def test_processing_changes_do_not_reclassify_widgets(processing, classification):
    expected = copy.deepcopy(classification["classifications"]["reserve_trend"])
    processing["features"]["reserve_trend"]["windows"]["30d"]["normalized_slope_percent_per_day"] = -999
    result = build_on_chain_miners_screen_contract(processing, classification)["widgets"]["reserve_trend"]
    assert result["state"] == expected["state"] and result["source"] == expected["source"]


def test_no_sopr_hashrate_difficulty_or_delta_recalculation(processing, classification):
    processing["series"]["sopr_7d"]["records"][-1]["value"] = 77
    processing["series"]["hashrate_eh_s"]["records"][-1]["value"] = 682.9
    processing["series"]["difficulty_t"]["records"][-1]["value"] = 94.6
    processing["series"]["miner_net_position_change"]["records"][-1]["value"] = 123
    result = build_on_chain_miners_screen_contract(processing, classification)["charts"]
    assert result["sopr_7d"]["series_by_range"]["1D"]["points"][0]["value"] == 77
    assert result["hashrate"]["series_by_range"]["1D"]["points"][0]["value"] == 682.9
    assert result["difficulty"]["series_by_range"]["1D"]["points"][0]["value"] == 94.6
    assert result["miner_net_position_change"]["series_by_range"]["1D"]["points"][0]["value"] == 123


def test_upstreams_are_immutable_and_nested_outputs_are_new(processing, classification):
    p_copy, c_copy = copy.deepcopy(processing), copy.deepcopy(classification)
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert processing == p_copy and classification == c_copy
    assert result["charts"]["miner_reserve"]["current"] is not processing["series"]["miner_reserve_btc"]["current"]
    assert result["charts"]["miner_reserve"]["series_by_range"]["30D"]["points"][0] is not processing["series"]["miner_reserve_btc"]["records"][-30]
    assert result["widgets"]["miner_pressure"]["source"] is not classification["classifications"]["miner_pressure"]["source"]
    assert result["widgets"]["miner_pressure"]["thresholds"] is not classification["classifications"]["miner_pressure"]["thresholds"]


def test_normal_and_invalid_outputs_are_strict_json(processing, classification):
    assert json.dumps(build_on_chain_miners_screen_contract(processing, classification), ensure_ascii=False, allow_nan=False)
    processing["family"] = "wrong"
    assert json.dumps(build_on_chain_miners_screen_contract(processing, classification), ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -0.0])
def test_nonfinite_and_negative_zero_never_escape(value, processing, classification):
    processing["series"]["miner_reserve_btc"]["records"][-1]["value"] = value
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)
    if not math.isfinite(value):
        assert result["quality"]["status"] == "invalid" and result["quality"]["data_as_of"] is None
    else:
        published = result["charts"]["miner_reserve"]["series_by_range"]["1D"]["points"][0]["value"]
        assert published == 0 and math.copysign(1, published) > 0


def test_nonserializable_object_returns_complete_clean_fallback(processing, classification):
    classification["context"]["generated_at"] = processing["context"]["generated_at"] = object()
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert set(result) == {"schema", "screen", "stage", "mode", "context", "range_selector", "operational_status", "charts", "widgets", "drilldowns", "quality"}
    assert result["quality"]["status"] == "invalid" and len(result["charts"]) == 5 and len(result["widgets"]) == 4
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_public_facade_matches_builder(processing, classification):
    assert build_on_chain_miners_screen_contract(processing, classification) == OnChainMinersContractBuilder(processing, classification).build()


def test_outflow_drilldown_copies_processing_aggregate_shares_ranks_and_current(processing, classification):
    feature = processing["features"]["miner_outflow_distribution"]
    source_record = feature["records"][-1]
    source_record["aggregate_outflow_total_btc"] = 2_242.0
    source_record["top1_share_ratio"] = 0.123456
    source_record["top3_share_ratio"] = 0.654321
    source_record["pools"][0]["rank"] = 77
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["miner_outflow_distribution"]
    assert item["current"]["aggregate_outflow_total_btc"] == 2_242.0 and item["current"]["display_value"] == "+2,242"
    assert item["current"]["top1_share_ratio"] == 0.123456 and item["current"]["top3_share_ratio"] == 0.654321
    assert item["current"]["pools"][0]["rank"] == 77
    assert item["series_by_range"]["1D"]["points"][0] == source_record


@pytest.mark.parametrize("status", ["unavailable", "invalid"])
def test_outflow_unavailable_or_invalid_is_disabled(status, processing, classification):
    feature = processing["features"]["miner_outflow_distribution"]
    feature["status"] = status
    feature["current"] = {"status": status, "value": None}
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["miner_outflow_distribution"]
    assert item["status"] == status and item["enabled"] is False
    assert item["current"]["aggregate_outflow_total_btc"] is None and item["current"]["display_value"] == "--"


def test_partial_drilldown_with_usable_current_remains_enabled(processing, classification):
    processing["features"]["miner_outflow_distribution"]["status"] = "partial"
    processing["features"]["miner_outflow_distribution"]["current"]["status"] = "partial"
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["miner_outflow_distribution"]
    assert item["status"] == "partial" and item["enabled"] is True


def test_reserve_age_drilldown_preserves_semantic_separation_and_thirteen_bands(contract, processing):
    item = contract["drilldowns"]["reserve_aging"]
    assert item["title"] == "Reserve Age Context"
    assert item["miner_specific"]["scope"] == "miner_specific"
    assert item["network_context"]["scope"] == "bitcoin_network" and item["network_context"]["is_miner_specific"] is False
    assert item["semantic_scope"]["network_context_is_miner_specific"] is False
    snapshot = item["network_context"]["snapshots_by_range"]["1D"]["snapshots"][0]
    assert len(snapshot["bands"]) == 13
    assert snapshot["bands"] == processing["features"]["reserve_age_context"]["network_context"]["records"][-1]["bands"]


def test_miner_unspent_current_is_copied_without_reconstruction(contract, processing):
    assert contract["drilldowns"]["reserve_aging"]["miner_specific"]["current"] == processing["series"]["miners_unspent_supply_btc"]["current"]


def test_revenue_drilldown_copies_processing_record_and_scale_without_recalculation(processing, classification):
    feature = processing["features"]["miner_revenue_breakdown"]
    record = feature["records"][-1]
    record.update({"total_revenue_usd": 2_000_000.0, "block_reward_revenue_usd": 1.0, "fee_revenue_usd": 123_456.0,
                   "derived_fee_share_ratio": 0.125, "derived_fee_share_percent": 12.5, "provider_fee_scale": "unresolved",
                   "provider_fee_ratio": 0.777, "provider_fee_difference_ratio": 0.321})
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["revenue_breakdown"]
    assert item["current"]["fee_revenue_usd"] == 123_456.0
    assert item["current"]["provider_fee_scale"] == "unresolved" and item["current"]["provider_fee_ratio"] == 0.777
    assert item["current"]["display"] == {"total_revenue": "$2.00M", "block_reward_revenue": "$1.00",
                                            "fee_revenue": "$123,456", "fee_share": "12.50%"}
    assert item["series_by_range"]["1D"]["points"][0] == record


@pytest.mark.parametrize(("value", "expected"), [(1_250_000.0, "$1.25M"), (125_000.0, "$125,000"), (850.25, "$850.25")])
def test_revenue_currency_formatting(value, expected):
    assert format_currency(value) == expected


def test_nupl_drilldown_uses_classification_and_preserves_basis_values(processing, classification):
    phase = classification["classifications"]["nupl_phase"]
    phase["state"], phase["signal"], phase["display_label"] = "custom_phase", "custom_signal", "CUSTOM"
    phase["source"]["previous"] = {"status": "available", "value": -100}
    phase["source"]["change_1d"] = 999
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["nupl_phases"]
    assert item["phase"]["state"] == "custom_phase" and item["phase"]["signal"] == "custom_signal"
    assert item["phase"]["classification_label"] == "CUSTOM"
    assert item["phase"]["previous"] == {"status": "available", "value": -100} and item["phase"]["change_1d"] == 999
    assert item["phase"]["thresholds"] == phase["thresholds"]


def test_nupl_display_format_preserves_ratio_precision_and_price_cents(processing, classification):
    processing["features"]["nupl_phase_basis"]["current"].update({"value": 0.58642, "price_usd": 118_400.25})
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["nupl_phases"]
    assert item["current"]["display_value"] == "0.586" and item["current"]["display_price"] == "$118,400.25"


def test_nupl_history_contains_no_per_point_semantics(contract):
    points = contract["drilldowns"]["nupl_phases"]["series_by_range"]["90D"]["points"]
    assert points and all(set(point) == {"timestamp", "value", "price_usd"} for point in points)
    assert all({"state", "signal", "display_color_token"}.isdisjoint(point) for point in points)


def test_nupl_phase_bands_copy_classification_thresholds(contract):
    item = contract["drilldowns"]["nupl_phases"]
    assert [band["state"] for band in item["phase_bands"]] == ["capitulation", "hope_fear", "optimism_anxiety", "belief_denial", "euphoria_greed"]
    assert item["phase_bands"][0]["maximum"] == item["phase"]["thresholds"]["capitulation_max"]
    assert item["phase_bands"][-1]["minimum"] == item["phase"]["thresholds"]["belief_denial_max"]


@pytest.mark.parametrize("drilldown_id", DRILLDOWN_IDS)
@pytest.mark.parametrize("range_id", RANGE_OPTIONS)
def test_every_drilldown_range_is_calendar_anchored(drilldown_id, range_id, contract):
    item = contract["drilldowns"][drilldown_id]
    ranges = (item["miner_specific"]["series_by_range"] if drilldown_id == "reserve_aging" else item["series_by_range"])
    payload = ranges[range_id]
    assert payload["to_timestamp"] == contract["context"]["data_as_of"]
    assert payload["from_timestamp"] == payload["to_timestamp"] - (RANGE_DAYS[range_id] - 1) * DAY
    assert payload["expected_points"] == RANGE_DAYS[range_id]


def test_drilldown_ranges_do_not_fill_or_interpolate(processing, classification):
    records = processing["features"]["miner_outflow_distribution"]["records"]
    removed = records.pop(-2)["timestamp"]
    item = build_on_chain_miners_screen_contract(processing, classification)["drilldowns"]["miner_outflow_distribution"]
    timestamps = [point["timestamp"] for point in item["series_by_range"]["7D"]["points"]]
    assert removed not in timestamps and len(timestamps) == len(set(timestamps))


def test_unavailable_drilldown_degrades_quality_to_partial(processing, classification):
    feature = processing["features"]["miner_outflow_distribution"]
    feature["status"] = "unavailable"
    feature["current"] = {"status": "unavailable", "value": None}
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"]["status"] == "partial"
    assert result["quality"]["availability"]["drilldowns"]["miner_outflow_distribution"] == "unavailable"
    assert "miner_outflow_distribution" in result["quality"]["missing_fields"]


def test_all_four_available_drilldowns_allow_quality_ok(contract):
    assert contract["quality"]["status"] == "ok"
    assert contract["quality"]["availability"]["drilldowns"] == {drilldown_id: "available" for drilldown_id in DRILLDOWN_IDS}
    assert contract["quality"]["optional_unavailable"] == []


def test_drilldown_nested_objects_are_independent(processing, classification):
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["drilldowns"]["miner_outflow_distribution"]["current"]["pools"] is not processing["features"]["miner_outflow_distribution"]["records"][-1]["pools"]
    assert result["drilldowns"]["reserve_aging"]["network_context"]["snapshots_by_range"]["1D"]["snapshots"][0]["bands"] is not \
        processing["features"]["reserve_age_context"]["network_context"]["records"][-1]["bands"]
    assert result["drilldowns"]["nupl_phases"]["phase"]["thresholds"] is not classification["classifications"]["nupl_phase"]["thresholds"]


def test_drilldown_nonfinite_auxiliary_returns_strict_invalid_contract(processing, classification):
    processing["features"]["miner_outflow_distribution"]["records"][-1]["top1_share_ratio"] = math.nan
    result = build_on_chain_miners_screen_contract(processing, classification)
    assert result["quality"]["status"] == "invalid" and result["quality"]["data_as_of"] is None
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_no_placeholder_recalculation_or_io_contract_fields(contract):
    forbidden = {"nupl_phase_calculated", "fee_revenue_calculated", "pool_share_calculated", "reserve_age_score", "input_contract",
                 "processing_contract", "classification_contract", "raw_response", "fetcher"}
    assert forbidden.isdisjoint(_keys(contract))
    serialized = json.dumps(contract)
    assert "data_source_not_implemented" not in serialized and "optional_enrichment_not_available" not in serialized
