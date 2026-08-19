from copy import deepcopy
import json
import math

import pytest

from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import process_etf_exchange_flows
from processing_signals.processing.etf_exchange_flows.etf_exchange_flows_feature_builder import _finite


def output(contract=None, **kwargs):
    return process_etf_exchange_flows(input_contract=contract or cloned_input(), generated_at=NOW, **kwargs)


@pytest.mark.parametrize("case_id", [f"ETF-P{number:02d}" for number in (2, 8, 9, 10, 11, 12, 17, 20, 25, 26, 28, 29, 30, 33, 34, 35, 41, 42, 45, 48, 49, 50)])
def test_core_contract_invariants(case_id):
    result = output()
    serialized = json.dumps(result, allow_nan=False)
    assert result["family"] == "etf_exchange_flows" and result["stage"] == "processing"
    assert result["features"]["etf"]["net_flow_usd_latest"]["value"] == -120
    assert result["features"]["etf"]["net_flow_btc_latest"]["value"] == -2
    assert result["features"]["pressure"]["flow_24h"]["timestamp"] == NOW
    assert not any(token in serialized for token in ('"classification"', '"confidence"', '"widget"', '"screen"'))
    native = result["capital_flow_analysis"]
    assert native["recalculate_in_hmi"] is False
    assert set(native["indicators"]) == {
        "etf_flow_momentum_persistence", "etf_flow_zscore", "btc_etf_flow_divergence",
        "exchange_flow_pressure", "exchange_reserve_change", "capital_regime_wasserstein",
    }


def test_etf_p03_root_validation():
    with pytest.raises(ValueError, match="invalid_processing_input"):
        process_etf_exchange_flows(input_contract=[], generated_at=NOW)


def test_etf_p04_p05_p46_numeric_safety():
    assert _finite(True) is None and _finite(math.nan) is None and _finite(math.inf) is None
    assert _finite(-0.0) == 0.0 and math.copysign(1, _finite(-0.0)) == 1


def test_etf_p06_future_timestamp():
    contract = cloned_input()
    contract["datasets"]["etf_flows_daily"] = [{**contract["datasets"]["etf_flows_daily"][-1], "timestamp": NOW+1}]
    feature = output(contract)["features"]["etf"]["net_flow_usd_latest"]
    assert feature["status"] == "invalid" and feature["reason"] == "future_timestamp"


def test_etf_p07_duplicate_is_deterministic():
    contract = cloned_input()
    duplicate = deepcopy(contract["datasets"]["etf_flows_daily"][-1])
    duplicate["flow_usd"] = 77
    contract["datasets"]["etf_flows_daily"].append(duplicate)
    result = output(contract)
    assert result["features"]["etf"]["net_flow_usd_latest"]["value"] == 77
    assert "duplicate_input_record" in result["quality"]["warnings"]


def test_etf_p13_missing_price_partial():
    contract = cloned_input()
    contract["datasets"]["etf_flows_daily"][-1]["price_usd"] = None
    result = output(contract)["features"]["etf"]
    assert result["period_flow_btc"]["7d"]["status"] == "partial"


def test_etf_p14_p15_p16_signed_share_and_zero_denominator():
    result = output()["snapshots"]["funds"]
    assert sum(abs(fund["periods"]["1d"]["period_signed_flow_share"]["value"]) for fund in result) == 1
    contract = cloned_input()
    for row in contract["datasets"]["etf_fund_flows_daily"]:
        row["flow_usd"] = 0
    zero = output(contract)["snapshots"]["funds"][0]
    assert zero["periods"]["1d"]["period_signed_flow_share"]["reason"] == "invalid_denominator"
    assert zero["issuer_flow"]["reason"] == "issuer_identity_unavailable"


def test_etf_p18_p19_aum_partial_and_reconciliation():
    contract = cloned_input()
    contract["datasets"]["etf_funds_snapshot"][0]["aum_usd"] = None
    result = output(contract)["features"]
    assert result["etf"]["calculated_fund_aum_usd"]["status"] == "partial"
    assert result["provider_reconciliation"]["aum"]["reported"]["value"] == 1100


def test_etf_p21_p23_p24_p32_common_anchor_and_coverage():
    contract = cloned_input(hourly=5)
    contract["datasets"]["exchange_outflow"]["hour"][-1]["timestamp"] = NOW-3600
    result = output(contract)["features"]
    common = NOW-3600
    assert result["exchange_flows"]["inflow_24h"]["timestamp"] == common
    assert result["exchange_flows"]["outflow_24h"]["timestamp"] == common
    assert result["pressure"]["flow_24h"]["status"] == "partial"


def test_etf_p27_netflow_reconciliation_uses_common_anchor():
    contract = cloned_input()
    contract["datasets"]["exchange_netflow"]["hour"].append({**contract["datasets"]["exchange_netflow"]["hour"][-1],
        "timestamp": NOW+3600, "netflow_total": 991001})
    result = output(contract)["features"]["provider_reconciliation"]["netflow"]
    assert result["reported"]["timestamp"] == NOW and result["difference"]["value"] == 0


def test_etf_p22_p34_scope_mismatch():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_outflow"]["hour"]:
        row["exchange_scope"] = "other"
    pressure = output(contract)["features"]["pressure"]["flow_24h"]
    assert pressure["reason"] == "exchange_scope_mismatch"


@pytest.mark.parametrize(("inflow", "outflow", "expected", "reason"), [(2, 1, 1/3, None), (1, 2, -1/3, None), (1, 1, 0, None), (0, 0, None, "zero_total_flow")])
def test_etf_p28_to_p31_pressure(inflow, outflow, expected, reason):
    contract = cloned_input()
    for endpoint, field, value in (("exchange_inflow", "inflow_total", inflow), ("exchange_outflow", "outflow_total", outflow)):
        for row in contract["datasets"][endpoint]["hour"]:
            row[field] = value
    pressure = output(contract)["features"]["pressure"]["flow_24h"]
    assert pressure["reason"] == reason
    assert pressure["value"] == pytest.approx(expected) if expected is not None else pressure["value"] is None


def test_etf_p36_to_p40_glassnode_unit_and_spread():
    native = output()["features"]
    assert native["exchange_balances"]["glassnode_secondary"]["value"] == 990
    assert native["provider_reconciliation"]["exchange_balance"]["difference"] == 10
    for field, value in (("currency", "USD"), ("asset", "ETH")):
        contract = cloned_input()
        record = contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"][0]
        record[field] = value
        feature = output(contract)["features"]["exchange_balances"]["glassnode_secondary"]
        assert feature["status"] == "unavailable" and feature["reason"] == "provider_unit_unconfirmed"
    contract = cloned_input()
    record = contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"][0]
    record["value"], record["value_raw"] = None, {"x": 1}
    assert output(contract)["features"]["exchange_balances"]["glassnode_secondary"]["reason"] == "provider_unit_unconfirmed"
    contract = cloned_input()
    record = contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"][0]
    record["exchange_scope"] = "other"
    assert output(contract, exchange_scope="all_exchange")["features"]["exchange_balances"]["glassnode_secondary"]["reason"] == "exchange_scope_mismatch"


def test_etf_p33_pressure_rejects_negative_provider_flows():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_inflow"]["hour"]:
        row["inflow_total"] = -1
    pressure = output(contract)["features"]["pressure"]["flow_24h"]
    # The invalid source is identified on inflow; dependent composites carry no residual value.
    assert pressure["status"] == "unavailable" and pressure["reason"] == "source_invalid"


def test_etf_p43_p44_quality_isolation():
    contract = cloned_input()
    contract["datasets"]["etf_premium_discount_daily"] = []
    assert output(contract)["quality"]["status"] == "partial"
    empty = cloned_input()
    for key, value in list(empty["datasets"].items()):
        empty["datasets"][key] = {} if isinstance(value, dict) else []
    assert output(empty)["quality"]["status"] == "invalid"


def test_etf_p47_deep_immutability():
    contract = cloned_input()
    before = deepcopy(contract)
    result = output(contract)
    result["snapshots"]["funds"][0]["ticker"] = "MUTATED"
    assert contract == before
