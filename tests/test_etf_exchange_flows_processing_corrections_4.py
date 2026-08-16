"""PC4 regressions for safe identities and endpoint-specific provenance."""
from copy import deepcopy
import json
from pathlib import Path

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import process_etf_exchange_flows


def output(contract):
    return process_etf_exchange_flows(input_contract=contract, generated_at=NOW)


def balance(exchange="A", *, endpoint="exchange_balance_chart", timestamp=NOW, value=100.0):
    return {"timestamp": timestamp, "exchange_name": exchange, "balance_btc": value, "price_usd": 60.0,
            "symbol": "BTC", "provider": "coinglass", "endpoint_id": endpoint}


def test_etf_pc4_01_endpoint_mapping_does_not_raise_and_is_rejected():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance(endpoint={})]
    result = output(contract)
    metadata = result["series_metadata"]["exchange_balance"]
    assert result["series"]["exchange_balance_source_points"] == []
    assert metadata["status"] == "invalid" and metadata["reason"] == "invalid_entity_identity"
    assert metadata["invalid_entities"] == [{"identity_field": "endpoint_id",
        "reason": "invalid_entity_identity", "count": 1}]


def test_etf_pc4_02_pc4_04_bool_and_none_are_rejected_without_identity_collision():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance(True), balance(None)]
    result = output(contract)
    metadata = result["series_metadata"]["exchange_balance"]
    assert result["series"]["exchange_balance_source_points"] == []
    assert metadata["invalid_entities"] == [{"identity_field": "exchange_name",
        "reason": "invalid_entity_identity", "count": 2}]
    rendered = json.dumps(result, allow_nan=False)
    assert '"exchange_name": null' not in rendered


def test_etf_pc4_05_pc4_07_valid_entity_remains_and_local_status_is_partial():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance("A"), balance(True)]
    result = output(contract)
    assert len(result["series"]["exchange_balance_source_points"]) == 1
    assert result["series"]["exchange_balance_source_points"][0]["status"] == "available"
    metadata = result["series_metadata"]["exchange_balance"]
    assert (metadata["status"], metadata["reason"]) == ("partial", "invalid_entity_identity")


def test_etf_pc4_06_all_invalid_is_local_invalid():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance([]), balance(endpoint={})]
    metadata = output(contract)["series_metadata"]["exchange_balance"]
    assert (metadata["status"], metadata["reason"], metadata["records_available"]) == (
        "invalid", "invalid_entity_identity", 0)


def _future_secondary(endpoint, interval, seconds):
    return {"timestamp": NOW + seconds, "value": 1.0, "value_raw": 1.0, "asset": "BTC",
            "interval": interval, "exchange_scope": None, "provider": "glassnode", "endpoint_id": endpoint}


def test_etf_pc4_08_pc4_11_secondary_endpoint_interval_paths_and_exact_totals():
    contract = cloned_input()
    balance_root = contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]
    balance_root["1h"] = [_future_secondary("endpoint_a", "1h", 1),
                          _future_secondary("endpoint_b", "1h", 2)]
    balance_root["24h"] = [_future_secondary("endpoint_a", "24h", 3),
                           _future_secondary("endpoint_b", "24h", 4)]
    anomalies = output(contract)["provenance"]["anomalies"]
    assert anomalies["future_records_by_dataset"] == {
        "secondary_sources.glassnode.exchange_balance.endpoint_a.1h": 1,
        "secondary_sources.glassnode.exchange_balance.endpoint_a.24h": 1,
        "secondary_sources.glassnode.exchange_balance.endpoint_b.1h": 1,
        "secondary_sources.glassnode.exchange_balance.endpoint_b.24h": 1,
    }
    assert anomalies["future_records_excluded"] == sum(anomalies["future_records_by_dataset"].values()) == 4


def test_etf_pc4_12_pc4_13_strict_json_and_deep_immutability():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"].append(balance(True))
    before = deepcopy(contract)
    first = output(contract)
    second = output(contract)
    assert contract == before and first == second
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    first["series_metadata"]["exchange_balance"]["invalid_entities"][0]["count"] = 99
    assert second["series_metadata"]["exchange_balance"]["invalid_entities"][0]["count"] == 1


def test_etf_pc4_14_entity_isolation_by_exchange_symbol_provider_endpoint():
    contract = cloned_input()
    rows = [balance("A", endpoint="one"), balance("B", endpoint="one", value=2),
            {**balance("A", endpoint="two", value=3), "symbol": "ETH"},
            {**balance("A", endpoint="three", value=4), "provider": "other"}]
    contract["datasets"]["exchange_balances_history"] = rows
    result = output(contract)["series"]["exchange_balance_source_points"]
    assert len(result) == 4
    assert len({(row["exchange_name"], row["symbol"], row["provider"], row["endpoint_id"]) for row in result}) == 4


def test_etf_pc4_15_pc4_18_previous_unit_anchor_negative_and_future_regressions():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_netflow"]["hour"]:
        row["unit"] = "USD"
    result = output(contract)
    assert result["features"]["exchange_flows"]["netflow_24h_reported"]["reason"] == "invalid_unit"
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][0]["inflow_total"] = -1
    contract["datasets"]["etf_flows_daily"].append({**contract["datasets"]["etf_flows_daily"][-1],
                                                      "timestamp": NOW + 1, "flow_usd": 999999.0})
    result = output(contract)
    assert result["features"]["exchange_flows"]["inflow_24h"]["reason"] == "negative_flow_observation"
    assert result["features"]["etf"]["net_flow_usd_latest"]["value"] == -120.0
    anchored = output(cloned_input())
    assert anchored["features"]["pressure"]["flow_24h"]["window_end"] == NOW


def test_etf_pc4_19_pc4_20_input_hashes_and_no_registration():
    root = Path(__file__).parents[1]
    expected = {
        "etf_exchange_flows_data_raw_extract.py": "AF591861B05961161A38B422B211B1FFDE3A3080B98A27338FE0D5E227682298",
        "etf_exchange_flows_data_raw_preprocessing.py": "053D57C3C8A867545C9E83C56CE3B3D309260B9474ED7D8328992DFA9869183C",
    }
    folder = root / "src/processing_signals/input/etf_exchange_flows"
    actual = {name: canonical_text_sha256(folder / name) for name in expected}
    assert actual == expected
