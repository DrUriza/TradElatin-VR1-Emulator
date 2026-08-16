"""Adversarial tests for ETF exchange-flow Contract Builder v0.1."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from test_etf_exchange_flows_contract_builder import contracts
from processing_signals.classification.etf_exchange_flows import build_etf_exchange_flows_contract


def invoke(processing, classification, **kwargs):
    return build_etf_exchange_flows_contract(
        processing_contract=processing, classification_contract=classification, **kwargs
    )


def test_cba01_invalid_selected_range_is_rejected():
    processing, classification = contracts()
    with pytest.raises(ValueError, match="^invalid_selected_range$"):
        invoke(processing, classification, selected_range="365d")


@pytest.mark.parametrize("field", ("family", "stage", "version"))
@pytest.mark.parametrize("target", ("processing", "classification"))
def test_cba02_cba07_invalid_root_identity_returns_invalid(target, field):
    processing, classification = contracts()
    contract = processing if target == "processing" else classification
    contract[field] = "wrong"
    output = invoke(processing, classification)
    assert output["quality"]["status"] == "invalid"
    assert any(field in error for error in output["quality"]["errors"])


@pytest.mark.parametrize(
    ("target", "field"),
    (("processing", "features"), ("processing", "series"), ("processing", "snapshots"),
     ("processing", "series_metadata"), ("processing", "quality"), ("processing", "provenance"),
     ("classification", "classifications"), ("classification", "quality"),
     ("classification", "provenance")),
)
def test_cba08_cba16_required_root_mappings(target, field):
    processing, classification = contracts()
    contract = processing if target == "processing" else classification
    contract[field] = []
    assert invoke(processing, classification)["quality"]["status"] == "invalid"


@pytest.mark.parametrize("value", ("1740000000", 1_740_000_000.0, True, -1, None))
@pytest.mark.parametrize("target", ("processing", "classification"))
def test_cba17_cba26_strict_root_timestamp(target, value):
    processing, classification = contracts()
    contract = processing if target == "processing" else classification
    contract["data_as_of"] = value
    output = invoke(processing, classification)
    assert output["quality"]["status"] == "invalid"


def test_cba27_classification_cannot_be_later_than_processing():
    processing, classification = contracts()
    classification["data_as_of"] = processing["data_as_of"] + 1
    output = invoke(processing, classification)
    assert output["quality"]["status"] == "invalid"
    assert output["quality"]["errors"] == ["upstream_timestamp_inconsistent"]


@pytest.mark.parametrize("value", ("1740000000", 1_740_000_000.0, True))
def test_cba28_cba30_feature_timestamp_is_not_coerced(value):
    processing, classification = contracts()
    processing["features"]["etf"]["period_flow_usd"]["30d"]["data_as_of"] = value
    output = invoke(processing, classification)
    kpi = output["kpis"]["etf_net_flow"]
    assert (kpi["status"], kpi["reason"], kpi["value"]) == (
        "invalid", "upstream_timestamp_inconsistent", None
    )


def test_cba31_future_feature_is_invalid_not_clipped():
    processing, classification = contracts()
    processing["features"]["etf"]["period_flow_usd"]["30d"]["data_as_of"] += 1
    output = invoke(processing, classification)
    assert output["kpis"]["etf_net_flow"]["reason"] == "upstream_timestamp_inconsistent"
    assert output["quality"]["status"] == "invalid"


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf"), True, Decimal("1")))
def test_cba32_cba36_invalid_financial_value(value):
    processing, classification = contracts()
    processing["features"]["etf"]["reported_total_aum_usd"]["value"] = value
    if isinstance(value, Decimal):
        output = invoke(processing, classification)
    else:
        output = invoke(processing, classification)
    assert output["kpis"]["total_aum"]["status"] == "invalid"
    assert output["kpis"]["total_aum"]["value"] is None


def test_cba37_incompatible_unit_is_invalid():
    processing, classification = contracts()
    processing["features"]["exchange_balances"]["cryptoquant_reserve"]["unit"] = "USD"
    output = invoke(processing, classification)
    assert output["kpis"]["exchange_balance"]["reason"] == "source_unit_incompatible"


@pytest.mark.parametrize("chart", ("etf_flow_daily", "etf_cumulative_flow", "exchange_balance"))
def test_cba38_cba40_non_list_series_is_invalid(chart):
    processing, classification = contracts()
    processing["series"][chart] = {}
    output = invoke(processing, classification)
    if chart == "etf_cumulative_flow":
        assert output["kpis"]["cumulative_etf_net_flow"]["status"] == "invalid"
        assert "etf_cumulative_net_flow" not in output["charts"]
    else:
        assert output["charts"][chart]["status"] == "invalid"


def test_cba41_non_list_selected_netflow_series_is_invalid():
    processing, classification = contracts()
    processing["series"]["exchange_netflow"]["day"] = {}
    assert invoke(processing, classification)["charts"]["exchange_net_flow"]["status"] == "invalid"


def test_cba42_future_point_is_invalid_and_not_returned():
    processing, classification = contracts()
    processing["series"]["etf_flow_daily"] = [
        {"timestamp": processing["data_as_of"] + 1, "flow_usd": 1.0,
         "provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"}
    ]
    chart = invoke(processing, classification)["charts"]["etf_flow_daily"]
    assert chart["status"] == "invalid" and chart["points"] == []


@pytest.mark.parametrize("field", ("open", "high", "low", "close"))
@pytest.mark.parametrize("value", (None, True, {}, []))
def test_cba43_cba58_invalid_exchange_candle_is_rejected(field, value):
    processing, classification = contracts()
    processing["series"]["exchange_balance"][0][field] = value
    chart = invoke(processing, classification)["charts"]["exchange_balance"]
    assert chart["status"] == "invalid" and chart["candles"] == []


def test_cba59_invalid_fund_does_not_degrade_valid_fund():
    processing, classification = contracts()
    processing["snapshots"]["funds"][0]["ticker"] = True
    table = invoke(processing, classification)["tables"]["etf_funds"]
    assert table["status"] == "partial" and len(table["rows"]) == 1
    assert table["rows"][0]["ticker"] == "IBIT"


def test_cba60_classification_wrapper_future_is_invalid():
    processing, classification = contracts()
    classification["classifications"]["etf_flow_persistence"]["data_as_of"] = processing["data_as_of"] + 1
    output = invoke(processing, classification)
    wrapper = output["classification_states"]["etf_flow_persistence"]
    assert wrapper["status"] == "invalid" and wrapper["reason"] == "upstream_timestamp_inconsistent"


def test_cba61_json_rejects_non_json_classification_evidence():
    processing, classification = contracts()
    classification["classifications"]["gbtc_premium_regime"]["evidence"] = {"bad": Decimal("1")}
    with pytest.raises(ValueError, match="non_json_contract_value"):
        invoke(processing, classification)


def test_cba62_upstreams_are_immutable_even_on_fallback():
    processing, classification = contracts()
    before_processing, before_classification = deepcopy(processing), deepcopy(classification)
    processing["family"] = "wrong"
    before_processing = deepcopy(processing)
    output = invoke(processing, classification)
    json.dumps(output, allow_nan=False)
    assert processing == before_processing and classification == before_classification
