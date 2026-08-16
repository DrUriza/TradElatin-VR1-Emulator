"""Regressions for the second read-only Processing audit."""
from copy import deepcopy
import json

from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import process_etf_exchange_flows


def output(contract):
    return process_etf_exchange_flows(input_contract=contract, generated_at=NOW)


def test_netflow_explicit_usd_is_invalid_and_cannot_be_reconciled():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_netflow"]["hour"]:
        row["unit"] = "USD"
    result = output(contract)
    reported = result["features"]["exchange_flows"]["netflow_24h_reported"]
    reconciliation = result["features"]["provider_reconciliation"]["netflow"]
    assert (reported["value"], reported["status"], reported["reason"]) == (None, "invalid", "invalid_unit")
    assert reported["unit"] == "BTC" and "invalid_unit" in reported["warnings"]
    assert reconciliation["difference"]["value"] is None
    assert reconciliation["difference"]["status"] == "unavailable"
    assert reconciliation["difference"]["reason"] == "invalid_unit"


def test_netflow_absent_or_explicit_btc_unit_remains_valid():
    implicit = output(cloned_input())["features"]["exchange_flows"]["netflow_24h_reported"]
    contract = cloned_input()
    for row in contract["datasets"]["exchange_netflow"]["hour"]:
        row["unit"] = "BTC"
    explicit = output(contract)["features"]["exchange_flows"]["netflow_24h_reported"]
    assert implicit["value"] == explicit["value"] == 24
    assert implicit["status"] == explicit["status"] == "available"


def test_one_incompatible_netflow_observation_invalidates_the_aggregate():
    contract = cloned_input()
    contract["datasets"]["exchange_netflow"]["hour"][5]["unit"] = "USD"
    reported = output(contract)["features"]["exchange_flows"]["netflow_24h_reported"]
    assert reported["value"] is None
    assert (reported["status"], reported["reason"]) == ("invalid", "invalid_unit")
    assert reported["coverage"]["samples_received"] == 24
    assert reported["coverage"]["samples_valid"] == 23
    assert reported["coverage"]["samples_rejected"] == 1


def test_future_balance_history_degrades_retained_series_locally():
    contract = cloned_input()
    future = deepcopy(contract["datasets"]["exchange_balances_history"][-1])
    future.update(timestamp=NOW + 1, balance_btc=991001)
    contract["datasets"]["exchange_balances_history"].append(future)
    result = output(contract)
    history = result["series"]["exchange_balance_source_points"]
    assert len(history) == 1 and history[0]["balance_btc"] == 100
    assert (history[0]["status"], history[0]["reason"]) == ("partial", "future_timestamp")
    assert history[0]["warnings"] == ["future_timestamp"]
    assert history[0]["future_records_excluded"] == 1
    assert result["quality"]["status"] == "ok"


def test_future_counters_are_exposed_without_raw_or_sentinel():
    contract = cloned_input()
    future_etf = deepcopy(contract["datasets"]["etf_flows_daily"][-1])
    future_etf.update(timestamp=NOW + 1, flow_usd=991001)
    future_history = deepcopy(contract["datasets"]["exchange_balances_history"][-1])
    future_history.update(timestamp=NOW + 2, balance_btc=992002)
    contract["datasets"]["etf_flows_daily"].append(future_etf)
    contract["datasets"]["exchange_balances_history"].append(future_history)
    result = output(contract)
    anomalies = result["provenance"]["anomalies"]
    assert anomalies["future_records_excluded"] == 2
    assert anomalies["future_records_by_dataset"] == {"etf_flows_daily": 1, "exchange_balances_history": 1}
    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "991001" not in serialized and "992002" not in serialized


def test_negative_rejection_counters_are_exposed_by_feature():
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][4]["inflow_total"] = -10
    contract["datasets"]["exchange_outflow"]["hour"][8]["outflow_total"] = -20
    anomalies = output(contract)["provenance"]["anomalies"]
    assert anomalies["negative_observations_rejected"] == 2
    assert anomalies["negative_observations_by_feature"] == {"inflow_24h": 1, "outflow_24h": 1}


def test_anomaly_metadata_is_json_safe_deterministic_and_immutable():
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][0]["inflow_total"] = -1
    future = deepcopy(contract["datasets"]["etf_flows_daily"][-1])
    future["timestamp"] = NOW + 1
    contract["datasets"]["etf_flows_daily"].append(future)
    before = deepcopy(contract)
    first = output(contract)
    second = output(contract)
    assert first == second and contract == before
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    first["provenance"]["anomalies"]["future_records_by_dataset"]["etf_flows_daily"] = 99
    assert second["provenance"]["anomalies"]["future_records_by_dataset"]["etf_flows_daily"] == 1
