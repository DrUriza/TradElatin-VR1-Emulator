from __future__ import annotations

import json

from processing_signals.classification.classification_pipeline import (
    CLASSIFICATION_FAMILY_HANDLERS,
    FAMILY_ORDER,
    run_classification_pipeline,
)
from processing_signals.input.input_pipeline import run_input_pipeline
from processing_signals.processing.processing_pipeline import run_processing_pipeline
from processing_signals.runtime.emulator import SyntheticProviderRouter
from test_eight_family_input_processing_pipeline import FAMILIES, _arguments


def test_all_eight_families_run_raw_through_classification() -> None:
    assert FAMILY_ORDER == FAMILIES
    assert tuple(CLASSIFICATION_FAMILY_HANDLERS) == FAMILIES

    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    inputs = run_input_pipeline(enabled_families=FAMILIES, family_arguments=_arguments(router))
    processing = run_processing_pipeline(
        input_contracts=inputs,
        enabled_families=FAMILIES,
        now_timestamp=1786150805,
    )
    classification = run_classification_pipeline(
        processing_contracts=processing,
        enabled_families=FAMILIES,
    )

    for family in FAMILIES:
        assert classification[family]["family"] == family
        assert classification[family]["stage"] == "classification"
        allowed = {"ok", "available", "partial"} if family == "on_chain_miners" else {"ok", "available"}
        assert classification[family]["quality"]["status"] in allowed
        json.dumps(classification[family], ensure_ascii=False, allow_nan=False)

    prices = classification["prices_ohlcv"]
    assert tuple(prices["technical_bias"]) == ("spot", "futures")
    assert tuple(prices["indicator_signals"]) == ("spot", "futures")
    assert "spot" not in json.dumps(prices, ensure_ascii=False).lower()

    volatility = classification["volatility_market_regimes"]
    volatility_text = json.dumps(volatility, ensure_ascii=False).lower()
    assert "deribit" not in volatility_text
    assert "implied" not in volatility_text
    assert "spread_volatility" not in volatility_text
    assert set(volatility["classifications"]) == {"daily_regimes", "positioning"}

    liquidity = classification["liquidity_microstructure"]
    assert liquidity["quality"]["status"] == "ok"

    oi = classification["open_interest_and_funding"]
    assert oi["quality"]["status"] == "ok"
    assert "open_interest_change_state" in oi["availability"]["optional"]
    assert "oi_funding_quadrant" in oi["availability"]["optional"]

    cvd = classification["cvd_volume_orderflow"]
    assert cvd["quality"]["status"] == "ok"
    assert cvd["quality"]["core_status"] == "ok"
    assert cvd["quality"]["enrichment_status"] in {"ok", "partial"}
