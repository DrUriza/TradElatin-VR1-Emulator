from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from processing_signals.classification.cvd_volume_orderflow.cvd_volume_orderflow_classifier import (
    CvdVolumeOrderflowClassifier, classify_cvd_volume_orderflow,
)

ROOT = Path(__file__).resolve().parents[1]
NOW  = 2_000_000_000


def metric(value, status="available", reason=None):
    return {"value": value, "status": status, "reason": reason}


def bar(timestamp=NOW, *, imbalance=.18, ratio=1.10, delta=999.0, efficiency=.68, open_value=100.0, close_value=90.0,
        continuity="complete", coverage=True, partial=False):
    return {"timestamp": timestamp, "taker_buy_volume_usd": 100.0, "taker_sell_volume_usd": 80.0, "total_volume_usd": 180.0,
        "volume_delta_usd": delta, "buy_sell_ratio": metric(ratio), "buy_share": metric(.55), "sell_share": metric(.45),
        "order_flow_imbalance": metric(imbalance), "delta_ma_21_usd": 1.0, "flow_efficiency": metric(efficiency),
        "cvd_ohlc_usd": {"open": open_value, "high": 110.0, "low": 80.0, "close": close_value},
        "source_records_expected": 1, "source_records_used": 1, "coverage_complete": coverage, "is_partial": partial,
        "continuity_status": continuity, "provider_cvd_reference_usd": 123456.0}


def timeframe(*, current=None, previous=None, status="available", reason=None):
    current = bar() if current is None else current
    records = [] if current is None else ([previous] if previous is not None else []) + [current]
    return {"status": status, "reason": reason, "source_timeframe": "1m", "target_timeframe": "1m", "interval_seconds": 60,
        "source_factor": 1, "records_available": len(records), "first_timestamp": records[0]["timestamp"] if records else None,
        "last_timestamp": records[-1]["timestamp"] if records else None, "current_timestamp": current["timestamp"] if current else None,
        "complete_records": len(records), "partial_records": 0, "gap_count": 0, "continuity_break_count": 0,
        "anchor_method": "zero_before_first_available_record", "anchor_timestamp": records[0]["timestamp"] if records else None,
        "anchor_value_usd": 0.0, "history_relative": True, "provider_reference_used_in_calculation": False,
        "records": records, "current": current}


def summary(*, imbalance=.18, ratio=1.10, delta=999.0, efficiency=.68, status="available", reason=None):
    return {"taker_buy_volume_usd": 100.0, "taker_sell_volume_usd": 80.0, "total_volume_usd": 180.0,
        "volume_delta_usd": delta, "buy_sell_ratio": metric(ratio), "buy_share": metric(.55), "sell_share": metric(.45),
        "order_flow_imbalance": metric(imbalance), "flow_efficiency": metric(efficiency), "records_expected": 4,
        "records_used": 4, "coverage_complete": status == "available", "first_timestamp": NOW - 2700, "last_timestamp": NOW,
        "status": status, "reason": reason}


def processing_contract():
    markets = {}
    for market in ("spot", "futures"):
        markets[market] = {"timeframes": {name: timeframe(previous=bar(NOW - 60, imbalance=-.10, open_value=90, close_value=95))
            for name in ("1m", "5m", "15m", "30m", "1h", "4h", "1d")},
            "window_summaries": {"1h": summary(), "24h": summary()}, "footprint_summaries": {"1h": {}},
            "price_vs_vwap": {"value": .0042, "status": "available", "reason": None, "price_timestamp": NOW, "price_usd": 100.0},
            "availability": {}}
    cross_market = {"window_summaries": {"1h": summary(), "24h": summary()},
        "volume_ratios": {"futures_vs_spot": {"1h": {"value": 1.0, "status": "available", "reason": None,
            "futures_volume_usd": 180.0, "spot_volume_usd": 180.0, "timestamp": NOW}}},
        "footprint_summaries": {"1h": {"status": "unavailable", "reason": "not_in_fixture"}}}
    return {"family": "cvd_volume_orderflow", "stage": "processing", "version": "0.1.0", "mode": "bootstrap",
        "context": {"base_asset": "BTC", "pair_symbol": "BTCUSDT", "markets": ["spot", "futures"],
            "base_timeframes": ["1m", "15m"], "available_timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
            "data_mode": "synthetic", "is_demo": True, "reference_timestamp": NOW, "input_requested_at": "x",
            "input_execution_timestamp": NOW, "processing_timestamp": NOW}, "parameters": {"source_timeframes": {}},
        "markets": markets, "cross_market": cross_market,
        "quality": {"status": "ok", "core_status": "available", "enrichment_status": "available", "warnings": [], "errors": []}}


def classify(contract=None):
    return classify_cvd_volume_orderflow(contract or processing_contract(), clock=lambda: NOW + 1)


@pytest.mark.parametrize(("field", "value", "message"), [("family", "wrong", "family"), ("stage", "input", "stage"),
    ("version", "9", "version")])
def test_rejects_wrong_root_contract(field, value, message):
    contract = processing_contract()
    contract[field] = value
    with pytest.raises(ValueError, match=message):
        classify(contract)


def test_rejects_non_mapping_missing_market_and_timeframe():
    with pytest.raises(ValueError, match="mapping"):
        classify_cvd_volume_orderflow([])
    contract = processing_contract()
    del contract["markets"]["spot"]
    with pytest.raises(ValueError, match="structure"):
        classify(contract)
    contract = processing_contract()
    del contract["markets"]["spot"]["timeframes"]["1m"]
    with pytest.raises(ValueError, match="timeframes"):
        classify(contract)


def test_contract_immutable_clock_context_and_strict_json():
    contract = processing_contract()
    before = copy.deepcopy(contract)
    first, second = classify(contract), classify(contract)
    assert contract == before
    assert first == second
    assert first["context"] == {"base_asset": "BTC", "pair_symbol": "BTCUSDT", "markets": ["spot", "futures"],
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"], "data_mode": "synthetic", "is_demo": True,
        "reference_timestamp": NOW, "processing_timestamp": NOW, "classification_timestamp": NOW + 1}
    json.dumps(first, allow_nan=False)


def test_synthetic_requires_demo():
    contract = processing_contract()
    contract["context"]["is_demo"] = False
    with pytest.raises(ValueError, match="demo"):
        classify(contract)


@pytest.mark.parametrize(("value", "state"), [(-1, "negative"), (0, "neutral"), (1, "positive")])
def test_delta_sign(value, state):
    atom = CvdVolumeOrderflowClassifier().classify_delta(value, "available", None, "x")
    assert (atom["state"], atom["value"], atom["threshold_id"]) == (state, value, "volume_delta_sign_v1")


@pytest.mark.parametrize(("value", "state"), [(-.25, "strong_selling"), (-.05, "neutral"), (.05, "neutral"),
    (.25, "strong_buying"), (.18, "buying")])
def test_imbalance_boundaries(value, state):
    atom = CvdVolumeOrderflowClassifier().classify_order_flow_imbalance(metric(value), "x")
    assert (atom["state"], atom["value"], atom["unit"]) == (state, value, "decimal")


@pytest.mark.parametrize(("value", "state"), [(.80, "strong_selling"), (.95, "balanced"), (1.05, "balanced"),
    (1.25, "strong_buying"), (1.10, "buying")])
def test_ratio_boundaries(value, state):
    assert CvdVolumeOrderflowClassifier().classify_buy_sell_ratio(metric(value), "x")["state"] == state


@pytest.mark.parametrize(("value", "state"), [(0, "low"), (.33, "moderate"), (.67, "high"), (.68, "high"), (1, "high")])
def test_efficiency_boundaries(value, state):
    atom = CvdVolumeOrderflowClassifier().classify_flow_efficiency(metric(value), "x")
    assert (atom["state"], atom["direction"]) == (state, "neutral")


@pytest.mark.parametrize("value", [-.01, 1.01, float("nan"), float("inf"), True])
def test_efficiency_rejects_invalid_domain_and_numbers(value):
    with pytest.raises(ValueError):
        CvdVolumeOrderflowClassifier().classify_flow_efficiency(metric(value), "x")


@pytest.mark.parametrize("field", ["high", "low"])
def test_non_finite_cvd_ohlc_snapshot_values_are_rejected(field):
    contract = processing_contract()
    contract["markets"]["spot"]["timeframes"]["1m"]["current"]["cvd_ohlc_usd"][field] = float("nan")
    contract["markets"]["spot"]["timeframes"]["1m"]["records"][-1]["cvd_ohlc_usd"][field] = float("nan")
    with pytest.raises(ValueError, match=f"cvd_{field}"):
        classify(contract)


@pytest.mark.parametrize(("open_value", "close_value", "state"), [(2, 1, "falling"), (2, 2, "flat"), (1, 2, "rising")])
def test_cvd_direction_compares_received_open_close(open_value, close_value, state):
    atom = CvdVolumeOrderflowClassifier().classify_cvd_direction(
        {"open": open_value, "high": max(open_value, close_value), "low": min(open_value, close_value), "close": close_value}, "available", None, "x")
    assert (atom["state"], atom["value"]) == (state, {"open": open_value, "close": close_value})


@pytest.mark.parametrize(("value", "state"), [(-.005, "far_below"), (0, "at_vwap"), (.005, "far_above"), (.0042, "above")])
def test_price_vs_vwap_boundaries(value, state):
    assert CvdVolumeOrderflowClassifier().classify_price_vs_vwap(metric(value), "x")["state"] == state


def test_continuity_coverage_and_source_status_propagation():
    classifier = CvdVolumeOrderflowClassifier()
    assert classifier.classify_continuity("complete", "available", None, "x")["state"] == "complete"
    assert classifier.classify_continuity("broken", "partial", "gap", "x")["availability"] == {"status": "partial", "reason": "gap"}
    assert classifier.classify_coverage(True, False, "available", None, "x")["state"] == "complete"
    assert classifier.classify_coverage(False, True, "partial", "bucket", "x")["state"] == "partial"
    partial = classifier.classify_order_flow_imbalance(metric(.18, "partial", "source_partial"), "x")
    assert (partial["state"], partial["availability"]["status"]) == ("buying", "partial")
    unavailable = classifier.classify_order_flow_imbalance(metric(None, "unavailable", "warmup"), "x")
    assert (unavailable["state"], unavailable["availability"]) == ("unavailable", {"status": "unavailable", "reason": "warmup"})
    invalid = classifier.classify_order_flow_imbalance(metric(None, "invalid", "bad"), "x")
    assert invalid["availability"] == {"status": "invalid", "reason": "bad"}


def test_current_null_and_partial_current_never_fall_back():
    contract = processing_contract()
    unavailable = timeframe(status="unavailable", reason="no_records")
    unavailable.update(records=[], records_available=0, first_timestamp=None, last_timestamp=None, current_timestamp=None, current=None)
    contract["markets"]["spot"]["timeframes"]["1m"] = unavailable
    output = classify(contract)["classifications"]["markets"]["spot"]["timeframes"]["1m"]
    assert output["timestamp"] is None
    assert all(atom["state"] == "unavailable" for atom in output["atoms"].values())
    contract = processing_contract()
    current = bar(NOW, partial=True, coverage=False, efficiency=.1)
    contract["markets"]["spot"]["timeframes"]["1m"] = timeframe(current=current, previous=bar(NOW - 60), status="partial", reason="bucket")
    output = classify(contract)["classifications"]["markets"]["spot"]["timeframes"]["1m"]
    assert output["timestamp"] == NOW
    assert output["availability"]["status"] == "partial"


def test_no_recalculation_uses_deliberately_inconsistent_processing_values():
    output = classify()
    atoms = output["classifications"]["markets"]["spot"]["timeframes"]["1m"]["atoms"]
    assert atoms["delta_state"]["state"] == "positive" and atoms["delta_state"]["value"] == 999
    assert atoms["buy_sell_pressure_state"]["state"] == "buying" and atoms["buy_sell_pressure_state"]["value"] == 1.10
    assert atoms["order_flow_state"]["state"] == "buying" and atoms["order_flow_state"]["value"] == .18
    assert atoms["flow_efficiency_state"]["state"] == "high" and atoms["flow_efficiency_state"]["value"] == .68
    assert atoms["cvd_direction_state"]["state"] == "falling"


def test_summary_classification_and_confirmations():
    output = classify()
    spot = output["classifications"]["markets"]["spot"]["window_summaries"]
    assert spot["1h"]["atoms"]["order_flow_state"]["state"] == "buying"
    assert spot["24h"]["atoms"]["delta_state"]["value"] == 999
    assert output["confirmations"]["market_agreement_1h"]["state"] == "confirmed_buying"
    assert output["confirmations"]["temporal_alignment"]["spot"]["state"] == "persistent_buying"


def test_partial_and_unavailable_confirmation_sources_propagate_availability():
    contract = processing_contract()
    contract["markets"]["spot"]["window_summaries"]["1h"] = summary(status="partial", reason="incomplete_fixed_window")
    agreement = classify(contract)["confirmations"]["market_agreement_1h"]
    assert agreement["state"] == "confirmed_buying"
    assert agreement["availability"] == {"status": "partial", "reason": "source_partial"}
    contract["markets"]["spot"]["window_summaries"]["1h"] = summary(status="unavailable", reason="no_records")
    contract["markets"]["spot"]["window_summaries"]["1h"]["order_flow_imbalance"] = metric(None, "unavailable", "no_records")
    assert classify(contract)["confirmations"]["market_agreement_1h"]["state"] == "unavailable"


@pytest.mark.parametrize(("spot", "futures", "expected"), [(.2, .3, "confirmed_buying"), (-.2, -.3, "confirmed_selling"),
    (0, 0, "balanced"), (.2, -.2, "divergent"), (.2, 0, "mixed")])
def test_market_agreement_states(spot, futures, expected):
    contract = processing_contract()
    contract["markets"]["spot"]["window_summaries"]["1h"] = summary(imbalance=spot)
    contract["markets"]["futures"]["window_summaries"]["1h"] = summary(imbalance=futures)
    assert classify(contract)["confirmations"]["market_agreement_1h"]["state"] == expected


@pytest.mark.parametrize(("one", "day", "expected"), [(.2, .3, "persistent_buying"), (-.2, -.3, "persistent_selling"),
    (.2, -.2, "reversal"), (.2, 0, "mixed")])
def test_temporal_alignment_states(one, day, expected):
    contract = processing_contract()
    contract["markets"]["spot"]["window_summaries"] = {"1h": summary(imbalance=one), "24h": summary(imbalance=day)}
    assert classify(contract)["confirmations"]["temporal_alignment"]["spot"]["state"] == expected


def test_snapshots_preserve_exact_values_without_history_or_aliasing():
    contract = processing_contract()
    output = classify(contract)
    snapshot = output["snapshots"]["markets"]["spot"]["timeframes"]["1m"]
    assert snapshot["volume_delta_usd"] == 999
    assert snapshot["cvd_ohlc_usd"] == {"open": 100.0, "high": 110.0, "low": 80.0, "close": 90.0}
    assert "records" not in snapshot
    snapshot["cvd_ohlc_usd"]["open"] = -1
    assert contract["markets"]["spot"]["timeframes"]["1m"]["current"]["cvd_ohlc_usd"]["open"] == 100


def test_transition_divergence_continuity_events_ids_order_and_no_duplicates():
    contract = processing_contract()
    contract["markets"]["futures"]["window_summaries"]["1h"] = summary(imbalance=-.2)
    current = bar(NOW, imbalance=.2, continuity="broken")
    previous = bar(NOW - 60, imbalance=-.2, continuity="complete")
    contract["markets"]["spot"]["timeframes"]["1m"] = timeframe(current=current, previous=previous, status="partial", reason="gap")
    events = classify(contract)["interpreted_events"]
    ids = [event["event_id"] for event in events]
    assert f"cvd:spot:1m:order_flow_transition:{NOW}" in ids
    assert f"cvd:spot:1m:continuity_break:{NOW}" in ids
    assert all("general" not in event_id for event_id in ids)
    assert len(ids) == len(set(ids))
    assert events == sorted(events, key=lambda event: (event["timestamp"], event["event_type"], event["market"], event["timeframe"], event["event_id"]))


def test_no_transition_within_same_direction_group():
    contract = processing_contract()
    contract["markets"]["spot"]["timeframes"]["1m"] = timeframe(current=bar(NOW, imbalance=.3), previous=bar(NOW - 60, imbalance=.1))
    assert not [event for event in classify(contract)["interpreted_events"] if event["market"] == "spot" and event["timeframe"] == "1m"]


def test_quality_ok_partial_invalid_and_enrichment_separation():
    assert classify()["quality"] == {"status": "ok", "core_status": "ok", "enrichment_status": "ok",
        "processing_quality_status": "ok", "warnings": [], "errors": []}
    contract = processing_contract()
    contract["markets"]["spot"]["price_vs_vwap"] = {"value": None, "status": "unavailable", "reason": "price_reference_not_provided"}
    output = classify(contract)
    assert output["quality"]["status"] == "ok"
    assert output["quality"]["core_status"] == "ok"
    assert output["quality"]["enrichment_status"] == "partial"
    contract = processing_contract()
    contract["quality"]["status"] = "invalid"
    assert classify(contract)["quality"]["status"] == "invalid"
    contract = processing_contract()
    for market in ("spot", "futures"):
        for timeframe_name in contract["markets"][market]["timeframes"]:
            source = contract["markets"][market]["timeframes"][timeframe_name]
            source.update(status="unavailable", reason="no_records", records=[], current=None)
    assert classify(contract)["quality"]["core_status"] == "invalid"
    contract = processing_contract()
    contract["markets"]["spot"]["window_summaries"]["1h"] = summary(imbalance=.2)
    contract["markets"]["futures"]["window_summaries"]["1h"] = summary(imbalance=0)
    mixed = classify(contract)
    assert mixed["confirmations"]["market_agreement_1h"]["availability"] == {"status": "partial", "reason": "market_agreement_mixed"}
    assert mixed["quality"]["core_status"] == "partial"
    assert mixed["quality"]["status"] == "partial"


def test_output_has_no_presentation_runtime_or_history_layers():
    encoded = json.dumps(classify(), allow_nan=False).lower()
    for forbidden in ('"screen"', '"kpi"', '"charts"', '"widgets"', '"color_token"', '"display_label"', '"route"'):
        assert forbidden not in encoded


def test_registered_in_classification_pipeline():
    from processing_signals.classification.classification_pipeline import CLASSIFICATION_FAMILY_HANDLERS
    assert "cvd_volume_orderflow" in CLASSIFICATION_FAMILY_HANDLERS
