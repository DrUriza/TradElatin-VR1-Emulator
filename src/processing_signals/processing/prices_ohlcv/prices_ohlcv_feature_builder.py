from __future__ import annotations

from copy   import deepcopy
from typing import Any, Mapping


TIMEFRAME_ORDER = ("1m", "5m", "15m", "1h", "4h", "1d")
MARKET_ORDER    = ("spot", "futures", "general")


def build_market_series_features(market: Mapping[str, Any]) -> dict[str, Any]:
    """Copy already-computed numeric market series without recalculation."""
    timeframes = market.get("timeframes", {})
    return {
        "timeframes": {
            timeframe: {
                "records": deepcopy(timeframes.get(timeframe, {}).get("records", [])),
                "unavailable_records": deepcopy(
                    timeframes.get(timeframe, {}).get("unavailable_records", [])
                ),
            }
            for timeframe in TIMEFRAME_ORDER
        }
    }


def build_main_ohlcv_features(markets: Mapping[str, Any]) -> dict[str, Any]:
    return {
        market: build_market_series_features(markets.get(market, {}))
        for market in MARKET_ORDER
    }


def build_market_selector_features(markets: Mapping[str, Any]) -> dict[str, Any]:
    # Prices-only presentation contract: HMI exposes one canonical market,
    # ``general``, whose data is an exact alias of Spot.  Spot/Futures remain
    # in Processing for internal comparison and confirmation.
    available = ["general"] if any(
        markets.get("general", {}).get("timeframes", {}).get(timeframe, {}).get("records")
        for timeframe in TIMEFRAME_ORDER
    ) else []
    return {
        "default_market": "general",
        "selected_market": "general",
        "available_markets": available,
        "timeframes": list(TIMEFRAME_ORDER),
        "default_timeframe": "1h",
        "selected_timeframe": "1h",
        "canonical_source_market": "spot",
    }


def build_spot_futures_comparison_features(
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    source = comparison.get("by_timeframe", comparison)
    return {
        "by_timeframe": {
            timeframe: {
                "series": deepcopy(source.get(timeframe, {}).get("series", [])),
                "current": deepcopy(source.get(timeframe, {}).get("current", {})),
            }
            for timeframe in TIMEFRAME_ORDER
        }
    }


def build_indicator_placeholders() -> dict[str, dict[str, Any]]:
    return {market: {} for market in MARKET_ORDER}


def build_timeframe_indicator_features(indicators: Mapping[str, Any]) -> dict[str, Any]:
    """Package previously calculated indicators without recalculating them."""
    return deepcopy(dict(indicators))


def build_market_indicator_features(indicators: Mapping[str, Any]) -> dict[str, Any]:
    return {
        timeframe: build_timeframe_indicator_features(indicators.get(timeframe, {}))
        for timeframe in TIMEFRAME_ORDER
    }


def build_indicator_features(indicators: Mapping[str, Any]) -> dict[str, Any]:
    return {
        market: build_market_indicator_features(indicators.get(market, {}))
        for market in MARKET_ORDER
    }


def build_technical_cross_features(crosses: Mapping[str, Any]) -> dict[str, Any]:
    return {
        market: {
            timeframe: deepcopy(crosses.get(market, {}).get(timeframe, []))
            for timeframe in TIMEFRAME_ORDER
        }
        for market in MARKET_ORDER
    }


def build_candlestick_pattern_features(patterns: Mapping[str, Any]) -> dict[str, Any]:
    return {market: {timeframe: deepcopy(patterns.get(market, {}).get(timeframe, [])) for timeframe in TIMEFRAME_ORDER} for market in MARKET_ORDER}


def build_statistical_features(results: Mapping[str, Any]) -> dict[str, Any]:
    return {market: {timeframe: deepcopy(results.get(market, {}).get(timeframe, {}).get("descriptive", {})) for timeframe in TIMEFRAME_ORDER} for market in MARKET_ORDER}


def build_risk_features(results: Mapping[str, Any]) -> dict[str, Any]:
    return {market: {timeframe: deepcopy(results.get(market, {}).get(timeframe, {}).get("risk", {})) for timeframe in TIMEFRAME_ORDER} for market in MARKET_ORDER}


def build_performance_features(results: Mapping[str, Any]) -> dict[str, Any]:
    return {market: {timeframe: deepcopy(results.get(market, {}).get(timeframe, {}).get("performance", {})) for timeframe in TIMEFRAME_ORDER} for market in MARKET_ORDER}


def build_statistical_performance_features(results: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "default_market": "general", "default_metrics_timeframe": "1h",
        "markets": {market: {timeframe: deepcopy(results.get(market, {}).get(timeframe, {})) for timeframe in TIMEFRAME_ORDER} for market in MARKET_ORDER},
        "statistics": build_statistical_features(results), "risk": build_risk_features(results),
        "performance": build_performance_features(results),
    }


def build_bias_component_features(components: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(components))


def build_prices_features(
    *,
    markets: Mapping[str, Any],
    comparison: Mapping[str, Any],
    indicators: Mapping[str, Any] | None = None,
    technical_crosses: Mapping[str, Any] | None = None,
    candlestick_patterns: Mapping[str, Any] | None = None,
    statistical_performance: Mapping[str, Any] | None = None,
    bias_components: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Organize numeric Processing results for the future classifier."""
    return {
        "market_selector": build_market_selector_features(markets),
        "main_ohlcv": build_main_ohlcv_features(markets),
        "spot_futures_comparison": build_spot_futures_comparison_features(comparison),
        "indicators": (
            build_indicator_features(indicators)
            if indicators is not None
            else build_indicator_placeholders()
        ),
        "technical_crosses": build_technical_cross_features(technical_crosses or {}),
        "candlestick_patterns": build_candlestick_pattern_features(candlestick_patterns or {}),
        "statistical_performance": build_statistical_performance_features(statistical_performance or {}),
        "bias_components": build_bias_component_features(bias_components or {}),
    }


class PricesOhlcvFeatureBuilder:
    """OO facade that only packages existing numeric results."""

    def build(
        self,
        *,
        markets: Mapping[str, Any],
        comparison: Mapping[str, Any],
        indicators: Mapping[str, Any] | None = None,
        technical_crosses: Mapping[str, Any] | None = None,
        candlestick_patterns: Mapping[str, Any] | None = None,
        statistical_performance: Mapping[str, Any] | None = None,
        bias_components: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_prices_features(
            markets=markets, comparison=comparison, indicators=indicators,
            technical_crosses=technical_crosses,
            candlestick_patterns=candlestick_patterns,
            statistical_performance=statistical_performance,
            bias_components=bias_components,
        )
