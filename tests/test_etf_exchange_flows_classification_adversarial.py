"""Adversarial boundaries for ETF exchange-flow Classification."""
from copy import deepcopy
import math
from pathlib import Path

import pytest

from canonical_hash_helpers import canonical_text_sha256
from etf_exchange_flows_classification_helpers import NOW, cloned_processing
from processing_signals.classification.etf_exchange_flows import classify_etf_exchange_flows


@pytest.mark.parametrize("parameters", [
    {"pressure_neutral_threshold": True}, {"pressure_neutral_threshold": math.nan},
    {"pressure_strong_threshold": math.inf}, {"pressure_neutral_threshold": 0.5, "pressure_strong_threshold": 0.4},
    {"gbtc_discount_threshold_percent": 1}, {"aum_aligned_max_percent": 6, "aum_watch_max_percent": 5},
    {"netflow_deadband_btc": -1}, {"unknown": 1},
])
def test_invalid_parameter_overrides_are_rejected(parameters):
    with pytest.raises(ValueError, match="invalid_classification_input"):
        classify_etf_exchange_flows(processing_contract=cloned_processing(), generated_at=NOW, parameters=parameters)


@pytest.mark.parametrize(("path", "value"), [
    (("features", "pressure", "flow_24h", "value"), True),
    (("features", "pressure", "flow_24h", "value"), math.nan),
    (("features", "etf", "period_flow_usd", "1d", "value"), math.inf),
])
def test_bool_nan_infinity_are_rejected(path, value):
    processing = cloned_processing()
    target = processing
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    if isinstance(value, float) and not math.isfinite(value):
        with pytest.raises(ValueError, match="invalid_classification_input"):
            classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
    else:
        result = classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)
        assert result["classifications"]["exchange_pressure_regime"]["status"] == "invalid"


@pytest.mark.parametrize(("field", "value"), [("family", "other"), ("stage", "input"), ("version", "9")])
def test_incompatible_roots_are_rejected(field, value):
    processing = cloned_processing()
    processing[field] = value
    with pytest.raises(ValueError, match=f"invalid_classification_input:{field}"):
        classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW)


def test_no_visual_contract_pipeline_or_frozen_hash_changes():
    root = Path(__file__).parents[1]
    # Classification rules remain presentation-agnostic.  The contract builder is
    # intentionally allowed to emit HMI presentation fields in SP 1.3.
    classifier = root / "src/processing_signals/classification/etf_exchange_flows/etf_exchange_flows_classifier.py"
    source = classifier.read_text(encoding="utf-8")
    assert all(token not in source for token in ("display_value", "color", "widget", "requests", "httpx", "getenv", "environ"))
    expected = {
        "src/processing_signals/input/etf_exchange_flows/etf_exchange_flows_data_raw_extract.py": "AF591861B05961161A38B422B211B1FFDE3A3080B98A27338FE0D5E227682298",
        "src/processing_signals/input/etf_exchange_flows/etf_exchange_flows_data_raw_preprocessing.py": "053D57C3C8A867545C9E83C56CE3B3D309260B9474ED7D8328992DFA9869183C",
        "src/processing_signals/processing/etf_exchange_flows/etf_exchange_flows_feature_builder.py": "3D286E8F0B5666841E7A78DC012FCC3BFB0E356D7A24F5006AFF87D07B46160A",
        "src/processing_signals/processing/etf_exchange_flows/etf_exchange_flows_processor.py": "661303A31E654D7F618DD758D4A359EA0284A5B1A6C50BE040D7E1C782C8DDCA",
    }
    actual = {path: canonical_text_sha256(root / path) for path in expected}
    assert actual == expected


def test_reordered_input_is_deterministic():
    processing = cloned_processing()
    reordered = {key: deepcopy(processing[key]) for key in reversed(processing)}
    assert classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW) == \
        classify_etf_exchange_flows(processing_contract=reordered, generated_at=NOW)
