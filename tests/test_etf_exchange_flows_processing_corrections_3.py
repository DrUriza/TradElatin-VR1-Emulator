"""Regressions for entity-local future anomalies and canonical provenance paths."""
from copy import deepcopy
import json
from pathlib import Path

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import process_etf_exchange_flows


def output(contract):
    return process_etf_exchange_flows(input_contract=contract, generated_at=NOW)


def balance(exchange, symbol, value, timestamp=NOW):
    return {"timestamp": timestamp, "exchange_name": exchange, "balance_btc": value, "price_usd": 60.0,
            "symbol": symbol, "provider": "coinglass", "endpoint_id": "exchange_balance_chart"}


def test_etf_pc3_01_pc3_03_future_isolated_by_exchange_and_symbol():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [
        balance("A", "BTC", 100), balance("A", "BTC", 991001, NOW + 1),
        balance("B", "BTC", 200), balance("A", "ETH", 300),
    ]
    result = output(contract)
    rows = {(row["exchange_name"], row["symbol"]): row for row in result["series"]["exchange_balance_source_points"]}
    assert (rows[("A", "BTC")]["status"], rows[("A", "BTC")]["reason"],
            rows[("A", "BTC")]["future_records_excluded"]) == ("partial", "future_timestamp", 1)
    for key in (("B", "BTC"), ("A", "ETH")):
        assert (rows[key]["status"], rows[key]["reason"], rows[key]["warnings"],
                rows[key]["future_records_excluded"]) == ("available", None, [], 0)
    assert rows[("A", "BTC")]["warnings"] is not rows[("B", "BTC")]["warnings"]


def test_etf_pc3_04_pc3_05_only_future_has_local_invalid_metadata_without_row():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance("A", "BTC", 991001, NOW + 1)]
    result = output(contract)
    assert result["series"]["exchange_balance_source_points"] == []
    assert result["series_metadata"]["exchange_balance"] == {
        "status": "invalid", "reason": "future_timestamp", "warnings": ["future_timestamp"],
        "records_available": 0, "future_records_excluded": 1,
        "future_records_by_entity": [{"exchange_name": "A", "symbol": "BTC", "provider": "coinglass",
            "endpoint_id": "exchange_balance_chart", "future_records_excluded": 1}],
        "first_timestamp": None, "last_timestamp": None,
    }
    assert "991001" not in json.dumps(result, allow_nan=False)


def test_etf_pc3_06_pc3_07_multiple_futures_have_exact_entity_and_total_counts():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"] = [balance("A", "BTC", 100),
        balance("A", "BTC", 1, NOW + 1), balance("A", "BTC", 2, NOW + 2), balance("B", "BTC", 3, NOW + 3)]
    result = output(contract)
    metadata = result["series_metadata"]["exchange_balance"]
    counts = {(item["exchange_name"], item["symbol"]): item["future_records_excluded"]
              for item in metadata["future_records_by_entity"]}
    assert metadata["future_records_excluded"] == sum(counts.values()) == 3
    assert counts == {("A", "BTC"): 2, ("B", "BTC"): 1}
    assert result["provenance"]["anomalies"]["future_records_excluded"] == 3


def test_etf_pc3_08_pc3_13_cryptoquant_hour_day_paths_stay_separate():
    contract = cloned_input()
    expected = {}
    for dataset in ("exchange_inflow", "exchange_outflow", "exchange_netflow", "exchange_reserve"):
        hour = deepcopy(contract["datasets"][dataset]["hour"][-1])
        hour["timestamp"] = NOW + 1
        day = deepcopy(hour)
        day.update(timestamp=NOW + 2, window="day")
        contract["datasets"][dataset]["hour"].append(hour)
        contract["datasets"][dataset]["day"] = [day, {**day, "timestamp": NOW + 3}]
        expected[f"{dataset}.hour"] = 1
        expected[f"{dataset}.day"] = 2
    paths = output(contract)["provenance"]["anomalies"]["future_records_by_dataset"]
    assert paths == expected
    assert not any(key in paths for key in ("exchange_inflow", "exchange_outflow", "exchange_netflow", "exchange_reserve"))


def test_etf_pc3_14_pc3_16_secondary_path_and_multidataset_total_are_exact():
    contract = cloned_input()
    etf = deepcopy(contract["datasets"]["etf_flows_daily"][-1])
    etf["timestamp"] = NOW + 1
    contract["datasets"]["etf_flows_daily"].append(etf)
    inflow = deepcopy(contract["datasets"]["exchange_inflow"]["hour"][-1])
    inflow["timestamp"] = NOW + 2
    contract["datasets"]["exchange_inflow"]["hour"].append(inflow)
    gn = deepcopy(contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"][-1])
    gn["timestamp"] = NOW + 3
    contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"].append(gn)
    anomalies = output(contract)["provenance"]["anomalies"]
    assert anomalies["future_records_by_dataset"] == {"etf_flows_daily": 1, "exchange_inflow.hour": 1,
        "secondary_sources.glassnode.exchange_balance.1h": 1}
    assert anomalies["future_records_excluded"] == sum(anomalies["future_records_by_dataset"].values()) == 3


def test_etf_pc3_17_optional_future_keeps_global_ok_and_metadata_partial():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"].append(balance("A", "BTC", 991001, NOW + 1))
    result = output(contract)
    assert result["quality"]["status"] == "ok"
    assert result["series_metadata"]["exchange_balance"]["status"] == "partial"
    assert result["data_as_of"] == NOW


def test_etf_pc3_18_pc3_20_json_immutability_and_output_isolation():
    contract = cloned_input()
    contract["datasets"]["exchange_balances_history"].append(balance("A", "BTC", 991001, NOW + 1))
    before = deepcopy(contract)
    first, second = output(contract), output(contract)
    assert contract == before and first == second
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    first["series_metadata"]["exchange_balance"]["warnings"].append("mutated")
    assert second["series_metadata"]["exchange_balance"]["warnings"] == ["future_timestamp"]


def test_etf_pc3_21_pc3_25_previous_corrections_regressions():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_netflow"]["hour"]:
        row["unit"] = "USD"
    result = output(contract)
    assert result["features"]["exchange_flows"]["netflow_24h_reported"]["reason"] == "invalid_unit"
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][0]["inflow_total"] = -1
    result = output(contract)
    assert result["features"]["exchange_flows"]["inflow_24h"]["reason"] == "negative_flow_observation"
    assert result["provenance"]["anomalies"]["negative_observations_rejected"] == 1


def test_etf_pc3_26_pc3_30_boundaries_hashes_and_contract():
    root = Path(__file__).parents[1]
    expected = {"etf_exchange_flows_data_raw_extract.py": "AF591861B05961161A38B422B211B1FFDE3A3080B98A27338FE0D5E227682298",
        "etf_exchange_flows_data_raw_preprocessing.py": "053D57C3C8A867545C9E83C56CE3B3D309260B9474ED7D8328992DFA9869183C"}
    folder = root / "src/processing_signals/input/etf_exchange_flows"
    assert {name: canonical_text_sha256(folder / name) for name in expected} == expected
    result = output(cloned_input())
    assert result["family"] == "etf_exchange_flows" and result["stage"] == "processing"
