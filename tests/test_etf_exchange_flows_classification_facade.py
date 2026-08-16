"""Facade and contract tests for ETF exchange-flow Classification."""
from copy import deepcopy
import json

from etf_exchange_flows_classification_helpers import NOW, cloned_processing
from processing_signals.classification.etf_exchange_flows import (
    EtfExchangeFlowsClassifier,
    classify_etf_exchange_flows,
    run_etf_exchange_flows_classification,
)


def test_public_facades_are_equivalent_and_shape_is_canonical():
    processing = cloned_processing()
    direct = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
    assert direct == run_etf_exchange_flows_classification(processing_contract=processing, generated_at=NOW)
    assert direct == EtfExchangeFlowsClassifier().classify(processing_contract=processing, generated_at=NOW)
    assert set(direct) == {"family", "stage", "version", "mode", "data_mode", "is_demo", "generated_at",
                           "data_as_of", "classifications", "technical_events", "provenance", "quality"}
    assert (direct["family"], direct["stage"], direct["version"]) == ("etf_exchange_flows", "classification", "0.1")


def test_technical_cross_candidates_become_indexed_semantic_events():
    processing = cloned_processing()
    processing["technical_analysis"]["cross_candidates"] = [{"timestamp": NOW, "cross_id": "macd_above_signal",
        "first_series": "macd", "second_series": "signal", "direction": 1,
        "previous_difference": -1.0, "current_difference": 1.0}]
    technical = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)["technical_events"]
    event = technical["events"][0]
    assert set(event) == {"event_uid", "timestamp", "event_id", "event_type", "event_group", "indicator_id", "signal",
                          "label", "marker", "source", "display"}
    assert event["indicator_id"] == "macd"
    assert event["display"] == {"screen_a": False, "screen_b": True}
    assert technical["indexes"]["macd"] == [event["event_uid"]]


def test_generated_at_priority_does_not_change_data_as_of():
    processing = cloned_processing()
    first = classify_etf_exchange_flows(processing_contract=processing)
    second = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW + 100)
    assert second["generated_at"] == NOW + 100
    assert first["data_as_of"] == second["data_as_of"] == NOW


def test_json_strict_deep_immutability_and_output_isolation():
    processing = cloned_processing()
    before = deepcopy(processing)
    first = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
    second = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    assert processing == before and first == second
    first["classifications"]["etf_flow_direction"]["1d"]["warnings"].append("mutated")
    assert second["classifications"]["etf_flow_direction"]["1d"]["warnings"] == []


def test_synthetic_metadata_propagates_without_rule_changes():
    processing = cloned_processing()
    processing.update(mode="synthetic", data_mode="synthetic", is_demo=True)
    result = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
    assert (result["mode"], result["data_mode"], result["is_demo"]) == ("synthetic", "synthetic", True)
    assert result["classifications"]["exchange_pressure_regime"]["state"] == "strong_exchange_inflow"
