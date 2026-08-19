from __future__ import annotations

import copy
import json
import math
import runpy
from pathlib import Path

import pytest

from processing_signals.classification.open_interest_and_funding.open_interest_and_funding_classifier import (
    OpenInterestAndFundingClassifier,
    classify_open_interest_and_funding,
)
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_processor import (
    process_open_interest_and_funding,
)

FAMILY = "open_interest_and_funding"
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
ATOM_TYPES = (
    "open_interest_change_state", "funding_state", "oi_funding_quadrant", "oi_trend_strength",
    "directional_index_relation", "macd_relation", "stochastic_range_state", "bollinger_position",
    "cci_state", "oi_roc_state",
)
PROCESSING_TEST = Path(__file__).with_name("test_open_interest_and_funding_processing_vertical.py")
CLASSIFIER = Path(__file__).parents[1] / "src/processing_signals/classification/open_interest_and_funding/open_interest_and_funding_classifier.py"
_INPUT = runpy.run_path(str(PROCESSING_TEST))["_input"]
BASE = process_open_interest_and_funding(_INPUT())


def _contract() -> dict:
    return copy.deepcopy(BASE)


def _output(contract: dict | None = None) -> dict:
    return classify_open_interest_and_funding(contract or _contract())


def _current(output: dict, timeframe: str = "1h") -> dict:
    return output["classifications"]["by_timeframe"][timeframe]["current"]


def _oi_change(contract: dict, timeframe: str = "1h") -> dict:
    return contract["series"]["open_interest_ohlc"]["timeframes"][timeframe]["derived"]["oi_change_24h"]


def _indicator(contract: dict, name: str, timeframe: str = "1h") -> dict:
    return contract["indicators"]["open_interest"]["timeframes"][timeframe][name]


def _set_oi(contract: dict, value: float, timeframe: str = "1h", timestamp: int = 1_800_000_000,
            status: str = "available") -> None:
    wrapper = _oi_change(contract, timeframe)
    wrapper.update(status=status, reason=None if status == "available" else "classification_source_partial",
                   current={"change_absolute_usd": value, "change_percent": value}, current_timestamp=timestamp)


def _set_funding(contract: dict, value: float, timeframe: str = "1h", timestamp: int = 1_800_000_000,
                 status: str = "available") -> None:
    frame = contract["series"]["funding_rate_ohlc"]["timeframes"][timeframe]
    frame.update(status=status, reason=None if status == "available" else "classification_source_partial",
                 unit="percent_points", representation="percentage_points",
                 current={"timestamp": timestamp, "open": value, "high": value, "low": value, "close": value})


def _set_indicator(contract: dict, name: str, values: dict[str, float], timeframe: str = "1h",
                   timestamp: int = 1_800_000_000) -> None:
    wrapper = _indicator(contract, name, timeframe)
    wrapper.update(status="available", reason=None, current=values, current_timestamp=timestamp)


def _empty_events(contract: dict) -> None:
    contract["events"] = {"by_id": {}, "timeframes": {timeframe: {"event_ids": []} for timeframe in TIMEFRAMES}}


def _single_macd_event(contract: dict, timestamp: int) -> str:
    _empty_events(contract)
    event_id = f"{FAMILY}:1h:{timestamp}:macd_signal_cross:macd_x_signal"
    event = {"event_id": event_id, "event_type": "macd_signal_cross", "timestamp": timestamp, "timeframe": "1h",
             "source_metric": "open_interest_ohlc", "first_series": "macd", "second_series": "signal",
             "threshold": None, "direction_numeric": 1, "previous_difference": -1.0,
             "current_difference": 1.0, "values": {"macd": 1.0, "signal": 0.0}, "parameters": {}}
    contract["events"]["by_id"][event_id] = event
    contract["events"]["timeframes"]["1h"]["event_ids"] = [event_id]
    return event_id


def test_functional_class_and_bundle_apis_are_equivalent():
    contract = _contract()
    direct = classify_open_interest_and_funding(contract)
    assert OpenInterestAndFundingClassifier().classify(contract) == direct
    assert classify_open_interest_and_funding({"processing": contract}) == direct


def test_exact_root_order_and_identity():
    output = _output()
    assert list(output) == ["family", "stage", "version", "mode", "context", "classifications",
                            "interpreted_events", "snapshots", "confirmations", "availability", "quality"]
    assert (output["family"], output["stage"], output["version"]) == (FAMILY, "classification", "0.1")


@pytest.mark.parametrize("value", [None, [], "processing", 1, object()])
def test_non_mapping_roots_are_value_errors(value):
    with pytest.raises(ValueError):
        classify_open_interest_and_funding(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(("field", "value"), [
    ("family", "prices_ohlcv"), ("stage", "input"), ("version", "1.0"), ("mode", "live")])
def test_invalid_root_identity_is_rejected(field, value):
    contract = _contract()
    contract[field] = value
    with pytest.raises(ValueError, match=field):
        _output(contract)


@pytest.mark.parametrize("section", ["context", "series", "indicators", "events", "snapshots",
                                      "confirmations", "availability", "quality"])
def test_incompatible_root_sections_are_rejected(section):
    contract = _contract()
    contract[section] = []
    with pytest.raises(ValueError):
        _output(contract)


def test_missing_extra_and_reordered_timeframes_are_rejected():
    for mutation in ("missing", "extra", "reordered"):
        contract = _contract()
        frames = contract["series"]["open_interest_ohlc"]["timeframes"]
        if mutation == "missing":
            frames.pop("1m")
        elif mutation == "extra":
            frames["2h"] = copy.deepcopy(frames["1h"])
        else:
            contract["series"]["open_interest_ohlc"]["timeframes"] = dict(reversed(frames.items()))
        with pytest.raises(ValueError, match="timeframes"):
            _output(contract)


def test_six_timeframes_and_ten_atoms_are_always_present():
    output = _output()
    frames = output["classifications"]["by_timeframe"]
    assert tuple(frames) == TIMEFRAMES
    assert all(tuple(frames[timeframe]["current"]) == ATOM_TYPES for timeframe in TIMEFRAMES)
    for timeframe in TIMEFRAMES:
        for name, atom in frames[timeframe]["current"].items():
            assert tuple(atom) == ("classification_id", "type", "status", "state", "reason", "evidence")
            assert atom["type"] == name


@pytest.mark.parametrize(("value", "state"), [(2.0, "expanding"), (-2.0, "contracting"), (0.0, "unchanged")])
def test_open_interest_change_states(value, state):
    contract = _contract()
    _set_oi(contract, value)
    assert _current(_output(contract))["open_interest_change_state"]["state"] == state


@pytest.mark.parametrize(("value", "state"), [(0.01, "positive"), (-0.01, "negative"), (0.0, "neutral")])
def test_funding_states_without_unit_conversion(value, state):
    contract = _contract()
    _set_funding(contract, value)
    atom = _current(_output(contract))["funding_state"]
    assert atom["state"] == state
    assert atom["evidence"]["units"] == {"funding_close": "percent_points"}


@pytest.mark.parametrize(("oi", "funding", "state"), [
    (1.0, 1.0, "positive_funding_expansion"), (1.0, -1.0, "negative_funding_expansion"),
    (1.0, 0.0, "neutral_funding_expansion"), (-1.0, 1.0, "positive_funding_contraction"),
    (-1.0, -1.0, "negative_funding_contraction"), (-1.0, 0.0, "neutral_funding_contraction"),
    (0.0, 1.0, "positive_funding_unchanged"), (0.0, -1.0, "negative_funding_unchanged"),
    (0.0, 0.0, "neutral_funding_unchanged"),
])
def test_all_nine_quadrants_preserve_funding_sign(oi, funding, state):
    contract = _contract()
    _set_oi(contract, oi)
    _set_funding(contract, funding)
    assert _current(_output(contract))["oi_funding_quadrant"]["state"] == state


@pytest.mark.parametrize(("value", "state"), [(1.0, "positive"), (-1.0, "negative"), (0.0, "neutral")])
def test_roc_states(value, state):
    contract = _contract()
    _set_indicator(contract, "oi_roc", {"roc": value})
    assert _current(_output(contract))["oi_roc_state"]["state"] == state


def test_unit_and_series_units_mismatches_are_local_invalid():
    contract = _contract()
    _oi_change(contract)["units"]["change_percent"] = "ratio"
    assert _current(_output(contract))["open_interest_change_state"]["reason"] == "classification_unit_mismatch"
    contract = _contract()
    _oi_change(contract)["units"].pop("change_percent")
    atom = _current(_output(contract))["open_interest_change_state"]
    assert (atom["status"], atom["reason"]) == ("invalid", "classification_series_units_mismatch")


def test_bool_timestamps_and_values_are_invalid_not_incidental_exceptions():
    contract = _contract()
    _oi_change(contract)["current_timestamp"] = True
    assert _current(_output(contract))["open_interest_change_state"]["status"] == "invalid"
    contract = _contract()
    contract["series"]["funding_rate_ohlc"]["timeframes"]["1h"]["current"]["close"] = True
    assert _current(_output(contract))["funding_state"]["status"] == "invalid"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_input_is_rejected_at_the_public_boundary(value):
    contract = _contract()
    _oi_change(contract)["current"]["change_percent"] = value
    with pytest.raises(ValueError, match="classification_input_invalid"):
        _output(contract)


def test_current_absence_propagates_real_processing_reason():
    contract = _contract()
    wrapper = _oi_change(contract)
    wrapper.update(status="partial", current=None, current_timestamp=None,
                   reason="insufficient_history_for_exact_24h_change")
    atom = _current(_output(contract))["open_interest_change_state"]
    assert (atom["status"], atom["state"], atom["reason"]) == (
        "unavailable", None, "insufficient_history_for_exact_24h_change")


def test_quadrant_timestamp_mismatch_is_unavailable():
    contract = _contract()
    _set_oi(contract, 1.0, timestamp=100)
    _set_funding(contract, 1.0, timestamp=101)
    atom = _current(_output(contract))["oi_funding_quadrant"]
    assert (atom["status"], atom["reason"]) == ("unavailable", "classification_timestamp_mismatch")


def test_partial_source_has_closed_reason_and_determinable_state():
    contract = _contract()
    _set_oi(contract, 1.0, status="partial")
    atom = _current(_output(contract))["open_interest_change_state"]
    assert (atom["status"], atom["state"], atom["reason"]) == (
        "partial", "expanding", "classification_source_partial")


def test_events_shape_types_pairs_references_and_order_are_preserved():
    output = _output()
    events = BASE["events"]
    assert set(event["event_type"] for event in events["by_id"].values()) <= {
        "moving_average_cross", "macd_signal_cross", "stochastic_cross", "directional_indicator_cross",
        "adx_threshold_cross", "oi_roc_zero_cross", "funding_zero_cross"}
    interpreted = output["interpreted_events"]
    assert len(interpreted["by_id"]) == len(events["by_id"])
    ordering = [(item["timestamp"], item["event_type"], item["event_id"]) for item in interpreted["by_id"].values()]
    assert ordering == sorted(ordering)
    assert all(item["evidence"]["source_event"] == events["by_id"][item["event_id"]]
               for item in interpreted["by_id"].values())


def test_all_seven_event_types_nine_pairs_and_exact_interpretations_are_wired():
    contract = _contract()
    _empty_events(contract)
    specifications = (
        ("moving_average_cross", "sma_20", "sma_50", None, "sma_20_crossed_above_sma_50"),
        ("moving_average_cross", "sma_50", "sma_100", None, "sma_50_crossed_above_sma_100"),
        ("moving_average_cross", "sma_100", "sma_200", None, "sma_100_crossed_above_sma_200"),
        ("macd_signal_cross", "macd", "signal", None, "macd_crossed_above_signal"),
        ("stochastic_cross", "k", "d", None, "k_crossed_above_d"),
        ("directional_indicator_cross", "di_plus", "di_minus", None, "di_plus_crossed_above_di_minus"),
        ("adx_threshold_cross", "adx", None, 25.0, "adx_crossed_above_25"),
        ("oi_roc_zero_cross", "roc", None, 0.0, "oi_roc_crossed_above_zero"),
        ("funding_zero_cross", "funding_close", None, 0.0, "funding_crossed_above_zero"),
    )
    ids = []
    expected_states = []
    for index, (event_type, first, second, threshold, state) in enumerate(specifications, start=1):
        timestamp = 1_700_000_000 + index
        pair = f"{first}_x_{second}" if second else (
            "oi_roc_12_x_0" if event_type == "oi_roc_zero_cross" else
            "funding_close_x_0" if event_type == "funding_zero_cross" else "adx_x_25")
        event_id = f"{FAMILY}:1h:{timestamp}:{event_type}:{pair}"
        contract["events"]["by_id"][event_id] = {
            "event_id": event_id, "event_type": event_type, "timestamp": timestamp, "timeframe": "1h",
            "source_metric": "funding_rate_ohlc" if event_type == "funding_zero_cross" else "open_interest_ohlc",
            "first_series": first, "second_series": second, "threshold": threshold, "direction_numeric": 1,
            "previous_difference": -1.0, "current_difference": 1.0, "values": {}, "parameters": {}}
        ids.append(event_id)
        expected_states.append(state)
    contract["events"]["timeframes"]["1h"]["event_ids"] = ids
    interpreted = _output(contract)["interpreted_events"]["by_id"].values()
    assert [item["state"] for item in interpreted] == expected_states
    assert {item["event_type"] for item in interpreted} == {
        "moving_average_cross", "macd_signal_cross", "stochastic_cross", "directional_indicator_cross",
        "adx_threshold_cross", "oi_roc_zero_cross", "funding_zero_cross"}


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_timeframe", "key_mismatch", "event_type"])
def test_invalid_event_graph_is_rejected_with_value_error(mutation):
    contract = _contract()
    timestamp = _indicator(contract, "macd")["current_timestamp"]
    event_id = _single_macd_event(contract, timestamp)
    if mutation == "missing":
        contract["events"]["timeframes"]["1h"]["event_ids"] = ["missing"]
    elif mutation == "duplicate":
        contract["events"]["timeframes"]["1h"]["event_ids"] = [event_id, event_id]
    elif mutation == "wrong_timeframe":
        contract["events"]["by_id"][event_id]["timeframe"] = "4h"
    elif mutation == "key_mismatch":
        contract["events"]["by_id"][event_id]["event_id"] = "other"
    else:
        contract["events"]["by_id"][event_id]["event_type"] = "unknown"
    with pytest.raises(ValueError):
        _output(contract)


def _interpreted_event(output: dict, source_event_id: str) -> dict:
    return next(item for item in output["interpreted_events"]["by_id"].values()
                if item["event_id"] == source_event_id)


def test_processing_quality_invalid_has_global_precedence_over_required_atoms():
    contract = _contract()
    contract["quality"].update(status="invalid", errors=["processing_contract_invalid"])
    output = _output(contract)
    assert output["quality"]["status"] == "invalid"
    assert output["quality"]["data_complete"] is False
    assert output["quality"]["processing_quality"] == contract["quality"]
    for timeframe in TIMEFRAMES:
        frame = output["classifications"]["by_timeframe"][timeframe]
        assert (frame["status"], frame["reason"]) == ("invalid", "classification_input_invalid")
        for name in ("open_interest_change_state", "funding_state", "oi_funding_quadrant"):
            atom = frame["current"][name]
            assert (atom["status"], atom["state"], atom["classification_id"]) == ("invalid", None, None)
            assert atom["reason"] == "processing_contract_invalid"


@pytest.mark.parametrize(("kind", "name"), [
    ("oi", "open_interest_change_state"), ("funding", "funding_state")])
def test_internal_current_timestamp_mismatch_is_local_invalid(kind, name):
    contract = _contract()
    if kind == "oi":
        wrapper = _oi_change(contract)
    elif kind == "funding":
        wrapper = contract["series"]["funding_rate_ohlc"]["timeframes"]["1h"]
        wrapper["current_timestamp"] = wrapper["current"]["timestamp"] + 1
    else:
        wrapper = _indicator(contract, kind)
    if kind != "funding":
        wrapper["current"] = {**wrapper["current"], "timestamp": wrapper["current_timestamp"] + 1}
    atom = _current(_output(contract))[name]
    assert (atom["status"], atom["state"], atom["reason"], atom["classification_id"]) == (
        "invalid", None, "classification_timestamp_mismatch", None)


@pytest.mark.parametrize("status", ["available", "partial"])
def test_provider_comparisons_are_forced_unavailable(status):
    contract = _contract()
    for metric in ("open_interest", "funding_rate"):
        contract["confirmations"]["comparisons"][metric] = {
            "status": status, "reason": None, "provider_state": f"provider_{status}", "metadata": {"kept": True}}
    comparisons = _output(contract)["confirmations"]["comparisons"]
    for payload in comparisons.values():
        assert payload == {"status": "unavailable", "reason": "provider_scope_not_proven_comparable",
                           "provider_state": "provider_unavailable", "metadata": {"kept": True}}


def test_incompatible_provider_comparisons_are_invalid_without_exception():
    contract = _contract()
    contract["confirmations"]["comparisons"] = []
    assert _output(contract)["confirmations"]["comparisons"] == {
        "status": "invalid", "reason": "classification_input_invalid", "provider_state": "provider_invalid"}


@pytest.mark.parametrize("direction", [1.0, -1.0, True, False, 0, 2, "1", None])
def test_direction_numeric_requires_exact_positive_or_negative_int(direction):
    contract = _contract()
    event_id = _single_macd_event(contract, _indicator(contract, "macd")["current_timestamp"])
    contract["events"]["by_id"][event_id]["direction_numeric"] = direction
    output = _output(contract)
    event = _interpreted_event(output, event_id)
    assert (event["status"], event["state"], event["reason"]) == (
        "invalid", None, "classification_event_invalid")
    assert f"classification_event_invalid:{event_id}" in output["quality"]["warnings"]
    assert output["quality"]["status"] != "invalid"
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)


@pytest.mark.parametrize("field", ["values", "parameters"])
@pytest.mark.parametrize("value", [[], "", None, 1, True, {"bad"}])
def test_event_values_and_parameters_require_json_safe_mappings(field, value):
    contract = _contract()
    event_id = _single_macd_event(contract, _indicator(contract, "macd")["current_timestamp"])
    contract["events"]["by_id"][event_id][field] = value
    output = _output(contract)
    event = _interpreted_event(output, event_id)
    assert (event["status"], event["state"], event["reason"]) == (
        "invalid", None, "classification_event_invalid")
    assert event["evidence"]["source_event"][field] == {}
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)


@pytest.mark.parametrize(("kind", "name"), [
    ("oi_change", "open_interest_change_state"), ("funding", "funding_state"),
    ("adx", "oi_trend_strength"), ("macd", "macd_relation"),
    ("stochastic", "stochastic_range_state"), ("bollinger_bands", "bollinger_position"),
    ("cci", "cci_state"), ("oi_roc", "oi_roc_state")])
@pytest.mark.parametrize("source", [{"timeframe": "4h"}, {}, [], {"timeframe": 1}, {"timeframe": True}])
def test_wrapper_source_timeframe_must_match_its_path(kind, name, source):
    contract = _contract()
    wrapper = (_oi_change(contract) if kind == "oi_change" else
               contract["series"]["funding_rate_ohlc"]["timeframes"]["1h"] if kind == "funding" else
               _indicator(contract, kind))
    wrapper["source"] = copy.deepcopy(source)
    atom = _current(_output(contract))[name]
    assert (atom["status"], atom["state"], atom["reason"]) == (
        "invalid", None, "classification_input_invalid")


def test_snapshots_are_exact_deep_copied_passthrough_and_invalid_is_isolated():
    contract = _contract()
    contract["snapshots"]["options_open_interest"].update(status="invalid", reason="snapshot_records_incompatible")
    before = copy.deepcopy(contract["snapshots"])
    output = _output(contract)
    assert output["snapshots"] == before
    output["snapshots"]["options_open_interest"]["status"] = "changed"
    assert contract["snapshots"]["options_open_interest"]["status"] == "invalid"
    assert output["quality"]["status"] != "invalid"


def test_confirmations_are_deep_copied_and_provider_statuses_are_interpreted():
    contract = _contract()
    statuses = ("available", "partial", "unavailable", "invalid")
    paths = (("open_interest", "cryptoquant"), ("open_interest", "glassnode"),
             ("funding_rate", "cryptoquant"), ("funding_rate", "glassnode"))
    for status, path in zip(statuses, paths):
        contract["confirmations"][path[0]][path[1]]["status"] = status
    output = _output(contract)
    for status, path in zip(statuses, paths):
        copied = output["confirmations"][path[0]][path[1]]
        assert copied["provider_state"] == f"provider_{status}"
        assert copied["status"] == status
    output["confirmations"]["open_interest"]["cryptoquant"]["status"] = "changed"
    assert contract["confirmations"]["open_interest"]["cryptoquant"]["status"] == "available"
    assert output["quality"]["status"] != "invalid"


def test_provider_comparisons_remain_unavailable_without_calculation():
    comparisons = _output()["confirmations"]["comparisons"]
    assert all(item == {"status": "unavailable", "reason": "provider_scope_not_proven_comparable"}
               for item in comparisons.values())


def test_quality_ok_when_all_required_are_available():
    contract = _contract()
    for timeframe in TIMEFRAMES:
        timestamp = contract["series"]["funding_rate_ohlc"]["timeframes"][timeframe]["current"]["timestamp"]
        _set_oi(contract, 1.0, timeframe, timestamp)
    quality = _output(contract)["quality"]
    assert (quality["status"], quality["contract_complete"], quality["data_complete"]) == ("ok", True, True)


def test_quality_partial_for_warmup_or_unavailable_required_without_invalid():
    contract = _contract()
    wrapper = _oi_change(contract)
    wrapper.update(status="unavailable", reason="insufficient_history_for_exact_24h_change", current=None, current_timestamp=None)
    quality = _output(contract)["quality"]
    assert (quality["status"], quality["contract_complete"], quality["data_complete"]) == ("partial", True, False)


def test_quality_invalid_only_for_invalid_required_and_warnings_are_sorted_unique():
    contract = _contract()
    _oi_change(contract)["units"]["change_percent"] = "ratio"
    quality = _output(contract)["quality"]
    assert (quality["status"], quality["data_complete"]) == ("invalid", False)
    assert quality["warnings"] == sorted(set(quality["warnings"]))
    assert quality["errors"] == sorted(set(quality["errors"]))


def test_deep_immutability_output_decoupling_and_determinism():
    contract = _contract()
    before = copy.deepcopy(contract)
    first = _output(contract)
    second = _output(contract)
    assert contract == before
    assert first == second and first is not second
    first["context"]["asset"] = "changed"
    assert contract["context"]["asset"] == "BTC"


def test_strict_json_and_negative_zero_normalization():
    contract = _contract()
    _set_funding(contract, -0.0)
    output = _output(contract)
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)
    funding_value = _current(output)["funding_state"]["evidence"]["values"]["funding_close"]
    assert funding_value == 0.0 and math.copysign(1.0, funding_value) == 1.0
    assert _current(output)["funding_state"]["state"] == "neutral"


def test_modes_preserve_identical_mathematical_classifications():
    outputs = []
    for mode in ("bootstrap", "incremental", "recovery"):
        contract = _contract()
        contract["mode"] = mode
        outputs.append(_output(contract)["classifications"])
    assert outputs[0] == outputs[1] == outputs[2]


def test_source_has_no_prohibited_imports_calculations_or_screen_contract_fields():
    source = CLASSIFIER.read_text(encoding="utf-8")
    prohibited_imports = ("pandas", "numpy", "processing.math", "open_interest_and_funding_processor",
                          "prices_ohlcv", "requests", "os.environ", "datetime.now", "time.time")
    assert all(token not in source for token in prohibited_imports)
    output_text = json.dumps(_output()).lower()
    for token in ("bullish", "bearish", "buy", "sell", "overbought", "oversold", '"score"', '"confidence"',
                  '"kpis"', '"charts"', '"tables"', '"widgets"', '"layout"'):
        assert token not in output_text


def test_evidence_values_units_ids_and_ids_are_contractual():
    output = _output()
    for timeframe in TIMEFRAMES:
        for name, atom in _current(output, timeframe).items():
            evidence = atom["evidence"]
            assert set(evidence["values"]) == set(evidence["units"])
            assert evidence["event_ids"] == sorted(set(evidence["event_ids"]))
            if evidence["timestamp"] is None:
                assert atom["classification_id"] is None
            else:
                assert atom["classification_id"] == f"{FAMILY}:{timeframe}:{evidence['timestamp']}:{name}"
