from __future__ import annotations

from copy import deepcopy
import json

from processing_signals.classification.prices_ohlcv.prices_ohlcv_classifier import (
    calculate_group_bias,
    calculate_overall_bias,
    calculate_timeframe_bias,
    classify_adx,
    classify_atr,
    classify_basis,
    classify_candlestick_patterns,
    classify_kurtosis,
    classify_market_agreement,
    classify_market_leadership,
    classify_max_drawdown,
    classify_mfi,
    classify_profit_factor,
    classify_rsi,
    classify_sharpe,
    classify_statistical_performance,
    classify_technical_crosses,
    classify_williams_r,
    classify_win_rate,
    evaluate_prices_classification_quality,
    run_prices_ohlcv_classification,
)

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
MARKETS = ("spot", "futures", "general")


def _indicator_package(sign: float = 1.0) -> dict:
    return {
        "rsi": {"current": {"rsi": 60.0}},
        "macd": {"current": {"macd": sign * 2, "signal": sign, "histogram": sign}},
        "stochastic": {"current": {"k": 60.0, "d": 50.0}},
        "adx": {"current": {"adx": 30.0, "di_plus": 25.0 if sign > 0 else 15.0, "di_minus": 15.0 if sign > 0 else 25.0}},
        "cci": {"current": {"cci": sign * 120}},
        "mfi": {"current": {"mfi": 74.0}},
        "williams_r": {"current": {"williams_r": -50.0}},
        "atr": {"current": {"atr": 2.0}},
        "tsi": {"current": {"tsi": sign * 10}, "parameters": {"slow_period": 25, "fast_period": 13}},
    }


def _bias_values(sign: float = 1.0) -> dict:
    return {"values": {
        "ema_9_minus_ema_21": sign, "ema_21_minus_ema_50": sign, "sma_20_minus_sma_50": sign,
        "macd_minus_signal": sign, "macd_histogram": sign, "rsi_centered": sign * 10,
        "stochastic_k_minus_d": sign, "adx": 30, "di_plus_minus_di_minus": sign,
        "cci": sign * 120, "mfi_centered": sign * 15, "williams_r_centered": sign * 15,
        "tsi": sign * 10, "close_minus_bollinger_middle": sign, "atr_percent_of_close": 1.0,
    }}


def _statistics() -> dict:
    return {
        "descriptive": {"mean_close": 100, "return_standard_deviation": 0.01, "skewness": 0.1,
                        "kurtosis": 2.15, "z_score": 0.5, "metadata": {"kurtosis_mode": "pearson"}},
        "risk": {"var_95_return": -0.03, "cvar_95_return": -0.04, "var_95_price": -3, "cvar_95_price": -4},
        "performance": {"max_consecutive_wins": 8, "max_consecutive_losses": 4, "omega_ratio": 1.42,
                        "sharpe_ratio": 1.85, "sortino_ratio": 2.42, "calmar_ratio": 1.67,
                        "max_drawdown": -0.0482, "profit_factor": 2.15, "recovery_factor": 3.74,
                        "win_rate": 0.583, "performance_basis": "market_returns",
                        "metadata": {"periods_per_year": 8760, "return_type": "simple"}},
    }


def make_processing_output() -> dict:
    indicators = {m: {tf: _indicator_package(-1 if m == "futures" else 1) for tf in TIMEFRAMES} for m in MARKETS}
    biases = {m: {"timeframes": {tf: _bias_values(-1 if m == "futures" else 1) for tf in TIMEFRAMES}} for m in MARKETS}
    statistics = {m: {tf: _statistics() for tf in TIMEFRAMES} for m in MARKETS}
    crosses = {m: {tf: [] for tf in TIMEFRAMES} for m in MARKETS}
    patterns = {m: {tf: [] for tf in TIMEFRAMES} for m in MARKETS}
    crosses["spot"]["1h"] = [{"timestamp": 1, "cross_id": "ema_9_above_ema_21", "direction": 1}]
    patterns["spot"]["1h"] = [{"timestamp": 1, "pattern_id": "hammer", "direction": 1, "confidence": 0.82}]
    crosses["general"]["1h"] = deepcopy(crosses["spot"]["1h"])
    patterns["general"]["1h"] = deepcopy(patterns["spot"]["1h"])
    records = [{"timestamp": 1, "open": 99, "high": 102, "low": 98, "close": 101, "volume_usd": 1}]
    main = {m: {"timeframes": {tf: {"records": deepcopy(records), "unavailable_records": []} for tf in TIMEFRAMES}} for m in MARKETS}
    return {
        "family": "prices_ohlcv", "stage": "processing", "mode": "bootstrap", "markets": {},
        "features": {
            "market_selector": {"default_market": "general", "selected_market": "general", "available_markets": ["general"], "timeframes": list(TIMEFRAMES)},
            "main_ohlcv": main, "indicators": indicators, "bias_components": biases,
            "statistical_performance": {"markets": statistics}, "technical_crosses": crosses,
            "candlestick_patterns": patterns,
            "spot_futures_comparison": {"by_timeframe": {tf: {"current": {"basis_usd": 1, "basis_percent": 0.1}, "series": []} for tf in TIMEFRAMES}},
        },
        "quality": {"status": "ok", "warnings": [], "errors": []},
    }


def test_indicator_and_statistics_thresholds():
    assert classify_rsi(50)["state"] == "neutral"
    assert classify_rsi(72)["state"] == "overbought"
    assert classify_mfi(74)["signal"] == "neutral"
    assert classify_williams_r(-50)["state"] == "neutral"
    assert classify_atr(100, 2.0)["state"] == "high"
    assert classify_adx(30, 25, 15)["direction"] == "bullish"
    assert classify_kurtosis(2.15, "pearson")["state"] == "platykurtic"
    assert classify_max_drawdown(-0.0482)["state"] == "low"
    assert classify_profit_factor(2.15)["state"] == "strong"
    assert classify_win_rate(0.583)["state"] == "good"


def test_bias_groups_keep_micro_as_confidence_only():
    positive = calculate_timeframe_bias(_bias_values(1), "5m")
    negative = calculate_timeframe_bias(_bias_values(-1), "5m")
    tf = {"5m": positive, "15m": positive, "1h": positive, "4h": positive, "1d": positive}
    groups = {"short": calculate_group_bias(tf, {"5m": 0.4, "15m": 0.6}),
              "mid": calculate_group_bias(tf, {"1h": 0.4, "4h": 0.6}),
              "long": calculate_group_bias(tf, {"1d": 1})}
    aligned = calculate_overall_bias(groups, positive)
    contradicted = calculate_overall_bias(groups, negative)
    assert aligned["score"] == contradicted["score"]
    assert aligned["confidence"] > contradicted["confidence"]


def test_market_relationship_is_direct_spot_futures_only():
    assert classify_basis({"basis_percent": 0.1})["state"] == "premium"
    assert classify_basis({"basis_percent": -0.1})["state"] == "discount"
    biases = {"spot": {"overall": {"label": "bullish", "score": -0.5}},
              "futures": {"overall": {"label": "bearish", "score": 0.5}}}
    agreement = classify_market_agreement(biases)
    assert agreement["state"] == "divergent"
    assert agreement["basis"] == "spot_futures_bias_direction"
    assert set(agreement["directions"]) == {"spot", "futures"}
    assert classify_market_leadership(biases)["state"] == "derivatives_led"


def test_events_are_semantic_for_spot_and_futures():
    crosses = {m: {tf: [] for tf in TIMEFRAMES} for m in MARKETS}
    crosses["spot"]["1h"] = [{"timestamp": 1, "cross_id": "ema_9_above_ema_21", "direction": 1,
                                 "first_series": "ema_9", "second_series": "ema_21", "previous_difference": -1.0, "current_difference": 2.0}]
    event = classify_technical_crosses(crosses)["spot"]["1h"][0]
    assert event["marker"] == "arrow_up"
    assert event["source"] == {"market": "spot", "timeframe": "1h"}
    patterns = {m: {tf: [] for tf in TIMEFRAMES} for m in MARKETS}
    patterns["futures"]["1h"] = [{"timestamp": 1, "pattern_id": "hammer", "direction": 1, "confidence": 0.8,
                                     "components": {"body_ratio": 0.4, "invalid": float("nan")}}]
    event = classify_candlestick_patterns(patterns)["futures"]["1h"][0]
    assert event["calculation"]["components"]["invalid"] is None


def test_statistical_unavailable_values_preserve_units():
    package = _statistics()
    package["descriptive"].update({"mean_close": None, "close_standard_deviation": None, "return_standard_deviation": None})
    result = classify_statistical_performance(package, "1h")
    assert result["mean"]["state"] == "unavailable"
    assert result["standard_deviation"]["state"] == "unavailable"
    assert classify_sharpe(1.0)["confidence"] == 0.50


def test_public_classifier_includes_general_and_is_complete():
    output = run_prices_ohlcv_classification(make_processing_output())
    assert output["quality"]["status"] == "ok"
    assert set(output["indicator_signals"]) == set(MARKETS)
    assert set(output["statistical_signals"]) == set(MARKETS)
    assert set(output["technical_bias"]) == set(MARKETS)
    assert "general" in output["indicator_signals"]
    assert output["statistical_signals"]["spot"]["1h"]["metadata"]["performance_basis"] == "market_returns"
    assert output["indicator_signals"]["spot"]["1h"]["tsi"]["parameters"] == {"slow_period": 25, "fast_period": 13}


def test_quality_detects_missing_spot_structure():
    processing = make_processing_output()
    output = run_prices_ohlcv_classification(processing)
    indicators = deepcopy(output["indicator_signals"])
    del indicators["spot"]["1h"]["rsi"]
    quality = evaluate_prices_classification_quality(
        processing_features=processing["features"], indicator_signals=indicators,
        statistical_signals=output["statistical_signals"], technical_bias=output["technical_bias"],
        market_relationship=output["market_relationship"], events={"technical_crosses": {}, "candlestick_patterns": {}},
    )
    assert quality["status"] == "partial"
    assert "indicator_signals.spot.1h.rsi" in quality["missing_fields"]
