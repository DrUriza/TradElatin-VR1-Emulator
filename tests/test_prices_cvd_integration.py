from __future__ import annotations

import math

from processing_signals.classification.prices_ohlcv.prices_ohlcv_contract_builder import (
    MARKET_ORDER,
    TIMEFRAME_ORDER,
    _buy_sell_projection,
)


def _prices_context(*, high: float, low: float, close: float, volume_usd: float) -> dict:
    record = {
        "timestamp": 1_700_000_000,
        "open": (high + low) / 2.0,
        "high": high,
        "low": low,
        "close": close,
        "volume_usd": volume_usd,
    }
    return {
        "family": "prices_ohlcv",
        "stage": "processing",
        "metadata": {"is_demo": True},
        "markets": {
            "general": {
                "timeframes": {
                    timeframe: {"status": "available", "records": [record]}
                    for timeframe in TIMEFRAME_ORDER
                }
            }
        },
    }


def test_prices_buy_sell_projection_is_general_only_and_prices_owned() -> None:
    processing = _prices_context(high=110.0, low=90.0, close=102.0, volume_usd=100.0)
    widget = _buy_sell_projection(
        processing,
        {"selected_market": "general", "selected_timeframe": "1h"},
    )

    assert MARKET_ORDER == ("general",)
    assert widget["status"] == "available"
    assert widget["selected_market"] == "general"
    assert set(widget["by_market_timeframe"]) == {"general"}
    assert set(widget["by_market_timeframe"]["general"]) == set(TIMEFRAME_ORDER)
    assert widget["is_proxy"] is True
    assert widget["method"] == "synthetic_candle_position_proxy"
    assert widget["source_paths"] == ["charts.ohlcv.markets.general.timeframes.*.volume_by_side"]

    current = widget["current"]
    assert math.isclose(current["buy_share"], 0.6)
    assert math.isclose(current["sell_share"], 0.4)
    assert math.isclose(current["buy_volume_usd"], 60.0)
    assert math.isclose(current["sell_volume_usd"], 40.0)


def test_prices_buy_sell_zero_volume_is_finite_or_explicitly_undefined() -> None:
    processing = _prices_context(high=100.0, low=100.0, close=100.0, volume_usd=0.0)
    widget = _buy_sell_projection(
        processing,
        {"selected_market": "general", "selected_timeframe": "1d"},
    )
    current = widget["current"]

    assert current["buy_volume_usd"] == 0.0
    assert current["sell_volume_usd"] == 0.0
    assert current["buy_share"] == 0.5
    assert current["sell_share"] == 0.5
    assert all(math.isfinite(current[key]) for key in ("buy_volume_usd", "sell_volume_usd", "buy_share", "sell_share"))


def test_prices_projection_does_not_require_cvd_market_contract() -> None:
    processing = _prices_context(high=110.0, low=90.0, close=100.0, volume_usd=250.0)
    assert "spot" not in processing["markets"]
    assert "futures" not in processing["markets"]

    widget = _buy_sell_projection(
        processing,
        {"selected_market": "general", "selected_timeframe": "15m"},
    )
    assert widget["status"] == "available"
    assert widget["selected_market"] == "general"
