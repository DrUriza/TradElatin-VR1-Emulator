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


def test_processing_keeps_provider_markets_and_builds_general_as_exact_spot() -> None:
    output = _processing()
    assert output["stage"] == "processing"
    assert tuple(output["markets"]) == ("spot", "futures", "general")
    assert output["quality"]["status"] == "ok"
    for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"):
        spot = output["markets"]["spot"]["timeframes"][timeframe]["records"]
        general = output["markets"]["general"]["timeframes"][timeframe]["records"]
        assert len(general) == len(spot)
        for source, canonical in zip(spot, general, strict=True):
            for field in ("timestamp", "open", "high", "low", "close", "volume", "volume_usd"):
                assert canonical.get(field) == source.get(field)


def test_spot_futures_basis_is_direct_and_not_recalculated_from_general() -> None:
    output = _processing()
    payload = output["features"]["spot_futures_comparison"]["by_timeframe"]["1h"]
    assert payload["series"]
    current = payload["current"]
    assert current["basis_usd"] == pytest.approx(current["futures_price"] - current["spot_price"])
    assert current["basis_percent"] == pytest.approx((current["futures_price"] / current["spot_price"] - 1.0) * 100.0)


def test_market_selector_exposes_only_general_to_hmi() -> None:
    selector = _processing()["features"]["market_selector"]
    assert selector["default_market"] == "general"
    assert selector["selected_market"] == "general"
    assert selector["available_markets"] == ["general"]
    assert selector["canonical_source_market"] == "spot"
