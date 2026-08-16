from __future__ import annotations

import json
import math
from statistics import stdev

import pytest

from processing_signals.processing.math.statistics.descriptive_statistics import (
    calculate_kurtosis, calculate_mean, calculate_skewness, calculate_standard_deviation, calculate_z_score,
)
from processing_signals.processing.math.statistics.risk_metrics       import calculate_historical_cvar, calculate_historical_var
from processing_signals.processing.math.statistics.return_performance import (
    calculate_calmar_ratio, calculate_equity_curve, calculate_max_consecutive_losses,
    calculate_max_consecutive_wins, calculate_max_drawdown, calculate_omega_ratio,
    calculate_profit_factor, calculate_recovery_factor, calculate_sharpe_ratio,
    calculate_simple_returns, calculate_sortino_ratio, calculate_win_rate,
)
from processing_signals.processing.prices_ohlcv.prices_ohlcv_processor import (
    PRICE_PERIODS_PER_YEAR, TIMEFRAME_ORDER, build_indicator_bias_components,
    calculate_all_prices_statistics, calculate_prices_indicator_package,
)


def _records(count=120, step=60):
    return [{"timestamp": index * step, "open": 100 + index + (-1) ** index,
             "high": 102 + index, "low": 98 + index, "close": 100 + index + (-1) ** index,
             "volume_usd": 10_000.0} for index in range(count)]


def _markets():
    seconds = (60, 300, 900, 3600, 14400, 86400)
    return {market: {"timeframes": {tf: {"records": _records(step=step)} for tf, step in zip(TIMEFRAME_ORDER, seconds, strict=True)}} for market in ("general", "spot", "futures")}


def test_returns_descriptive_and_zscore_are_mathematically_consistent():
    returns = calculate_simple_returns([100, 102, 101])
    assert returns == pytest.approx([None, .02, 101 / 102 - 1], nan_ok=True)
    assert calculate_mean([1, 2, 3]) == 2
    assert calculate_standard_deviation([1, 2, 3], 1) == pytest.approx(stdev([1, 2, 3]))
    assert math.isfinite(calculate_skewness([-.2, -.1, 0, .2, .4]))
    assert calculate_kurtosis([-.2, -.1, 0, .2, .4], "pearson") == pytest.approx(calculate_kurtosis([-.2, -.1, 0, .2, .4], "excess") + 3)
    expected = (5 - calculate_mean([3, 4, 5])) / calculate_standard_deviation([3, 4, 5], 1)
    assert calculate_z_score([1, 2, 3, 4, 5], lookback=3) == pytest.approx(expected)


def test_historical_var_and_cvar_use_lower_return_tail():
    returns = [-.10, -.05, 0, .02, .04]
    var     = calculate_historical_var(returns, .8)
    assert var == pytest.approx(-.06)
    assert calculate_historical_cvar(returns, .8) == pytest.approx(-.10)


def test_market_return_performance_formulas_and_zero_division_guards():
    returns = [None, .1, .2, -.1, -.2, .1, .1]
    equity  = calculate_equity_curve(returns)
    assert equity[0] == 1 and equity[1] == pytest.approx(1.1)
    assert calculate_max_drawdown(equity) < 0
    assert calculate_max_consecutive_wins(returns) == 2
    assert calculate_max_consecutive_losses(returns) == 2
    assert calculate_win_rate(returns) == pytest.approx(4 / 6)
    assert calculate_profit_factor(returns) == pytest.approx(.5 / .3)
    assert calculate_omega_ratio([.1, .2]) is None
    assert calculate_sortino_ratio([.1, .2], periods_per_year=365) is None
    assert calculate_calmar_ratio([.1], 0, 365) is None
    assert calculate_recovery_factor([.1], 0) is None


def test_sharpe_uses_timeframe_annualization():
    returns = [.01, -.005, .02, -.01]
    daily   = calculate_sharpe_ratio(returns, periods_per_year=PRICE_PERIODS_PER_YEAR["1d"])
    hourly  = calculate_sharpe_ratio(returns, periods_per_year=PRICE_PERIODS_PER_YEAR["1h"])
    assert hourly / daily == pytest.approx(math.sqrt(8760 / 365))


def test_all_markets_and_timeframes_have_serializable_statistical_packages():
    result = calculate_all_prices_statistics(markets=_markets())
    assert set(result) == {"spot", "futures", "general"}
    for market in result.values():
        assert tuple(market) == TIMEFRAME_ORDER
        for package in market.values():
            assert package["performance"]["performance_basis"] == "market_returns"
            assert package["descriptive"]["metadata"]["kurtosis_mode"] == "pearson"
            assert package["risk"]["metadata"]["method"] == "historical"
    json.dumps(result, allow_nan=False)


def test_bias_components_are_numeric_neutral_and_tsi_reports_real_parameters():
    records    = _records()
    package    = calculate_prices_indicator_package(records=records, market_type="general", timeframe="1h")
    components = build_indicator_bias_components(indicator_package=package, close=records[-1]["close"])
    assert package["tsi"]["parameters"] == {"slow_period": 25, "fast_period": 13}
    assert all(value is None or isinstance(value, float) for value in components["values"].values())
    serialized = json.dumps(components).lower()
    assert "bullish" not in serialized and "bearish" not in serialized
