"""Registration and connection tests for the frozen ETF exchange-flow vertical."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_helpers import Fetcher, NOW
from processing_signals.classification.classification_pipeline import (
    CLASSIFICATION_FAMILY_HANDLERS,
    run_classification_pipeline,
)
from processing_signals.input.input_pipeline import INPUT_FAMILY_HANDLERS, run_input_pipeline
from processing_signals.main.main_pipeline import VERTICAL_FAMILY_HANDLERS, run_main_pipeline
from processing_signals.processing.processing_pipeline import (
    PROCESSING_FAMILY_HANDLERS,
    run_processing_pipeline,
)

FAMILY = "etf_exchange_flows"


def input_arguments():
    return {"fetcher": Fetcher(), "requested_mode": "bootstrap", "exchange_scope": "all_exchange",
            "data_mode": "synthetic", "is_demo": True, "now": NOW}


def run_input():
    return run_input_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: input_arguments()})[FAMILY]


def run_processing(input_contract=None):
    source = run_input() if input_contract is None else input_contract
    return run_processing_pipeline(input_contracts={FAMILY: source}, enabled_families=(FAMILY,),
        now_timestamp=NOW)[FAMILY]


def run_classification(processing_contract=None):
    source = run_processing() if processing_contract is None else processing_contract
    return run_classification_pipeline(processing_contracts={FAMILY: source}, enabled_families=(FAMILY,))[FAMILY]


def test_etf_pipeline_01_registries_are_canonical_and_unique():
    for registry in (INPUT_FAMILY_HANDLERS, PROCESSING_FAMILY_HANDLERS,
                     CLASSIFICATION_FAMILY_HANDLERS, VERTICAL_FAMILY_HANDLERS):
        assert FAMILY in registry
        assert list(registry).count(FAMILY) == 1
        assert not ({"institutional_flows", "etf_flows", "exchange_flows"} & set(registry))


def test_etf_pipeline_02_input_pipeline_uses_frozen_facade():
    output = run_input()
    assert output["family"] == FAMILY and output["stage"] == "input"
    assert output["mode"] == "bootstrap" and output["data_mode"] == "synthetic"


def test_etf_pipeline_03_processing_receives_input_unchanged(monkeypatch):
    source = run_input()
    before = deepcopy(source)
    captured = {}

    def handler(input_contract, *, existing_processing, now_timestamp, family_arguments):
        captured["contract"] = input_contract
        return {"family": FAMILY, "stage": "processing"}

    monkeypatch.setitem(PROCESSING_FAMILY_HANDLERS, FAMILY, handler)
    output = run_processing_pipeline(input_contracts={FAMILY: source}, enabled_families=(FAMILY,))[FAMILY]
    assert output["stage"] == "processing" and captured["contract"] is source and source == before


def test_etf_pipeline_04_processing_pipeline_uses_frozen_facade():
    output = run_processing()
    assert output["family"] == FAMILY and output["stage"] == "processing" and output["version"] == "0.1"


def test_etf_pipeline_05_classification_receives_processing_unchanged(monkeypatch):
    source = run_processing()
    before = deepcopy(source)
    captured = {}

    def handler(processing_contract, family_arguments):
        captured["contract"] = processing_contract
        assert family_arguments == {}
        return {"family": FAMILY, "stage": "classification"}

    monkeypatch.setitem(CLASSIFICATION_FAMILY_HANDLERS, FAMILY, handler)
    output = run_classification_pipeline(processing_contracts={FAMILY: source}, enabled_families=(FAMILY,))[FAMILY]
    assert output["stage"] == "classification" and captured["contract"] is source and source == before


def test_etf_pipeline_06_classification_pipeline_uses_frozen_facade():
    output = run_classification()
    assert output["family"] == FAMILY and output["stage"] == "classification" and output["version"] == "0.1"


@pytest.mark.parametrize("selected_range", ("1d", "7d", "30d", "90d"))
def test_etf_pipeline_07_10_main_connects_full_chain(selected_range):
    output = run_main_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: {
        "fetcher": Fetcher(), "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                            "data_mode": "synthetic", "is_demo": True},
        "contract_arguments": {"selected_range": selected_range},
    }})[FAMILY]
    assert list(output) == ["input", "processing", "classification", "screen"]
    assert all(output[stage]["family"] == FAMILY for stage in ("input", "processing", "classification"))
    assert output["screen"]["screen"]["family"] == FAMILY
    assert output["screen"]["stage"] == "screen_contract"
    assert output["screen"]["range_selector"]["selected"] == selected_range
    json.dumps(output["screen"], ensure_ascii=False, allow_nan=False)


def test_etf_pipeline_11_main_screens_only_returns_screen_contract():
    output = run_main_pipeline(enabled_families=(FAMILY,), screens_only=True, family_arguments={FAMILY: {
        "fetcher": Fetcher(), "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                            "data_mode": "synthetic", "is_demo": True},
    }})[FAMILY]
    assert output["stage"] == "screen_contract" and output["screen"]["family"] == FAMILY


def test_etf_pipeline_12_invalid_range_propagates_exact_error():
    with pytest.raises(ValueError, match="^invalid_selected_range$"):
        run_main_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: {
            "fetcher": Fetcher(), "now_timestamp": NOW,
            "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                                "data_mode": "synthetic", "is_demo": True},
            "contract_arguments": {"selected_range": "365d"},
        }})


def test_etf_pipeline_13_main_passes_upstreams_intact_to_builder(monkeypatch):
    import processing_signals.main.main_pipeline as main
    import processing_signals.main.etf_exchange_flows.etf_exchange_flows_vertical as vertical

    input_contract = {"family": FAMILY, "stage": "input"}
    processing_contract = {"family": FAMILY, "stage": "processing"}
    classification_contract = {"family": FAMILY, "stage": "classification"}
    captured = {}
    monkeypatch.setattr(vertical, "run_input_pipeline", lambda **_: {FAMILY: input_contract})
    monkeypatch.setattr(vertical, "run_processing_pipeline", lambda **_: {FAMILY: processing_contract})
    monkeypatch.setattr(vertical, "run_classification_pipeline", lambda **_: {FAMILY: classification_contract})

    def builder(*, processing_contract, classification_contract, **kwargs):
        captured.update(processing=processing_contract, classification=classification_contract, kwargs=kwargs)
        return {"family": FAMILY, "stage": "screen_contract"}

    monkeypatch.setattr(vertical, "build_etf_exchange_flows_contract", builder)
    output = main.run_main_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: {
        "fetcher": object(), "now_timestamp": NOW, "contract_arguments": {"selected_range": "7d"},
    }})[FAMILY]
    assert captured == {"processing": processing_contract, "classification": classification_contract,
                        "kwargs": {"selected_range": "7d"}}
    assert output["screen"]["stage"] == "screen_contract"


def test_etf_pipeline_14_arguments_and_previous_state_are_immutable():
    arguments = {FAMILY: {"fetcher": Fetcher(), "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                            "data_mode": "synthetic", "is_demo": True}}}
    input_before = deepcopy(arguments[FAMILY]["input_arguments"])
    previous = {FAMILY: {}}
    run_main_pipeline(enabled_families=(FAMILY,), family_arguments=arguments, previous_state=previous)
    assert arguments[FAMILY]["input_arguments"] == input_before
    assert arguments[FAMILY]["now_timestamp"] == NOW
    assert previous == {FAMILY: {}}


def test_etf_pipeline_15_no_runtime_export_or_hmi_side_effect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_main_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: {
        "fetcher": Fetcher(), "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                            "data_mode": "synthetic", "is_demo": True},
    }})
    assert list(tmp_path.iterdir()) == []


def test_etf_pipeline_16_frozen_hashes_are_intact():
    root = Path(__file__).parents[1]
    expected = {
        "src/processing_signals/input/etf_exchange_flows/etf_exchange_flows_data_raw_extract.py": "AF591861B05961161A38B422B211B1FFDE3A3080B98A27338FE0D5E227682298",
        "src/processing_signals/input/etf_exchange_flows/etf_exchange_flows_data_raw_preprocessing.py": "053D57C3C8A867545C9E83C56CE3B3D309260B9474ED7D8328992DFA9869183C",
        "src/processing_signals/processing/etf_exchange_flows/etf_exchange_flows_feature_builder.py": "3D286E8F0B5666841E7A78DC012FCC3BFB0E356D7A24F5006AFF87D07B46160A",
        "src/processing_signals/processing/etf_exchange_flows/etf_exchange_flows_processor.py": "661303A31E654D7F618DD758D4A359EA0284A5B1A6C50BE040D7E1C782C8DDCA",
        "src/processing_signals/classification/etf_exchange_flows/etf_exchange_flows_classifier.py": "43DAE7A9D169E1993321FE77B50407FF20CE92D443A432CFE785380AA134E46A",
        "src/processing_signals/classification/etf_exchange_flows/etf_exchange_flows_contract_builder.py": "DDCC54381A031690DDE084479626D452C384AAC384B87BA70DBC09C9F7F6D03C",
    }
    actual = {path: canonical_text_sha256(root / path) for path in expected}
    assert actual == expected
