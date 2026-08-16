from copy import deepcopy
import json

import pytest

from etf_exchange_flows_processing_helpers import NOW, cloned_input
from processing_signals.processing.etf_exchange_flows import (
    EtfExchangeFlowsFeatureBuilder,
    EtfExchangeFlowsProcessor,
    process_etf_exchange_flows,
    run_etf_exchange_flows_processing,
)


def test_public_api_and_exact_root_metadata():
    output = process_etf_exchange_flows(input_contract=cloned_input(), generated_at=NOW)
    assert output["family"] == "etf_exchange_flows" and output["stage"] == "processing" and output["version"] == "0.1"
    assert output["mode"] == "bootstrap" and output["data_mode"] == "live" and output["is_demo"] is False
    assert set(output) == {"family", "stage", "version", "mode", "data_mode", "is_demo", "generated_at", "data_as_of",
                           "features", "series", "series_metadata", "snapshots", "provenance", "quality", "technical_analysis"}
    assert run_etf_exchange_flows_processing(input_contract=cloned_input(), generated_at=NOW) == output
    assert EtfExchangeFlowsProcessor().process(input_contract=cloned_input(), generated_at=NOW) == output
    assert EtfExchangeFlowsFeatureBuilder().build(input_contract=cloned_input(), generated_at=NOW)["features"] == output["features"]


def test_exchange_balance_ta_is_precomputed_in_processing():
    output = process_etf_exchange_flows(input_contract=cloned_input(hourly=240), generated_at=NOW)
    candles = output["series"]["exchange_balance"]
    technical = output["technical_analysis"]
    assert candles and technical["status"] == "available"
    assert set(technical["regression_channel"]["series"]) == {"middle", "upper", "lower"}
    assert all(len(values) == len(candles) for values in technical["regression_channel"]["series"].values())
    assert set(technical["indicators"]["tsi"]["series"]) == {"tsi", "signal"}


@pytest.mark.parametrize("contract", [None, {}, {"family": "wrong"}])
def test_root_validation(contract):
    with pytest.raises(ValueError, match="invalid_processing_input"):
        process_etf_exchange_flows(input_contract=contract, generated_at=NOW)


def test_generated_at_requires_explicit_or_valid_input_clock():
    contract = cloned_input()
    contract["generated_at"] = None
    with pytest.raises(ValueError, match="invalid_processing_input"):
        process_etf_exchange_flows(input_contract=contract)
    output = process_etf_exchange_flows(input_contract=contract, generated_at=NOW)
    assert output["generated_at"].endswith("Z")


def test_provenance_has_formulas_parameters_and_no_raw():
    output = process_etf_exchange_flows(input_contract=cloned_input(), generated_at=NOW)
    provenance = output["provenance"]
    assert provenance["input_family"] == "etf_exchange_flows"
    assert provenance["parameters"]["pressure_window_seconds"] == 86400
    assert "exchange_flow_pressure_24h" in provenance["formulas"]
    assert "raw" not in json.dumps(provenance).lower()


def test_deep_immutability_and_output_isolation():
    source = cloned_input()
    before = deepcopy(source)
    output = process_etf_exchange_flows(input_contract=source, generated_at=NOW)
    assert source == before
    output["series"]["etf_flow_daily"][0]["flow_usd"] = 991001
    assert source == before
    source["datasets"]["etf_flows_daily"][0]["flow_usd"] = 992002
    assert output["series"]["etf_flow_daily"][0]["flow_usd"] != 992002


def test_strict_json_and_canonical_series_shapes():
    output = process_etf_exchange_flows(input_contract=cloned_input(), generated_at=NOW)
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)
    assert set(output["series"]["exchange_inflow"]) == {"hour", "day"}
    assert "inflow_total" in output["series"]["exchange_inflow"]["hour"][0]
    assert "outflow_total" not in output["series"]["exchange_inflow"]["hour"][0]
