from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy

import pytest

import processing_signals.main.cvd_volume_orderflow.cvd_volume_orderflow_vertical as vertical
from processing_signals.main.cvd_volume_orderflow import build_cvd_volume_orderflow_screen, run_cvd_volume_orderflow_vertical

INPUT_TEST = Path(__file__).with_name("test_cvd_volume_orderflow_input_vertical.py")


@pytest.fixture(scope="session")
def helpers():
    return runpy.run_path(str(INPUT_TEST))


@pytest.fixture(scope="session")
def input_contract(helpers):
    return helpers["small_input"]()


def test_public_api_and_exact_screen(input_contract, helpers):
    output = build_cvd_volume_orderflow_screen(input_contract, display_point_limit=1, clock=lambda: helpers["REFERENCE"])
    assert tuple(output) == vertical.SCREEN_ROOT
    assert output["schema"] == {"id": "trad_elatin.cvd_volume_orderflow.screen.v1", "version": "1.5.0"}
    assert output["screen"]["route"] == "/cvd-orderflow"
    assert output["context"]["markets"] == ["spot", "futures"]
    json.dumps(output, ensure_ascii=False, allow_nan=False)


def test_call_order_bundle_and_immutability(monkeypatch, input_contract, helpers):
    processing = vertical.process_cvd_volume_orderflow(copy.deepcopy(input_contract), clock=lambda: helpers["REFERENCE"])
    classification = vertical.classify_cvd_volume_orderflow(copy.deepcopy(processing), clock=lambda: helpers["REFERENCE"])
    expected = vertical.build_cvd_volume_orderflow_contract({"processing": processing, "classification": classification}, display_point_limit=1)
    calls = []
    monkeypatch.setattr(vertical, "process_cvd_volume_orderflow", lambda value, **kwargs: calls.append("processing") or copy.deepcopy(processing))
    monkeypatch.setattr(vertical, "classify_cvd_volume_orderflow", lambda value, **kwargs: calls.append("classification") or copy.deepcopy(classification))
    monkeypatch.setattr(vertical, "build_cvd_volume_orderflow_contract", lambda bundle, **kwargs: calls.append(("builder", set(bundle))) or copy.deepcopy(expected))
    before = copy.deepcopy(input_contract)
    output = build_cvd_volume_orderflow_screen(input_contract, display_point_limit=1, clock=lambda: helpers["REFERENCE"])
    assert calls == ["processing", "classification", ("builder", {"processing", "classification"})]
    assert input_contract == before and output == expected


@pytest.mark.parametrize("value", [None, [], "x", 1, True])
def test_invalid_input_rejected(value):
    with pytest.raises(ValueError, match="cvd_vertical_invalid:input"):
        build_cvd_volume_orderflow_screen(value)


@pytest.mark.parametrize("kwargs", [{"selected_market": "x"}, {"selected_timeframe": "2h"},
    {"display_point_limit": True}, {"display_point_limit": 0}, {"display_point_limit": 221}])
def test_invalid_visual_options(input_contract, kwargs):
    with pytest.raises(ValueError, match="cvd_vertical_invalid"):
        build_cvd_volume_orderflow_screen(input_contract, **kwargs)


def test_debug_is_strict_and_independent(input_contract, helpers):
    output = build_cvd_volume_orderflow_screen(input_contract, display_point_limit=1,
        clock=lambda: helpers["REFERENCE"], include_debug_bundle=True)
    assert tuple(output) == ("input", "processing", "classification", "screen")
    output["input"]["context"]["base_asset"] = "ETH"
    assert output["processing"]["context"]["base_asset"] == "BTC"
    json.dumps(output, allow_nan=False)


def test_runtime_bootstrap_and_state_modes(helpers):
    options = {"fetcher": helpers["fetcher"], "reference_timestamp": helpers["REFERENCE"],
        "clock": lambda: helpers["REFERENCE"], "target_display_records": 1, "warmup_records": 0,
        "display_point_limit": 1, "include_footprint": False, "include_cryptoquant_confirmation": False,
        "include_glassnode_confirmation": False}
    bootstrap = run_cvd_volume_orderflow_vertical(**options)
    initial = helpers["small_input"]()
    incremental = run_cvd_volume_orderflow_vertical(**{**options, "mode": "incremental",
        "existing_input": initial, "reference_timestamp": helpers["REFERENCE"] + 60})
    request = {"market": "spot", "timeframe": "1m", "start_timestamp": helpers["REFERENCE"] - 60,
        "end_timestamp": helpers["REFERENCE"], "records_required": 2}
    recovery = run_cvd_volume_orderflow_vertical(**{**options, "mode": "recovery", "existing_input": initial,
        "recovery_requests": [request]})
    assert (bootstrap["mode"], incremental["mode"], recovery["mode"]) == ("bootstrap", "incremental", "recovery")
