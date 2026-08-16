from copy import deepcopy
import json

import pytest

from processing_signals.classification.long_short_liquidations.long_short_liquidations_classifier import (
    LongShortLiquidationsClassifier, classify_cluster_regime, classify_concentration_regime,
    classify_event_activity_regime, classify_long_short_liquidations,
    classify_max_pain_proximity_regime, classify_pressure_regime,
    classify_provider_confirmation_regime, classify_realized_side_regime,
)

T = 1_800_000_000


def _metric(value, status="available", reason=None):
    return {"value": value, "status": status, "reason": reason}


def _concentration(top3=.6, hhi=.2, status="available"):
    return {"status": status, "reason": None, "top1_share": .3, "top3_share": top3,
            "hhi": hhi, "effective_bucket_count": 5}


def _contract():
    windows = {window: {"coverage_ratio": 1., "imbalance": _metric(.2)} for window in ("1h", "4h", "12h", "24h")}
    cluster = {"share_of_side": .3, "nearest_distance_bps": 50}
    return {"family": "long_short_liquidations", "stage": "processing", "reference_timestamp": T,
            "quality": {"status": "available", "warnings": [], "errors": []},
            "pressure": {"score": 40., "status": "available", "reason": None, "components": {
                "event_intensity": {**_metric(.4), "metadata": {"complete_baseline_bin_count": 80}}}},
            "realized": {"windows": windows, "confirmations": {}},
            "exchange_distribution": {"concentration": _concentration()},
            "events": {"aggregate": {"1h": {"event_usd_total": 10}}},
            "maps": {"aggregated": {"estimated_side_imbalance": _metric(-.4),
                "concentration": {"complete_map": _concentration(), "estimated_long": _concentration(),
                                  "estimated_short": _concentration()},
                "clusters": {"estimated_long": [cluster], "estimated_short": []}},
                "max_pain": {"status": "available", "reason": None, "provider_price_difference_bps": 100,
                             "long_distance_bps": -200, "short_distance_bps": 200, "provider_price": 100,
                             "long_max_pain_price": 98, "short_max_pain_price": 102}}}


def _confirmation(corr=.8, mape=.2, status="available", reason=None, coverage=1.):
    return {"pearson_correlation": _metric(corr, status, reason),
            "median_absolute_percentage_error": _metric(mape, status, reason),
            "aligned_point_count": _metric(24), "coverage_ratio": _metric(coverage)}


@pytest.mark.parametrize("case", range(1, 71), ids=[f"classification_smoke_{index:02d}" for index in range(1, 71)])
def test_classification_smoke(case):
    if case <= 10:
        value = [0, 24.999, 25, 49.999, 50, 74.999, 75, 100, -1, 101][case-1]
        result = classify_pressure_regime(_metric(value))
        expected = ["low_pressure", "low_pressure", "moderate_pressure", "moderate_pressure", "high_pressure",
                    "high_pressure", "extreme_pressure", "extreme_pressure", None, None][case-1]
        assert result["classification"] == expected
        assert result["status"] == ("invalid" if case >= 9 else "available")
    elif case <= 20:
        value = [0, .099, .1, .3, .6, 1, -.1, -.3, -.6, -1][case-11]
        result = classify_realized_side_regime(_metric(value))
        expected = ["realized_balanced", "realized_balanced", "realized_long_liquidations_dominant",
                    "realized_long_liquidations_dominant", "realized_long_liquidations_dominant",
                    "realized_long_liquidations_dominant", "realized_short_liquidations_dominant",
                    "realized_short_liquidations_dominant", "realized_short_liquidations_dominant",
                    "realized_short_liquidations_dominant"][case-11]
        assert result["classification"] == expected
    elif case == 21:
        contract = _contract()
        contract["maps"]["aggregated"]["estimated_side_imbalance"] = _metric(None, "unavailable", "missing_reference_price")
        assert classify_long_short_liquidations(contract)["classifications"]["estimated_side"]["reason"] == "missing_reference_price"
    elif case <= 27:
        value = [0, .25, .5, .75, .9, 1][case-22]
        expected = ["subdued_event_activity", "normal_event_activity", "elevated_event_activity",
                    "high_event_activity", "extreme_event_activity", "extreme_event_activity"][case-22]
        assert classify_event_activity_regime(_metric(value))["classification"] == expected
    elif case == 28:
        assert classify_long_short_liquidations(_contract())["classifications"]["events"]["1h"]["reason"] == "missing_processing_feature"
    elif case <= 32:
        top3 = [.5, .7, .85, .5][case-29]
        hhi = [.2, .3, .3, .1][case-29]
        result = classify_concentration_regime(_concentration(top3, hhi), source_path="x")
        assert result["classification"] == ["moderately_concentrated", "concentrated", "highly_concentrated", "moderately_concentrated"][case-29]
        if case == 32:
            assert result["reason"] == "conflicting_concentration_evidence"
    elif case <= 36:
        feature = [_confirmation(), _confirmation(.5, .4), _confirmation(.2, .2),
                   _confirmation(None, None, "unavailable", "zero_variance")][case-33]
        expected = ["provider_aligned", "provider_mixed", "provider_divergent", None][case-33]
        assert classify_provider_confirmation_regime(feature, source_path="x")["classification"] == expected
    elif case <= 43:
        if case == 37:
            clusters = {"estimated_long": [], "estimated_short": []}
        elif case in {38, 41}:
            clusters = {"estimated_long": [{"share_of_side": .25, "nearest_distance_bps": 100}], "estimated_short": []}
        elif case in {39, 42}:
            clusters = {"estimated_long": [], "estimated_short": [{"share_of_side": .1, "nearest_distance_bps": 250}]}
        elif case == 40:
            clusters = {"estimated_long": [{"share_of_side": .3, "nearest_distance_bps": 50}],
                        "estimated_short": [{"share_of_side": .2, "nearest_distance_bps": 150}]}
        else:
            clusters = {"estimated_long": [{"share_of_side": .05, "nearest_distance_bps": 300}], "estimated_short": []}
        result = classify_cluster_regime(clusters)
        expected = {37: "no_spatial_clusters", 38: "long_side_clustered", 39: "short_side_clustered",
                    40: "bilateral_clusters"}.get(case)
        if expected:
            assert result["classification"] == expected
        else:
            assert result["strength"] == {41: "strong", 42: "moderate", 43: "weak"}[case]
    elif case <= 47:
        value = [50, 150, 500, 501][case-44]
        expected = ["max_pain_very_near", "max_pain_near", "max_pain_moderate_distance", "max_pain_far"][case-44]
        assert classify_max_pain_proximity_regime(_metric(value))["classification"] == expected
    elif case == 48:
        result = classify_pressure_regime(_metric(30, "partial"))
        assert result["classification"] == "moderate_pressure" and result["confidence"] <= .7
    elif case == 49:
        result = classify_pressure_regime(_metric(None, "partial"))
        assert result["status"] == "unavailable" and result["reason"] == "missing_numeric_value"
    elif case in {50, 51}:
        status = "unavailable" if case == 50 else "invalid"
        result = classify_pressure_regime(_metric(40, status))
        assert result["classification"] is None and result["status"] == status
    elif case == 52:
        contract = _contract()
        contract["quality"]["status"] = "invalid"
        assert classify_long_short_liquidations(contract)["quality"]["status"] == "invalid"
    elif case == 53:
        contract = _contract()
        contract["maps"]["max_pain"] = {"status": "unavailable", "reason": "not_requested"}
        assert classify_long_short_liquidations(contract)["quality"]["status"] == "available"
    elif case == 54:
        contract = _contract()
        contract["pressure"].update(score=None, status="unavailable")
        assert classify_long_short_liquidations(contract)["quality"]["status"] == "available"
    elif case == 55:
        assert classify_pressure_regime(_metric(25))["confidence"] == .6
    elif case == 56:
        assert classify_pressure_regime(_metric(37.5))["confidence"] == 1.
    elif case == 57:
        feature = {**_metric(.4), "metadata": {"coverage_ratio": .5}}
        assert classify_event_activity_regime(feature)["confidence"] == .46
    elif case == 58:
        assert classify_long_short_liquidations(_contract())["classifications"]["composite_regime"]["reason"] == "classification_not_applicable"
    elif case == 59:
        json.dumps(classify_long_short_liquidations(_contract()), ensure_ascii=False, allow_nan=False)
    elif case == 60:
        source, config = _contract(), {"custom": [1]}
        before = deepcopy((source, config))
        classify_long_short_liquidations(source, config=config)
        assert (source, config) == before
    else:
        feature = _confirmation()
        if case == 61:
            feature["aligned_point_count"] = _metric(None, "unavailable", "insufficient_aligned_points")
        elif case == 62:
            feature["coverage_ratio"] = _metric(None, "unavailable", "insufficient_coverage")
        elif case == 63:
            feature["aligned_point_count"] = _metric(None, "invalid", "invalid_source_feature")
        elif case == 64:
            feature["coverage_ratio"] = _metric(None, "invalid", "invalid_source_feature")
        elif case == 65:
            feature["aligned_point_count"] = _metric(24, "partial", "insufficient_coverage")
        elif case == 66:
            feature["coverage_ratio"] = _metric(.75, "partial", "insufficient_coverage")
        elif case == 67:
            feature["aligned_point_count"] = _metric(None, "invalid", "invalid_source_feature")
            feature["coverage_ratio"] = _metric(None, "unavailable", "insufficient_coverage")
        elif case == 68:
            feature["aligned_point_count"] = _metric(None, "unavailable", "insufficient_aligned_points")
            feature["coverage_ratio"] = _metric(.75, "partial", "insufficient_coverage")
        elif case == 69:
            feature["aligned_point_count"] = _metric(None, "unavailable", "private_provider_reason")
        result = classify_provider_confirmation_regime(feature, source_path="x")
        if case in {61, 62, 68, 69}:
            assert result["classification"] is None and result["status"] == "unavailable"
        elif case in {63, 64, 67}:
            assert result["classification"] is None and result["status"] == "invalid"
        elif case in {65, 66}:
            assert result["classification"] == "provider_aligned" and result["status"] == "partial"
        else:
            assert result["classification"] == "provider_aligned" and result["status"] == "available"
        if case == 66:
            assert result["confidence"] == .357
        if case == 69:
            assert result["reason"] == "unavailable_source_feature"
            assert result["evidence"]["source_reasons"]["aligned_point_count"] == "private_provider_reason"


@pytest.mark.parametrize(("field", "value"), [("family", "bad"), ("stage", "input"), ("reference_timestamp", True)])
def test_invalid_root_contract(field, value):
    contract = _contract()
    contract[field] = value
    with pytest.raises(ValueError, match=f"invalid_processing_contract:{field}"):
        classify_long_short_liquidations(contract)


@pytest.mark.parametrize("hostile", [float("nan"), float("inf"), {1: "bad"}, {"bad": object()}])
def test_hostile_contract_and_config(hostile):
    contract = _contract()
    contract["hostile"] = hostile
    with pytest.raises(ValueError, match="invalid_processing_contract"):
        classify_long_short_liquidations(contract)
    with pytest.raises(ValueError, match="invalid_processing_contract"):
        classify_long_short_liquidations(_contract(), config={"hostile": hostile})


def test_unknown_reason_is_preserved_and_normalized():
    result = classify_pressure_regime(_metric(None, "unavailable", "provider_private_reason"))
    assert result["reason"] == "unavailable_source_feature"
    assert result["evidence"]["source_reason"] == "provider_private_reason"


def test_four_windows_multiple_confirmations_facade_and_no_hmi_labels():
    contract = _contract()
    contract["realized"]["confirmations"] = {"a": _confirmation(), "b": _confirmation(.2, .6)}
    before = deepcopy(contract)
    result = LongShortLiquidationsClassifier(config={"version": "test"}).classify(contract)
    assert set(result["classifications"]["realized_side"]) == {"1h", "4h", "12h", "24h"}
    assert set(result["classifications"]["confirmations"]) == {"a", "b"}
    assert contract == before and result["quality"]["status"] == "available"
    text = json.dumps(result).lower()
    assert all(label not in text for label in ("display_value", "color_token", "widget", "formatted_percentage", "bullish", "bearish"))


def test_quality_partial_unavailable_invalid_and_confidence_range():
    for status, expected in (("unavailable", "unavailable"), ("invalid", "invalid")):
        contract = _contract()
        contract["quality"]["status"] = status
        result = classify_long_short_liquidations(contract)
        assert result["quality"]["status"] == expected
    contract = _contract()
    contract["pressure"]["status"] = "partial"
    assert classify_long_short_liquidations(contract)["quality"]["status"] == "available"
    values = [classify_pressure_regime(_metric(value, "partial"))["confidence"] for value in (0, 12.5, 25, 50, 75, 100)]
    assert all(0 <= value <= 1 for value in values)


@pytest.mark.parametrize("scenario", [
    "aligned_unavailable", "aligned_invalid", "aligned_partial", "coverage_unavailable", "coverage_invalid",
    "coverage_partial", "correlation_unavailable", "mape_unavailable", "two_degraded_priorities",
    "invalid_dominates_unavailable", "unavailable_dominates_partial", "partial_classifies",
    "partial_single_penalty", "coverage_partial_data_factor", "aligned_bool", "coverage_bool",
    "coverage_out_of_range", "aligned_negative", "known_reason", "unknown_reason",
])
def test_confirmation_required_metric_semantics(scenario):
    feature = _confirmation()
    if scenario in {"aligned_unavailable", "known_reason"}:
        feature["aligned_point_count"] = _metric(None, "unavailable", "insufficient_aligned_points")
    elif scenario == "aligned_invalid":
        feature["aligned_point_count"] = _metric(None, "invalid", "invalid_source_feature")
    elif scenario in {"aligned_partial", "partial_classifies", "partial_single_penalty"}:
        feature["aligned_point_count"] = _metric(24, "partial", "insufficient_coverage")
    elif scenario == "coverage_unavailable":
        feature["coverage_ratio"] = _metric(None, "unavailable", "insufficient_coverage")
    elif scenario == "coverage_invalid":
        feature["coverage_ratio"] = _metric(None, "invalid", "invalid_source_feature")
    elif scenario in {"coverage_partial", "coverage_partial_data_factor"}:
        feature["coverage_ratio"] = _metric(.75, "partial", "insufficient_coverage")
    elif scenario == "correlation_unavailable":
        feature["pearson_correlation"] = _metric(None, "unavailable", "zero_variance")
    elif scenario == "mape_unavailable":
        feature["median_absolute_percentage_error"] = _metric(None, "unavailable", "insufficient_aligned_points")
    elif scenario in {"two_degraded_priorities", "unavailable_dominates_partial"}:
        feature["pearson_correlation"] = _metric(.8, "partial", "insufficient_coverage")
        feature["coverage_ratio"] = _metric(None, "unavailable", "insufficient_aligned_points")
    elif scenario == "invalid_dominates_unavailable":
        feature["aligned_point_count"] = _metric(None, "invalid", "invalid_source_feature")
        feature["coverage_ratio"] = _metric(None, "unavailable", "insufficient_coverage")
    elif scenario == "aligned_bool":
        feature["aligned_point_count"] = _metric(True)
    elif scenario == "coverage_bool":
        feature["coverage_ratio"] = _metric(True)
    elif scenario == "coverage_out_of_range":
        feature["coverage_ratio"] = _metric(1.01)
    elif scenario == "aligned_negative":
        feature["aligned_point_count"] = _metric(-1)
    elif scenario == "unknown_reason":
        feature["coverage_ratio"] = _metric(None, "unavailable", "private_provider_reason")
    if scenario in {"aligned_bool", "coverage_bool", "coverage_out_of_range", "aligned_negative"}:
        with pytest.raises(ValueError, match="invalid_processing_contract:x"):
            classify_provider_confirmation_regime(feature, source_path="x")
        return
    result = classify_provider_confirmation_regime(feature, source_path="x")
    if scenario in {"aligned_invalid", "coverage_invalid", "invalid_dominates_unavailable"}:
        assert result["status"] == "invalid" and result["classification"] is None and result["confidence"] == 0
    elif scenario in {"aligned_partial", "coverage_partial", "partial_classifies", "partial_single_penalty",
                      "coverage_partial_data_factor"}:
        assert result["status"] == "partial" and result["classification"] == "provider_aligned"
        expected = .357 if scenario in {"coverage_partial", "coverage_partial_data_factor"} else .476
        assert result["confidence"] == expected and result["reason"] == "partial_source_feature"
    else:
        assert result["status"] == "unavailable" and result["classification"] is None and result["confidence"] == 0
    if scenario == "known_reason":
        assert result["reason"] == "insufficient_aligned_points"
    if scenario == "unknown_reason":
        assert result["reason"] == "unavailable_source_feature"
        assert result["evidence"]["source_reasons"]["coverage_ratio"] == "private_provider_reason"


@pytest.mark.parametrize("degraded_status", ["unavailable", "invalid"])
def test_confirmation_optional_quality_registration_and_immutability(degraded_status):
    contract, config = _contract(), {"custom": [1]}
    feature = _confirmation()
    feature["coverage_ratio"] = _metric(None, degraded_status,
                                        "insufficient_coverage" if degraded_status == "unavailable" else "invalid_source_feature")
    contract["realized"]["confirmations"] = {"provider": feature}
    before = deepcopy((contract, config))
    result = classify_long_short_liquidations(contract, config=config)
    bucket = "unavailable_classifications" if degraded_status == "unavailable" else "invalid_classifications"
    assert result["quality"]["status"] == "available" and "provider_confirmation_regime" in result["quality"][bucket]
    result["classifications"]["confirmations"]["provider"]["evidence"]["required_metric_statuses"].clear()
    assert (contract, config) == before
    json.dumps(result, ensure_ascii=False, allow_nan=False)
