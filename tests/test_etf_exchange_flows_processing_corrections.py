"""Persistent regressions for the three Processing v0.1 audit corrections."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import (
    build_etf_exchange_flows_features,
    process_etf_exchange_flows,
    run_etf_exchange_flows_processing,
)


def output(contract):
    return process_etf_exchange_flows(input_contract=contract, generated_at=NOW)


def future_row(row, **updates):
    result = deepcopy(row)
    result.update(timestamp=NOW + 1, **updates)
    return result


def test_etf_pc01_pc04_reported_netflow_preserves_observed_anchor_and_exact_alignment():
    contract = cloned_input()
    contract["datasets"]["exchange_netflow"]["hour"] = contract["datasets"]["exchange_netflow"]["hour"][:-2]
    result = output(contract)
    reported = result["features"]["exchange_flows"]["netflow_24h_reported"]
    reconciliation = result["features"]["provider_reconciliation"]["netflow"]
    assert (reported["value"], reported["timestamp"], reported["data_as_of"]) == (22.0, NOW - 7200, NOW - 7200)
    assert reconciliation["reported_anchor"] == NOW - 7200
    assert reconciliation["calculated_anchor"] == NOW
    assert reconciliation["timestamp_distance"] == 7200
    assert reconciliation["difference"]["value"] is None
    assert reconciliation["difference"]["status"] == "unavailable"
    assert reconciliation["difference"]["reason"] == "anchors_not_aligned"


def test_etf_pc02_aligned_netflow_reconciles():
    contract = cloned_input()
    contract["datasets"]["exchange_netflow"]["hour"][-2]["netflow_total"] = 0
    contract["datasets"]["exchange_netflow"]["hour"][-1]["netflow_total"] = 0
    reconciliation = output(contract)["features"]["provider_reconciliation"]["netflow"]
    assert reconciliation["reported_anchor"] == reconciliation["calculated_anchor"] == NOW
    assert reconciliation["difference"]["value"] == pytest.approx(2.0)
    assert reconciliation["difference"]["status"] == "available"


@pytest.mark.parametrize(("dataset", "field"), [("exchange_inflow", "inflow_total"), ("exchange_outflow", "outflow_total")])
def test_etf_pc05_pc08_negative_observation_cannot_be_compensated(dataset, field):
    contract = cloned_input()
    contract["datasets"][dataset]["hour"][-2][field] = -10
    contract["datasets"][dataset]["hour"][-1][field] = 100
    result = output(contract)
    feature_name = "inflow_24h" if dataset.endswith("inflow") else "outflow_24h"
    feature = result["features"]["exchange_flows"][feature_name]
    assert feature["value"] is None
    assert (feature["status"], feature["reason"]) == ("invalid", "negative_flow_observation")
    assert feature["coverage"]["samples_valid"] == 23
    assert feature["coverage"]["samples_rejected"] == 1
    assert result["features"]["pressure"]["flow_24h"]["value"] is None
    assert result["features"]["pressure"]["flow_24h"]["reason"] == "source_invalid"
    assert result["features"]["exchange_flows"]["netflow_24h_calculated"]["value"] is None


def test_etf_pc09_reported_netflow_may_be_negative():
    contract = cloned_input()
    for row in contract["datasets"]["exchange_netflow"]["hour"]:
        row["netflow_total"] = -1
    reported = output(contract)["features"]["exchange_flows"]["netflow_24h_reported"]
    assert reported["value"] == -24
    assert reported["status"] == "available"


def test_etf_pc10_pc12_negative_component_blocks_composites_and_quality_is_partial():
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][0]["inflow_total"] = -1
    result = output(contract)
    assert result["features"]["pressure"]["flow_24h"]["status"] == "unavailable"
    assert result["features"]["exchange_flows"]["netflow_24h_calculated"]["reason"] == "source_invalid"
    assert result["quality"]["status"] == "partial"
    assert result["quality"]["required_invalid"] == 1


def test_etf_pc13_pc14_future_etf_with_history_is_partial_and_all_future_is_invalid():
    contract = cloned_input()
    contract["datasets"]["etf_flows_daily"].append(future_row(contract["datasets"]["etf_flows_daily"][-1], flow_usd=991001))
    result = output(contract)
    latest = result["features"]["etf"]["net_flow_usd_latest"]
    assert (latest["value"], latest["status"], latest["reason"]) == (-120.0, "partial", "future_timestamp")
    assert "future_timestamp" in latest["warnings"] and result["quality"]["status"] == "partial"
    assert 991001 not in json.loads(json.dumps(result, allow_nan=False)).values()
    all_future = cloned_input()
    all_future["datasets"]["etf_flows_daily"] = [future_row(all_future["datasets"]["etf_flows_daily"][-1])]
    latest = output(all_future)["features"]["etf"]["net_flow_usd_latest"]
    assert (latest["value"], latest["status"], latest["reason"]) == (None, "invalid", "future_timestamp")


@pytest.mark.parametrize(("dataset", "path", "feature_path"), [
    ("etf_net_assets_daily", (), ("etf", "reported_total_aum_usd")),
    ("etf_premium_discount_daily", (), ("premium_discount", "gbtc_latest")),
    ("exchange_inflow", ("hour",), ("exchange_flows", "inflow_24h")),
    ("exchange_outflow", ("hour",), ("exchange_flows", "outflow_24h")),
    ("exchange_netflow", ("hour",), ("exchange_flows", "netflow_24h_reported")),
    ("exchange_reserve", ("hour",), ("exchange_balances", "cryptoquant_reserve")),
])
def test_etf_pc15_pc20_future_required_datasets_degrade_with_history(dataset, path, feature_path):
    contract = cloned_input()
    rows = contract["datasets"][dataset]
    for key in path:
        rows = rows[key]
    rows.append(future_row(rows[-1]))
    result = output(contract)
    feature = result["features"]
    for key in feature_path:
        feature = feature[key]
    assert feature["value"] is not None
    assert (feature["status"], feature["reason"]) == ("partial", "future_timestamp")
    assert result["quality"]["status"] == "partial"
    assert result["data_as_of"] <= NOW


def test_etf_pc21_pc25_future_secondary_isolated_and_does_not_advance_anchor():
    contract = cloned_input()
    records = contract["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["1h"]
    records.append(future_row(records[-1], value=993003, value_raw=993003))
    result = output(contract)
    feature = result["features"]["exchange_balances"]["glassnode_secondary"]
    assert (feature["value"], feature["status"], feature["reason"]) == (990.0, "partial", "future_timestamp")
    assert result["quality"]["status"] == "ok"
    assert result["data_as_of"] == NOW


def test_etf_pc24_future_optional_does_not_degrade_global():
    contract = cloned_input()
    rows = contract["datasets"]["etf_fund_flows_daily"]
    rows.append(future_row(rows[-1], flow_usd=992002))
    result = output(contract)
    assert result["snapshots"]["funds"][0]["periods"]["1d"]["period_flow_usd"]["status"] == "partial"
    assert result["quality"]["status"] == "ok"


def test_etf_pc26_common_anchor_excludes_future_inflow():
    contract = cloned_input()
    rows = contract["datasets"]["exchange_inflow"]["hour"]
    rows.append(future_row(rows[-1], inflow_total=994004))
    result = output(contract)
    inflow = result["features"]["exchange_flows"]["inflow_24h"]
    pressure = result["features"]["pressure"]["flow_24h"]
    assert inflow["value"] == 48 and inflow["status"] == "partial"
    assert inflow["requested_anchor"] == inflow["observed_anchor"] == NOW
    assert pressure["value"] == pytest.approx(1 / 3) and pressure["status"] == "partial"


def test_etf_pc27_future_netflow_does_not_hide_misalignment():
    contract = cloned_input()
    rows = contract["datasets"]["exchange_netflow"]["hour"]
    del rows[-2:]
    rows.append(future_row(rows[-1], netflow_total=993003))
    reconciliation = output(contract)["features"]["provider_reconciliation"]["netflow"]
    assert reconciliation["reported"]["status"] == "partial"
    assert reconciliation["difference"]["reason"] == "anchors_not_aligned"
    assert reconciliation["timestamp_distance"] == 7200


def test_etf_pc28_future_negative_precedence_is_temporal():
    contract = cloned_input()
    rows = contract["datasets"]["exchange_inflow"]["hour"]
    rows.append(future_row(rows[-1], inflow_total=-10))
    inflow = output(contract)["features"]["exchange_flows"]["inflow_24h"]
    assert inflow["value"] == 48
    assert (inflow["status"], inflow["reason"]) == ("partial", "future_timestamp")


def test_etf_pc29_provenance_records_exact_alignment_metadata():
    contract = cloned_input()
    del contract["datasets"]["exchange_netflow"]["hour"][-2:]
    provenance = output(contract)["provenance"]["reconciliations"]["netflow"]
    assert provenance == {"calculated_anchor": NOW, "reported_anchor": NOW - 7200,
                          "timestamp_distance": 7200, "window_seconds": 86400,
                          "scope": "all_exchange", "alignment_required": "exact"}


def test_etf_pc30_pc31_strict_json_and_deep_immutability_after_anomalies():
    contract = cloned_input()
    contract["datasets"]["exchange_inflow"]["hour"][0]["inflow_total"] = -10
    contract["datasets"]["etf_flows_daily"].append(future_row(contract["datasets"]["etf_flows_daily"][-1]))
    before = deepcopy(contract)
    first = output(contract)
    second = run_etf_exchange_flows_processing(input_contract=contract, generated_at=NOW)
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    assert contract == before and first == second
    first["features"]["etf"]["net_flow_usd_latest"]["warnings"].append("mutated")
    assert contract == before and first != second
    assert build_etf_exchange_flows_features(input_contract=contract, generated_at=NOW)


def test_etf_pc32_input_hashes_unchanged():
    root = Path(__file__).parents[1]
    expected = {
        "etf_exchange_flows_data_raw_extract.py": "AF591861B05961161A38B422B211B1FFDE3A3080B98A27338FE0D5E227682298",
        "etf_exchange_flows_data_raw_preprocessing.py": "053D57C3C8A867545C9E83C56CE3B3D309260B9474ED7D8328992DFA9869183C",
    }
    folder = root / "src/processing_signals/input/etf_exchange_flows"
    assert {name: canonical_text_sha256(folder / name) for name in expected} == expected


def test_etf_pc33_pc36_boundaries_and_regression_contract():
    result = output(cloned_input())
    forbidden = {"classification", "confidence", "display_value", "color_token", "widget", "screen"}
    assert not forbidden.intersection(json.dumps(result).lower().replace('"', " ").split())
    assert (result["family"], result["stage"], result["version"]) == ("etf_exchange_flows", "processing", "0.1")
