from __future__ import annotations

import json

import pytest

from processing_signals.input.prices_ohlcv.prices_ohlcv_data_raw_preprocessing import run_prices_ohlcv_input
from processing_signals.processing.prices_ohlcv.prices_ohlcv_processor import run_prices_ohlcv_processing
from processing_signals.runtime.emulator import SyntheticProviderRouter


def _processing():
    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    source = run_prices_ohlcv_input(fetcher=router.for_family("prices_ohlcv"), requested_mode="bootstrap", bootstrap_limit=500)
    return run_prices_ohlcv_processing(source, now_timestamp=1786150805)


def test_processing_keeps_provider_markets_without_aliases() -> None:
    output = _processing()
    assert output["stage"] == "processing"
    assert tuple(output["markets"]) == ("spot", "futures")
    assert output["quality"]["status"] == "ok"
    for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert output["markets"]["spot"]["timeframes"][timeframe]["records"]
        assert output["markets"]["futures"]["timeframes"][timeframe]["records"]


def test_spot_futures_basis_is_direct_and_not_recalculated_from_spot() -> None:
    output = _processing()
    payload = output["features"]["spot_futures_comparison"]["by_timeframe"]["1h"]
    assert payload["series"]
    current = payload["current"]
    assert current["basis_usd"] == pytest.approx(current["futures_price"] - current["spot_price"])
    assert current["basis_percent"] == pytest.approx((current["futures_price"] / current["spot_price"] - 1.0) * 100.0)


def test_market_selector_exposes_only_spot_to_hmi() -> None:
    selector = _processing()["features"]["market_selector"]
    assert selector["default_market"] == "spot"
    assert selector["selected_market"] == "spot"
    assert selector["available_markets"] == ["spot"]
    assert selector["canonical_source_market"] == "spot"
