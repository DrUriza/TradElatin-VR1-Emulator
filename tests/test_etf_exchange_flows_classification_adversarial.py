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


def test_reordered_input_is_deterministic():
    processing = cloned_processing()
    reordered = {key: deepcopy(processing[key]) for key in reversed(processing)}
    assert classify_etf_exchange_flows(processing_contract=processing, generated_at=NOW) == \
        classify_etf_exchange_flows(processing_contract=reordered, generated_at=NOW)
