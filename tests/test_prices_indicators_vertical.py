from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

pytestmark = pytest.mark.skip(reason="legacy Prices general market retired; current contract contains spot and futures")

from processing_signals.processing.math.technical_cross_signals              import detect_numeric_crosses
from processing_signals.processing.prices_ohlcv.prices_ohlcv_feature_builder import build_prices_features
from processing_signals.processing.prices_ohlcv.prices_ohlcv_processor       import (
    PRICE_INDICATOR_CONFIG,
    TIMEFRAME_ORDER,
    calculate_all_prices_indicators,
    calculate_prices_crosses,
    calculate_prices_indicator_package,
)


def _records(count: int = 120, step: int = 60) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100 + index * 0.05 + math.sin(index / 3) * 2
        rows.append({"timestamp": index * step, "open": close - .2, "high": close + 1,
                     "low": close - 1, "close": close, "volume_usd": 10_000 + index * 3})
    return rows


def _markets() -> dict:
    return {
        market: {"timeframes": {tf: {"records": _records(step=seconds)} for tf, seconds in
                                  zip(TIMEFRAME_ORDER, (60, 300, 900, 3600, 14400, 86400), strict=True)}}
        for market in ("general", "spot", "futures")
    }


def test_all_markets_timeframes_and_sources_receive_complete_package() -> None:
    result   = calculate_all_prices_indicators(markets=_markets())
    required = {"moving_averages", "bollinger_bands", "fibonacci_levels", "rsi", "macd",
                "stochastic", "adx", "cci", "mfi", "williams_r", "atr", "tsi"}
    assert set(result) == {"general", "spot", "futures"}
    for market, timeframes in result.items():
        assert tuple(timeframes) == TIMEFRAME_ORDER
        for package in timeframes.values():
            assert set(package) == required
            assert package["rsi"]["source"]["is_synthetic_source"] is (market == "general")
    assert result["general"]["1m"]["rsi"]["source"]["construction"] == "spot_futures_arithmetic_mean"


def test_alignment_warmup_current_and_nonfinite_safety() -> None:
    package  = calculate_prices_indicator_package(records=_records(), market_type="general", timeframe="1m")
    expected = 120
    for name, keys in {"rsi": ("rsi",), "macd": ("macd", "signal", "histogram"),
                       "stochastic": ("k", "d"), "adx": ("adx", "di_plus", "di_minus"),
                       "bollinger_bands": ("upper", "middle", "lower")}.items():
        indicator = package[name]
        assert len(indicator["timestamps"]) == expected
        for key in keys:
            assert len(indicator["series"][key]) == expected
            assert indicator["current"][key] == next(
                (value for value in reversed(indicator["series"][key]) if value is not None), None)
    assert package["rsi"]["series"]["rsi"][0] is None
    json.dumps(package, allow_nan=False)


def test_moving_average_configuration_and_fibonacci_levels() -> None:
    package = calculate_prices_indicator_package(records=_records(), market_type="spot", timeframe="1h")
    assert set(package["moving_averages"]["series"]) == {
        "ema_9", "ema_21", "ema_50", "sma_20", "sma_50", "wma_20", "wma_50"
    }
    assert set(package["fibonacci_levels"]["current"]["levels"]) == {
        "0.0", "0.236", "0.382", "0.5", "0.618", "0.786", "1.0"
    }
    assert package["tsi"]["parameters"] == PRICE_INDICATOR_CONFIG["tsi"]


def test_insufficient_data_is_structured_and_does_not_abort() -> None:
    package = calculate_prices_indicator_package(records=_records(5), market_type="futures", timeframe="1m")
    assert all(item["quality"]["status"] == "insufficient_data" for item in package.values())
    assert all(value is None for value in package["macd"]["current"].values())


def test_mfi_uses_estimated_base_volume_without_mutating_ohlcv() -> None:
    records  = _records()
    original = deepcopy(records)
    result   = calculate_prices_indicator_package(records=records, market_type="spot", timeframe="15m")["mfi"]
    assert records == original
    assert result["metadata"] == {
        "volume_mode": "estimated_base_from_quote_volume", "source_field": "volume_usd",
        "estimation": "volume_usd_divided_by_typical_price",
    }
    assert result["current"]["mfi"] is not None


def test_numeric_crosses_and_prices_cross_output_have_no_classification_labels() -> None:
    events = detect_numeric_crosses(timestamps=[1, 2, 3], first_values=[0, -1, 2],
                                    second_values=[1, 0, 0], first_series="ema_9", second_series="ema_21")
    assert events[-1]["cross_id"] == "ema_9_above_ema_21"
    indicators = calculate_all_prices_indicators(markets=_markets())
    crosses    = calculate_prices_crosses(indicators)
    serialized = json.dumps(crosses).lower()
    assert "bullish" not in serialized and "bearish" not in serialized and "label" not in serialized
    assert any("macd" in event["cross_id"] for event in crosses["general"]["1m"])


def test_feature_builder_only_copies_indicator_and_cross_results() -> None:
    markets    = _markets()
    indicators = calculate_all_prices_indicators(markets=markets)
    crosses    = calculate_prices_crosses(indicators)
    original   = deepcopy(indicators)
    features   = build_prices_features(markets=markets, comparison={}, indicators=indicators,
                                     technical_crosses=crosses)
    assert indicators == original
    assert features["indicators"] == indicators
    assert features["indicators"] is not indicators
    assert features["technical_crosses"] == crosses
