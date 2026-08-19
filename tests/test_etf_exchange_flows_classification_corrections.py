"""Timestamp semantics for ETF exchange-flow Classification.

Processing.data_as_of is a conservative shared/root anchor. Individual Processing
features may legitimately be fresher than that anchor; Classification only rejects
malformed timestamps or feature timestamps later than its generated timestamp.
"""
from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_classification_helpers import NOW, PARAMETERS, cloned_processing, feature
from processing_signals.classification.etf_exchange_flows import (
    classify_etf_exchange_flows,
    classify_etf_flow_direction,
)


def run(processing, generated_at=NOW):
    return classify_etf_exchange_flows(processing_contract=processing, generated_at=generated_at)


def assert_malformed_timestamp(item):
    assert (item["state"], item["status"], item["reason"], item["data_as_of"]) == (
        None, "invalid", "processing_timestamp_inconsistent", None)
    assert item["warnings"] == ["processing_timestamp_inconsistent"]


def assert_future_timestamp(item):
    assert item["state"] is None
    assert item["status"] == "invalid"
    assert item["reason"] == "future_timestamp"
    assert item["data_as_of"] is None
    assert "future_timestamp" in item["warnings"]


def test_feature_may_be_fresher_than_processing_root_anchor():
    processing = cloned_processing()
    processing["data_as_of"] = NOW - 100
    item = run(processing, generated_at=NOW)["classifications"]["etf_flow_direction"]["1d"]
    assert item["status"] == "available"
    assert item["data_as_of"] == NOW
    assert run(processing, generated_at=NOW)["data_as_of"] == NOW - 100


def test_simple_classifiers_accept_fresher_valid_features():
    processing = cloned_processing()
    processing["data_as_of"] = NOW - 100
    classifications = run(processing, generated_at=NOW)["classifications"]
    for name in ("gbtc_premium_regime", "exchange_pressure_regime", "exchange_netflow_regime",
                 "aum_reconciliation_state"):
        assert classifications[name]["status"] in {"available", "partial"}
        assert classifications[name]["reason"] != "processing_timestamp_inconsistent"


def test_future_relative_to_classification_clock_is_rejected():
    item = classify_etf_flow_direction(
        feature(1, data_as_of=NOW + 1), range_id="1d", generated_timestamp=NOW,
        parameters=PARAMETERS, processing_data_as_of=NOW - 100)
    assert_future_timestamp(item)


def test_future_required_feature_degrades_quality_but_other_pillars_survive():
    processing = cloned_processing()
    processing["features"]["etf"]["period_flow_usd"]["1d"]["data_as_of"] = NOW + 1
    output = run(processing, generated_at=NOW)
    assert output["quality"]["status"] == "partial"
    assert "etf_flow_direction.1d" in output["quality"]["invalid"]
    assert output["classifications"]["exchange_pressure_regime"]["status"] == "available"


def test_all_required_future_makes_quality_invalid():
    processing = cloned_processing()
    processing["features"]["etf"]["period_flow_usd"]["1d"]["data_as_of"] = NOW + 1
    processing["features"]["pressure"]["flow_24h"]["data_as_of"] = NOW + 1
    output = run(processing, generated_at=NOW)
    assert output["quality"]["status"] == "invalid"
    assert set(output["quality"]["invalid"]) >= {
        "etf_flow_direction.1d", "exchange_pressure_regime",
        "composite_capital_flow_regime", "data_confidence",
    }


def test_optional_future_does_not_invalidate_required_quality():
    processing = cloned_processing()
    processing["features"]["premium_discount"]["gbtc_latest"]["data_as_of"] = NOW + 1
    output = run(processing, generated_at=NOW)
    assert_future_timestamp(output["classifications"]["gbtc_premium_regime"])
    assert output["quality"]["status"] == "ok"
    assert "gbtc_premium_regime" in output["quality"]["invalid"]


def test_strict_int_timestamp_types_are_enforced():
    incompatible = (
        "2025-02-19T21:20:00+00:00", "1740000000", 1740000000.0, True,
        Decimal("1740000000"), {"timestamp": NOW}, [NOW], math.inf,
    )
    for timestamp in incompatible:
        item = classify_etf_flow_direction(
            feature(1, data_as_of=timestamp), range_id="1d", generated_timestamp=NOW,
            parameters=PARAMETERS, processing_data_as_of=NOW)
        assert_malformed_timestamp(item)
        json.dumps(item, allow_nan=False)


def test_exact_generated_at_boundary_is_valid():
    for timestamp in (NOW, NOW - 1):
        item = classify_etf_flow_direction(
            feature(1, data_as_of=timestamp), range_id="1d", generated_timestamp=NOW,
            parameters=PARAMETERS, processing_data_as_of=NOW - 10_000)
        assert (item["state"], item["status"], item["data_as_of"]) == ("inflow", "available", timestamp)


def test_derived_classifications_propagate_malformed_timestamp():
    for path in (("etf", "period_flow_usd", "1d"), ("pressure", "flow_24h")):
        processing = cloned_processing()
        target = processing["features"]
        for part in path:
            target = target[part]
        target["data_as_of"] = "2025-02-19T21:20:00+00:00"
        classifications = run(processing)["classifications"]
        if path[0] == "etf":
            assert_malformed_timestamp(classifications["etf_flow_persistence"])
        assert_malformed_timestamp(classifications["composite_capital_flow_regime"])
        assert_malformed_timestamp(classifications["data_confidence"])


def test_strict_json_determinism_and_immutability():
    processing = cloned_processing()
    processing["data_as_of"] = NOW - 100
    before = deepcopy(processing)
    first = run(processing, NOW)
    second = run(processing, NOW)
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    assert processing == before
    assert first == second
    first["classifications"]["exchange_pressure_regime"]["warnings"].append("mutated")
    assert "mutated" not in second["classifications"]["exchange_pressure_regime"]["warnings"]


def test_regime_boundaries_and_composite_regressions():
    result = run(cloned_processing())["classifications"]
    assert result["exchange_pressure_regime"]["state"] == "strong_exchange_inflow"
    assert result["gbtc_premium_regime"]["state"] == "premium"
    assert result["etf_flow_persistence"]["state"] == "persistent_outflow"
    assert result["composite_capital_flow_regime"]["state"] == "distribution"


