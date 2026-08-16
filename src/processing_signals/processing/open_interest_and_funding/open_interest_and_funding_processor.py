"""Mathematical Processing v0.1 for open interest and funding."""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Callable

import numpy as np
import pandas as pd

from processing_signals.processing.math.indicators.momentum.cci import cci
from processing_signals.processing.math.indicators.momentum.rsi import rsi
from processing_signals.processing.math.indicators.momentum.stochastic import stochastic
from processing_signals.processing.math.indicators.momentum.tsi import tsi
from processing_signals.processing.math.indicators.momentum.williams_r import williams_r
from processing_signals.processing.math.indicators.trend.adx import adx
from processing_signals.processing.math.indicators.trend.macd import macd
from processing_signals.processing.math.indicators.trend.moving_averages import ema, sma, wma
from processing_signals.processing.math.indicators.volatility.atr import atr
from processing_signals.processing.math.indicators.volatility.bollinger_bands import bollinger_bands
from processing_signals.processing.math.technical_cross_signals import detect_numeric_crosses
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_feature_builder import OpenInterestAndFundingFeatureBuilder

FAMILY            = "open_interest_and_funding"
TIMEFRAMES        = ("1m", "5m", "15m", "1h", "4h", "1d")
TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3_600, "4h": 14_400, "1d": 86_400}
CHANGE_24H_WARMUP = {timeframe: 86_400 // seconds + 1 for timeframe, seconds in TIMEFRAME_SECONDS.items()}
VALID_STATUSES    = {"available", "partial", "unavailable", "invalid"}
CONTEXT_FIELDS    = ("asset", "exchange_scope", "primary_provider", "confirmation_providers", "data_mode", "is_demo",
                     "reference_timestamp", "execution_timestamp", "generated_at")
SOURCE_IDS        = {"open_interest_ohlc": ("aggregated_open_interest_ohlc", "USD"),
                     "funding_rate_ohlc": ("oi_weighted_funding_rate_ohlc", "percent_points")}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return 0.0 if number == 0.0 else number


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("processing output contains a non-finite value")
        return 0.0 if number == 0.0 else number
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("processing output contains a non-string key")
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    raise ValueError(f"processing output contains unsupported type {type(value).__name__}")


def _input_contract(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("input_contract must be an Input contract or vertical bundle")
    candidate = value.get("input", value)
    if not isinstance(candidate, Mapping) or candidate.get("family") != FAMILY or candidate.get("stage") != "input":
        raise ValueError("Processing requires family=open_interest_and_funding and stage=input")
    context, series = candidate.get("context"), candidate.get("series")
    if candidate.get("mode") not in {"bootstrap", "incremental", "recovery"}:
        raise ValueError("Input mode is incompatible")
    if not isinstance(context, Mapping) or any(field not in context for field in CONTEXT_FIELDS):
        raise ValueError("Input context is structurally incomplete")
    if context.get("asset") != "BTC" or context.get("primary_provider") != "coinglass" or type(context.get("reference_timestamp")) is not int:
        raise ValueError("Input context is incompatible")
    if not isinstance(series, Mapping):
        raise ValueError("Input series must be a mapping")
    for metric_id, (endpoint_id, unit) in SOURCE_IDS.items():
        metric = series.get(metric_id)
        if not isinstance(metric, Mapping) or metric.get("provider") != "coinglass" or metric.get("endpoint_id") != endpoint_id or metric.get("unit") != unit:
            raise ValueError(f"Input {metric_id} metadata is incompatible")
        if metric_id == "funding_rate_ohlc" and (metric.get("aggregation") != "open_interest_weighted" or metric.get("representation") != "percentage_points"):
            raise ValueError("Input funding metadata is incompatible")
        timeframes = metric.get("timeframes")
        if not isinstance(timeframes, Mapping) or any(timeframe not in timeframes for timeframe in TIMEFRAMES):
            raise ValueError(f"Input {metric_id} must contain all six timeframes")
    return candidate


def _validate_frame(metric_id: str, timeframe: str, frame: Any, reference_timestamp: int) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(frame, Mapping) or frame.get("status") not in VALID_STATUSES or not isinstance(frame.get("records"), list):
        return [], "invalid_timeframe_structure"
    if frame.get("expected_interval_seconds") != TIMEFRAME_SECONDS[timeframe] or frame.get("unit") != SOURCE_IDS[metric_id][1]:
        return [], "invalid_timeframe_metadata"
    records, previous = [], None
    for row in frame["records"]:
        if not isinstance(row, Mapping) or type(row.get("timestamp")) is not int or row["timestamp"] > reference_timestamp:
            return [], "invalid_record_timestamp"
        if previous is not None and row["timestamp"] <= previous:
            return [], "timestamps_not_strictly_increasing"
        values = {field: _finite(row.get(field)) for field in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            return [], "invalid_ohlc_numeric"
        if metric_id == "open_interest_ohlc" and any(value < 0 for value in values.values()):
            return [], "negative_open_interest"
        if values["high"] < max(values.values()) or values["low"] > min(values.values()):
            return [], "inconsistent_ohlc"
        records.append({"timestamp": row["timestamp"], **values})
        previous = row["timestamp"]
    return records, None


def _segments(records: Sequence[Mapping[str, Any]], interval: int) -> tuple[list[tuple[int, int]], list[dict[str, int]]]:
    if not records:
        return [], []
    bounds, gaps, start = [], [], 0
    for index in range(1, len(records)):
        difference = records[index]["timestamp"] - records[index - 1]["timestamp"]
        if difference != interval:
            bounds.append((start, index))
            missing = max(0, math.ceil(difference / interval) - 1)
            gaps.append({"previous_timestamp": records[index - 1]["timestamp"], "next_timestamp": records[index]["timestamp"],
                "expected_interval_seconds": interval, "missing_records": missing,
                "start_timestamp": records[index - 1]["timestamp"] + interval, "end_timestamp": records[index]["timestamp"] - interval})
            start = index
    bounds.append((start, len(records)))
    return bounds, gaps


def _segment_metadata(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    return [{"segment_start_index": start, "segment_end_index": end - 1, "segment_first_timestamp": records[start]["timestamp"],
        "segment_last_timestamp": records[end - 1]["timestamp"], "record_count": end - start} for start, end in bounds]


def _source(metric_id: str, timeframe: str) -> dict[str, Any]:
    endpoint_id, unit = SOURCE_IDS[metric_id]
    return {"metric_id": metric_id, "provider": "coinglass", "endpoint_id": endpoint_id, "timeframe": timeframe, "unit": unit}


def _wrapper(*, timestamps: Sequence[int], series: Mapping[str, Sequence[Any]], units: Mapping[str, str], source: Mapping[str, Any], parameters: Mapping[str, Any],
             warmup: int | None, calculation: str | None, source_status: str, bounds: Sequence[tuple[int, int]], gaps: Sequence[Mapping[str, Any]],
             insufficient_reason: str = "insufficient_history", forced_status: str | None = None, forced_reason: str | None = None) -> dict[str, Any]:
    normalized = {name: [_finite(value) for value in values] for name, values in series.items()}
    normalized_units = copy.deepcopy(dict(units))
    if any(len(values) != len(timestamps) for values in normalized.values()):
        raise ValueError("calculation arrays have incompatible lengths")
    last_segment = range(*bounds[-1]) if bounds else range(0)
    complete_indices = [index for index in last_segment if normalized and all(values[index] is not None for values in normalized.values())]
    current_index = complete_indices[-1] if complete_indices else None
    current = {name: values[current_index] for name, values in normalized.items()} if current_index is not None else None
    if forced_status:
        status, reason = forced_status, forced_reason
    elif source_status == "invalid":
        status, reason = "invalid", "source_invalid"
    elif source_status == "unavailable" or not timestamps:
        status, reason = "unavailable", "source_unavailable"
    elif current is None:
        status, reason = "partial", insufficient_reason
    elif source_status == "partial" or gaps:
        status, reason = "partial", "source_partial_or_gapped"
    else:
        status, reason = "available", None
    if set(normalized) != set(normalized_units) or any(not isinstance(unit, str) for unit in normalized_units.values()):
        status, reason = "invalid", "series_units_mismatch"
    finite_records = sum(all(values[index] is not None for values in normalized.values()) for index in range(len(timestamps))) if normalized else 0
    return {"status": status, "reason": reason, "timestamps": list(timestamps), "series": normalized, "units": normalized_units, "current": current,
        "current_timestamp": timestamps[current_index] if current_index is not None else None, "parameters": copy.deepcopy(dict(parameters)),
        "warmup_records": warmup, "calculation": calculation, "source": copy.deepcopy(dict(source)),
        "quality": {"records_available": len(timestamps), "finite_records": finite_records, "null_records": len(timestamps) - finite_records,
            "segments": len(bounds), "gaps_present": bool(gaps), "warnings": []}}


def _segment_calculation(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]], names: Sequence[str],
                         calculator: Callable[[pd.DataFrame], Mapping[str, Sequence[Any]]], warmup: int) -> dict[str, list[float | None]]:
    output = {name: [None] * len(records) for name in names}
    for start, end in bounds:
        frame = pd.DataFrame(records[start:end])
        calculated = calculator(frame)
        for name in names:
            values = list(calculated[name])
            if len(values) != end - start:
                raise ValueError("Math returned an incompatible series length")
            for local_index, value in enumerate(values):
                output[name][start + local_index] = None if local_index < warmup - 1 else _finite(value)
    return output


def _oi_delta(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]]) -> dict[str, list[float | None]]:
    absolute, percent = [None] * len(records), [None] * len(records)
    for start, end in bounds:
        for index in range(start + 1, end):
            previous, current = records[index - 1]["close"], records[index]["close"]
            absolute[index] = current - previous
            percent[index] = 100 * (current / previous - 1) if previous > 0 else None
    return {"delta_absolute_usd": absolute, "delta_percent": percent}


def _oi_change_24h(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]]) -> dict[str, list[float | None]]:
    absolute, percent = [None] * len(records), [None] * len(records)
    for start, end in bounds:
        positions = {records[index]["timestamp"]: index for index in range(start, end)}
        for index in range(start, end):
            reference_index = positions.get(records[index]["timestamp"] - 86_400)
            if reference_index is None:
                continue
            previous, current = records[reference_index]["close"], records[index]["close"]
            absolute[index] = current - previous
            percent[index] = 100 * (current / previous - 1) if previous > 0 else None
    return {"change_absolute_usd": absolute, "change_percent": percent}


def _oi_roc(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]], period: int = 12) -> dict[str, list[float | None]]:
    values = [None] * len(records)
    for start, end in bounds:
        for index in range(start + period, end):
            previous = records[index - period]["close"]
            values[index] = 100 * (records[index]["close"] / previous - 1) if previous > 0 else None
    return {"roc": values}


def _regression_channel_series(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]], window: int = 100, deviation_multiplier: float = 2.0) -> dict[str, list[float | None]]:
    middle = [None] * len(records)
    upper = [None] * len(records)
    lower = [None] * len(records)
    n = int(window)
    sx = n * (n - 1) / 2.0
    sx2 = (n - 1) * n * (2 * n - 1) / 6.0
    denominator = n * sx2 - sx * sx
    if n <= 1 or denominator == 0:
        return {"middle": middle, "upper": upper, "lower": lower}
    for start, end in bounds:
        closes = [float(records[index]["close"]) for index in range(start, end)]
        if len(closes) < n:
            continue
        for local_end in range(n - 1, len(closes)):
            ys = closes[local_end - n + 1:local_end + 1]
            sy = sum(ys)
            sxy = sum(index * value for index, value in enumerate(ys))
            slope = (n * sxy - sx * sy) / denominator
            intercept = (sy - slope * sx) / n
            fitted = [intercept + slope * index for index in range(n)]
            residual_std = math.sqrt(sum((value - fit) ** 2 for value, fit in zip(ys, fitted, strict=True)) / n)
            center = fitted[-1]
            width = float(deviation_multiplier) * residual_std
            target = start + local_end
            middle[target] = center
            upper[target] = center + width
            lower[target] = center - width
    return {"middle": middle, "upper": upper, "lower": lower}


def _indicator_packages(records: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[int, int]], gaps: Sequence[Mapping[str, Any]],
                        timeframe: str, source_status: str) -> dict[str, Any]:
    timestamps, source = [row["timestamp"] for row in records], _source("open_interest_ohlc", timeframe)
    ma_names = ("ema_9", "ema_21", "ema_50", "sma_20", "sma_50", "sma_100", "sma_200", "wma_20", "wma_50")
    def ma_calc(frame: pd.DataFrame) -> dict[str, pd.Series]:
        close = frame["close"]
        return {
            "ema_9": ema(close, 9), "ema_21": ema(close, 21), "ema_50": ema(close, 50),
            "sma_20": sma(close, 20), "sma_50": sma(close, 50), "sma_100": sma(close, 100), "sma_200": sma(close, 200),
            "wma_20": wma(close, 20), "wma_50": wma(close, 50),
        }
    ma = _segment_calculation(records, bounds, ma_names, ma_calc, 1)
    warmups = {"ema_9": 9, "ema_21": 21, "ema_50": 50, "sma_20": 20, "sma_50": 50, "sma_100": 100, "sma_200": 200, "wma_20": 20, "wma_50": 50}
    for start, end in bounds:
        for name, period in warmups.items():
            for index in range(start, min(end, start + period - 1)):
                ma[name][index] = None
    moving = _wrapper(timestamps=timestamps, series=ma, units={name: "USD" for name in ma_names}, source=source,
        parameters={"ema_periods": [9, 21, 50], "sma_periods": [20, 50, 100, 200], "wma_periods": [20, 50]}, warmup=200,
        calculation="ema_sma_wma_on_open_interest_close", source_status=source_status, bounds=bounds, gaps=gaps)

    regression_values = _regression_channel_series(records, bounds, window=100, deviation_multiplier=2.0)
    regression = _wrapper(timestamps=timestamps, series=regression_values,
        units={"middle": "USD", "upper": "USD", "lower": "USD"}, source=source,
        parameters={"window": 100, "deviation_multiplier": 2.0, "field": "close", "fit": "rolling_ordinary_least_squares", "width_basis": "population_standard_deviation_of_residuals"},
        warmup=100, calculation="rolling_ols_regression_channel_on_open_interest_close",
        source_status=source_status, bounds=bounds, gaps=gaps)

    bb_values = _segment_calculation(records, bounds, ("middle", "upper", "lower", "bandwidth", "percent_b"),
        lambda frame: _renamed_bollinger(bollinger_bands(frame["close"], 20, 2.0)), 20)
    bb = _wrapper(timestamps=timestamps, series=bb_values, units={"middle": "USD", "upper": "USD", "lower": "USD", "bandwidth": "ratio", "percent_b": "ratio"}, source=source, parameters={"period": 20, "standard_deviations": 2.0}, warmup=20,
        calculation="bollinger_bands_on_open_interest_close", source_status=source_status, bounds=bounds, gaps=gaps)
    macd_values = _segment_calculation(records, bounds, ("macd", "signal", "histogram"),
        lambda frame: _renamed_macd(macd(frame["close"], 12, 26, 9)), 34)
    macd_package = _wrapper(timestamps=timestamps, series=macd_values, units={"macd": "USD", "signal": "USD", "histogram": "USD"}, source=source, parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
        warmup=34, calculation="macd_on_open_interest_close", source_status=source_status, bounds=bounds, gaps=gaps)
    adx_values = _segment_calculation(records, bounds, ("adx", "di_plus", "di_minus"),
        lambda frame: _renamed_adx(adx(frame["high"], frame["low"], frame["close"], 14)), 28)
    adx_package = _wrapper(timestamps=timestamps, series=adx_values, units={"adx": "index_0_100", "di_plus": "index_0_100", "di_minus": "index_0_100"}, source=source, parameters={"period": 14}, warmup=28,
        calculation="adx_and_directional_indicators_on_open_interest_ohlc", source_status=source_status, bounds=bounds, gaps=gaps)
    stochastic_values = _segment_calculation(records, bounds, ("k", "d"),
        lambda frame: _renamed_stochastic(stochastic(frame["high"], frame["low"], frame["close"], 14, k_smoothing=3, d_period=3)), 18)
    stochastic_package = _wrapper(timestamps=timestamps, series=stochastic_values, units={"k": "index_0_100", "d": "index_0_100"}, source=source,
        parameters={"k_period": 14, "k_smoothing": 3, "d_period": 3}, warmup=18,
        calculation="stochastic_on_open_interest_ohlc", source_status=source_status, bounds=bounds, gaps=gaps)
    atr_values = _segment_calculation(records, bounds, ("atr",), lambda frame: {"atr": atr(frame["high"], frame["low"], frame["close"], 14)}, 14)
    atr_package = _wrapper(timestamps=timestamps, series=atr_values, units={"atr": "USD"}, source=source, parameters={"period": 14}, warmup=14,
        calculation="average_true_range_on_open_interest_ohlc", source_status=source_status, bounds=bounds, gaps=gaps)
    cci_values = _segment_calculation(records, bounds, ("cci",), lambda frame: {"cci": cci(frame["high"], frame["low"], frame["close"], 20)}, 20)
    cci_package = _wrapper(timestamps=timestamps, series=cci_values, units={"cci": "index"}, source=source, parameters={"period": 20}, warmup=20,
        calculation="commodity_channel_index_on_open_interest_ohlc", source_status=source_status, bounds=bounds, gaps=gaps)
    roc_package = _wrapper(timestamps=timestamps, series=_oi_roc(records, bounds), units={"roc": "percent"}, source=source, parameters={"period": 12}, warmup=13,
        calculation="100*(close[t]/close[t-12]-1)", source_status=source_status, bounds=bounds, gaps=gaps)
    rsi_package = _wrapper(timestamps=timestamps, series=_segment_calculation(records, bounds, ("rsi",),
        lambda frame: {"rsi": rsi(frame["close"], 14)}, 14), units={"rsi": "index_0_100"}, source=source,
        parameters={"period": 14}, warmup=14, calculation="wilder_rsi_on_open_interest_close",
        source_status=source_status, bounds=bounds, gaps=gaps)
    tsi_values = _segment_calculation(records, bounds, ("tsi",), lambda frame: {"tsi": tsi(frame["close"], 25, 13)}, 38)
    tsi_signal = [None] * len(records)
    for start, end in bounds:
        segment = pd.Series(tsi_values["tsi"][start:end], dtype="float64")
        values = ema(segment, 13).tolist()
        for offset, value in enumerate(values):
            tsi_signal[start + offset] = None if pd.isna(value) else float(value)
    tsi_package = _wrapper(timestamps=timestamps, series={"tsi": tsi_values["tsi"], "signal": tsi_signal},
        units={"tsi": "index_-100_100", "signal": "index_-100_100"}, source=source,
        parameters={"slow_period": 25, "fast_period": 13, "signal_period": 13}, warmup=51,
        calculation="double_ema_true_strength_index_with_signal_on_open_interest_close", source_status=source_status, bounds=bounds, gaps=gaps)
    williams_package = _wrapper(timestamps=timestamps, series=_segment_calculation(records, bounds, ("williams_r",),
        lambda frame: {"williams_r": williams_r(frame["high"], frame["low"], frame["close"], 14)}, 14),
        units={"williams_r": "index_-100_0"}, source=source, parameters={"period": 14}, warmup=14,
        calculation="williams_percent_r_on_open_interest_ohlc", source_status=source_status, bounds=bounds, gaps=gaps)
    width_package = _wrapper(timestamps=timestamps, series={"bandwidth": list(bb_values["bandwidth"])},
        units={"bandwidth": "ratio"}, source=source, parameters={"period": 20, "standard_deviations": 2.0}, warmup=20,
        calculation="(bollinger_upper-bollinger_lower)/bollinger_middle", source_status=source_status, bounds=bounds, gaps=gaps)
    wasserstein_values = _segment_calculation(records, bounds, ("distance",), _wasserstein, 60)
    wasserstein_package = _wrapper(timestamps=timestamps, series=wasserstein_values, units={"distance": "ratio"}, source=source,
        parameters={"recent_window_returns": 20, "reference_window_returns": 40}, warmup=61,
        calculation="first_wasserstein_distance_between_recent_and_reference_open_interest_returns",
        source_status=source_status, bounds=bounds, gaps=gaps)
    mfi_package = _wrapper(timestamps=[], series={}, units={}, source=source, parameters={"period": 14}, warmup=None, calculation=None,
        source_status=source_status, bounds=[], gaps=[], forced_status="unavailable", forced_reason="historical_volume_series_not_available")
    return {"moving_averages": moving, "bollinger_bands": bb, "regression_channel": regression,
        "bollinger_band_width": width_package, "macd": macd_package, "rsi": rsi_package, "tsi": tsi_package,
        "adx": adx_package, "stochastic": stochastic_package, "williams_r": williams_package, "atr": atr_package,
        "cci": cci_package, "wasserstein_distance": wasserstein_package, "oi_roc": roc_package, "mfi": mfi_package}

def _wasserstein(frame: pd.DataFrame) -> dict[str, pd.Series]:
    returns = pd.to_numeric(frame["close"], errors="coerce").pct_change()
    values = pd.Series(index=frame.index, dtype="float64")
    for index in range(60, len(frame)):
        reference = np.sort(returns.iloc[index - 60:index - 20].dropna().to_numpy())
        recent = np.sort(returns.iloc[index - 20:index].dropna().to_numpy())
        if len(reference) and len(recent):
            quantiles = np.linspace(0.0, 1.0, max(len(reference), len(recent)))
            values.iloc[index] = float(np.mean(np.abs(
                np.quantile(reference, quantiles) - np.quantile(recent, quantiles))))
    return {"distance": values}


def _renamed_bollinger(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {"middle": frame["bb_middle_20"], "upper": frame["bb_upper_20"], "lower": frame["bb_lower_20"],
        "bandwidth": frame["bb_bandwidth_20"], "percent_b": frame["bb_percent_b_20"]}


def _renamed_macd(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {"macd": frame["macd"], "signal": frame["macd_signal"], "histogram": frame["macd_hist"]}


def _renamed_adx(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {"adx": frame["adx_14"], "di_plus": frame["plus_di_14"], "di_minus": frame["minus_di_14"]}


def _renamed_stochastic(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {"k": frame["stoch_k_14"], "d": frame["stoch_d_14"]}


def _processed_frame(metric_id: str, timeframe: str, input_frame: Mapping[str, Any], reference_timestamp: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    records, error = _validate_frame(metric_id, timeframe, input_frame, reference_timestamp)
    source_status = input_frame.get("status") if isinstance(input_frame, Mapping) and input_frame.get("status") in VALID_STATUSES else "invalid"
    if error:
        source_status = "invalid"
    bounds, gaps = _segments(records, TIMEFRAME_SECONDS[timeframe])
    status = "invalid" if error else ("unavailable" if source_status == "unavailable" or not records else ("partial" if source_status == "partial" or gaps else source_status))
    current = copy.deepcopy(records[-1]) if records and status != "invalid" else None
    result = {"status": status, "reason": error or (input_frame.get("reason") if isinstance(input_frame, Mapping) else None), "timeframe": timeframe,
        "expected_interval_seconds": TIMEFRAME_SECONDS[timeframe], "unit": SOURCE_IDS[metric_id][1],
        "representation": "percentage_points" if metric_id == "funding_rate_ohlc" else None, "source": _source(metric_id, timeframe),
        "records": copy.deepcopy(records), "current": current, "coverage": {"records": len(records), "segment_count": len(bounds),
            "segments": _segment_metadata(records, bounds), "first_timestamp": records[0]["timestamp"] if records else None,
            "last_timestamp": records[-1]["timestamp"] if records else None, "gaps": copy.deepcopy(gaps)},
        "quality": {"source_status": source_status, "records_available": len(records), "gaps_present": bool(gaps), "warnings": []}}
    if metric_id == "open_interest_ohlc":
        timestamps, source = [row["timestamp"] for row in records], _source(metric_id, timeframe)
        result["derived"] = {
            "oi_delta": _wrapper(timestamps=timestamps, series=_oi_delta(records, bounds), units={"delta_absolute_usd": "USD", "delta_percent": "percent"}, source=source, parameters={}, warmup=2,
                calculation="close[t]-close[t-1];100*(close[t]/close[t-1]-1)", source_status=source_status, bounds=bounds, gaps=gaps),
            "oi_change_24h": _wrapper(timestamps=timestamps, series=_oi_change_24h(records, bounds), units={"change_absolute_usd": "USD", "change_percent": "percent"}, source=source,
                parameters={"seconds": 86_400, "lag_bars": 86_400 // TIMEFRAME_SECONDS[timeframe]}, warmup=CHANGE_24H_WARMUP[timeframe],
                calculation="exact_timestamp_close_change_over_86400_seconds", source_status=source_status, bounds=bounds, gaps=gaps,
                insufficient_reason="insufficient_history_for_exact_24h_change")}
        return result, _indicator_packages(records, bounds, gaps, timeframe, source_status)
    return result, None


def _event(*, timeframe: str, event_type: str, pair: str, source_metric: str, cross: Mapping[str, Any],
           first_series: str, second_series: str | None, threshold: float | None, values: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
    event_id = f"{FAMILY}:{timeframe}:{cross['timestamp']}:{event_type}:{pair}"
    return {"event_id": event_id, "event_type": event_type, "timestamp": cross["timestamp"], "timeframe": timeframe,
        "source_metric": source_metric, "first_series": first_series, "second_series": second_series, "threshold": threshold,
        "direction_numeric": cross["direction"], "previous_difference": _finite(cross["previous_difference"]),
        "current_difference": _finite(cross["current_difference"]), "values": _json_safe(dict(values)), "parameters": copy.deepcopy(dict(parameters))}


def _events_for_timeframe(timeframe: str, oi_frame: Mapping[str, Any], funding_frame: Mapping[str, Any], indicators: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    bounds = [(item["segment_start_index"], item["segment_end_index"] + 1) for item in oi_frame["coverage"]["segments"]]
    timestamps = [row["timestamp"] for row in oi_frame["records"]]
    timestamp_indices = {timestamp: index for index, timestamp in enumerate(timestamps)}
    ma_pairs = [
        ("ema_9", "ema_21"), ("ema_9", "ema_50"), ("ema_21", "ema_50"),
        ("sma_20", "sma_50"), ("sma_20", "sma_100"), ("sma_20", "sma_200"),
        ("sma_50", "sma_100"), ("sma_50", "sma_200"), ("sma_100", "sma_200"),
        ("wma_20", "wma_50"),
    ]
    specifications = [
        *(("moving_average_cross", f"{first}_x_{second}", indicators["moving_averages"], first, second, None) for first, second in ma_pairs),
        ("channel_cross", "regression_middle_x_bollinger_middle", indicators["regression_channel"], "middle", "middle", None),
        ("macd_signal_cross", "macd_x_signal", indicators["macd"], "macd", "signal", None),
        ("stochastic_cross", "k_x_d", indicators["stochastic"], "k", "d", None),
        ("directional_indicator_cross", "di_plus_x_di_minus", indicators["adx"], "di_plus", "di_minus", None),
        ("adx_threshold_cross", "adx_x_25", indicators["adx"], "adx", None, 25.0),
        ("oi_roc_zero_cross", "oi_roc_12_x_0", indicators["oi_roc"], "roc", None, 0.0),
    ]
    for event_type, pair, package, first, second, threshold in specifications:
        first_values = package["series"].get(first, [])
        if event_type == "channel_cross":
            second_values = indicators["bollinger_bands"]["series"].get("middle", [])
        else:
            second_values = package["series"].get(second, []) if second else [threshold] * len(timestamps)
        for start, end in bounds:
            crosses = detect_numeric_crosses(timestamps=timestamps[start:end], first_values=first_values[start:end], second_values=second_values[start:end],
                first_series=first, second_series=second or str(int(threshold or 0)))
            for cross in crosses:
                index = timestamp_indices[cross["timestamp"]]
                values: dict[str, Any] = {first: first_values[index], second or "threshold": second_values[index]}
                if event_type == "stochastic_cross":
                    values.update(k_value=package["series"]["k"][index], d_value=package["series"]["d"][index],
                        below_20=package["series"]["k"][index] <= 20, above_80=package["series"]["k"][index] >= 80)
                if event_type == "directional_indicator_cross":
                    adx_value = package["series"]["adx"][index]
                    values.update(di_plus=package["series"]["di_plus"][index], di_minus=package["series"]["di_minus"][index],
                        adx_value=adx_value, adx_above_25=adx_value is not None and adx_value >= 25)
                event_first = "regression_middle" if event_type == "channel_cross" else first
                event_second = "bollinger_middle" if event_type == "channel_cross" else second
                if event_type == "channel_cross":
                    values = {"regression_middle": first_values[index], "bollinger_middle": second_values[index]}
                item = _event(timeframe=timeframe, event_type=event_type, pair=pair, source_metric="open_interest_ohlc", cross=cross,
                    first_series=event_first, second_series=event_second, threshold=threshold, values=values, parameters=package["parameters"])
                output[item["event_id"]] = item
    funding_timestamps = [row["timestamp"] for row in funding_frame["records"]]
    funding_timestamp_indices = {timestamp: index for index, timestamp in enumerate(funding_timestamps)}
    funding_values = [row["close"] for row in funding_frame["records"]]
    for segment in funding_frame["coverage"]["segments"]:
        start, end = segment["segment_start_index"], segment["segment_end_index"] + 1
        for cross in detect_numeric_crosses(timestamps=funding_timestamps[start:end], first_values=funding_values[start:end],
                                            second_values=[0.0] * (end - start), first_series="funding_close", second_series="zero"):
            index = funding_timestamp_indices[cross["timestamp"]]
            item = _event(timeframe=timeframe, event_type="funding_zero_cross", pair="funding_close_x_0", source_metric="funding_rate_ohlc",
                cross=cross, first_series="funding_close", second_series=None, threshold=0.0, values={"funding_close": funding_values[index], "threshold": 0.0}, parameters={})
            output[item["event_id"]] = item
    return sorted(output.values(), key=lambda item: (item["timestamp"], item["event_type"], item["event_id"]))


def _snapshot_sections(input_snapshots: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    source = input_snapshots if isinstance(input_snapshots, Mapping) else {}

    def normalized_snapshot(name: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        raw = source.get(name)
        if not isinstance(raw, Mapping):
            return {"status": "invalid", "reason": "snapshot_payload_not_mapping"}, [], []
        payload = copy.deepcopy(dict(raw))
        if payload.get("status") in ("unavailable", "invalid"):
            return payload, [], []
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            payload.update(status="invalid", reason="snapshot_records_not_list")
            return payload, [], []
        records = [copy.deepcopy(dict(row)) for row in raw_records if isinstance(row, Mapping)]
        invalid = [{"index": index, "reason": "snapshot_record_not_mapping"}
            for index, row in enumerate(raw_records) if not isinstance(row, Mapping)]
        if invalid:
            payload.update(status="partial" if records else "invalid",
                reason="invalid_snapshot_records_isolated" if records else "snapshot_records_incompatible")
        return payload, records, invalid

    oi, oi_records, oi_invalid = normalized_snapshot("open_interest_by_exchange")
    funding, funding_records, funding_invalid = normalized_snapshot("funding_rate_by_exchange")
    options, option_records, option_invalid = normalized_snapshot("options_open_interest")

    def aggregate(payload: dict[str, Any], records: list[dict[str, Any]]) -> Mapping[str, Any] | None:
        raw = payload.get("aggregate_record")
        if isinstance(raw, Mapping):
            return copy.deepcopy(dict(raw))
        if "aggregate_record" in payload and payload.get("status") not in ("unavailable", "invalid"):
            payload.update(status="partial" if records else "invalid", reason="aggregate_record_incompatible")
        return None

    oi_aggregate = aggregate(oi, oi_records)
    option_aggregate = aggregate(options, option_records)
    reported = {key: value for key, value in (oi_aggregate or {}).items() if key.startswith("open_interest_change_percent_")}
    snapshots = {
        "open_interest_by_exchange": {"status": oi.get("status", "unavailable"), "reason": oi.get("reason"), "records": oi_records, "invalid_records": oi_invalid,
            "aggregate_record": copy.deepcopy(oi_aggregate), "exchange_count": len({row.get("exchange") for row in oi_records if row.get("exchange") != "All"}),
            "current_total_usd": (oi_aggregate or {}).get("open_interest_usd"), "reported_changes": reported},
        "funding_rate_by_exchange": {"status": funding.get("status", "unavailable"), "reason": funding.get("reason"), "records": funding_records, "invalid_records": funding_invalid,
            "stablecoin_margin_records": [copy.deepcopy(row) for row in funding_records if row.get("margin_type") == "stablecoin"],
            "token_margin_records": [copy.deepcopy(row) for row in funding_records if row.get("margin_type") == "token"],
            "exchange_count": len({row.get("exchange") for row in funding_records}),
            "next_funding_timestamps": sorted({row.get("next_funding_timestamp") for row in funding_records if type(row.get("next_funding_timestamp")) is int})},
        "options_open_interest": {"status": options.get("status", "unavailable"), "reason": options.get("reason"), "records": option_records, "invalid_records": option_invalid,
            "aggregate_record": copy.deepcopy(option_aggregate), "current_options_open_interest_usd": (option_aggregate or {}).get("open_interest_usd"),
            "current_options_contracts": (option_aggregate or {}).get("open_interest_contracts")}}
    reported_status = ("invalid" if oi.get("status") == "invalid" else
        ("available" if _finite((oi_aggregate or {}).get("open_interest_change_percent_24h")) is not None else "unavailable"))
    metrics = {"reported_24h_percent": {"status": reported_status, "reason": None if reported_status == "available" else "reported_24h_percent_unavailable",
        "value": _finite((oi_aggregate or {}).get("open_interest_change_percent_24h")), "unit": "percent", "provider": "coinglass",
        "endpoint_id": "open_interest_exchange_list", "source_scope": "all_exchanges", "observation_timestamp": None},
        "current_open_interest": {"series_current_close_usd": {}, "snapshot_current_usd": snapshots["open_interest_by_exchange"]["current_total_usd"],
            "comparison": {"status": "unavailable", "reason": "observation_scope_or_timestamp_not_comparable"}}}
    return snapshots, metrics


def _confirmations(input_confirmations: Any) -> dict[str, Any]:
    source = input_confirmations if isinstance(input_confirmations, Mapping) else {}
    open_interest = source.get("open_interest") if isinstance(source.get("open_interest"), Mapping) else {}
    funding_rate = source.get("funding_rate") if isinstance(source.get("funding_rate"), Mapping) else {}
    leverage = source.get("estimated_leverage_ratio") if isinstance(source.get("estimated_leverage_ratio"), Mapping) else {}
    metadata = {
        ("open_interest", "cryptoquant"): {"provider": "cryptoquant", "endpoint_id": "open_interest", "unit": "USD", "provider_window": "hour"},
        ("open_interest", "glassnode"): {"provider": "glassnode", "endpoint_id": "futures_open_interest_sum", "unit": "USD", "provider_interval": "1h"},
        ("funding_rate", "cryptoquant"): {"provider": "cryptoquant", "endpoint_id": "funding_rates", "unit": "percent", "provider_window": "hour"},
        ("funding_rate", "glassnode"): {"provider": "glassnode", "endpoint_id": "futures_funding_rate_perpetual", "unit": "percent", "provider_interval": "1h"},
        ("estimated_leverage_ratio", "glassnode"): {"provider": "glassnode", "endpoint_id": "futures_estimated_leverage_ratio", "unit": "ratio", "provider_interval": "1h"},
    }

    def normalized(metric: str, provider: str, payload: Any) -> dict[str, Any]:
        base = metadata[(metric, provider)]
        if not isinstance(payload, Mapping):
            return {**base, "status": "invalid", "reason": "confirmation_payload_not_mapping", "records": []}
        result = {**copy.deepcopy(dict(payload)), **base}
        records = payload.get("records")
        if not isinstance(records, list):
            return {**result, "status": "invalid", "reason": "confirmation_records_not_list", "records": []}
        valid = [copy.deepcopy(dict(row)) for row in records if isinstance(row, Mapping)]
        invalid = [{"index": index, "reason": "confirmation_record_not_mapping"}
            for index, row in enumerate(records) if not isinstance(row, Mapping)]
        result["records"] = valid
        if invalid:
            result.update(status="partial" if valid else "invalid",
                reason="invalid_confirmation_records_isolated" if valid else "confirmation_records_incompatible",
                invalid_records=invalid)
        return result

    return {"open_interest": {provider: normalized("open_interest", provider, open_interest.get(provider)) for provider in ("cryptoquant", "glassnode")},
        "funding_rate": {provider: normalized("funding_rate", provider, funding_rate.get(provider)) for provider in ("cryptoquant", "glassnode")},
        "estimated_leverage_ratio": {"glassnode": normalized("estimated_leverage_ratio", "glassnode", leverage.get("glassnode"))},
        "comparisons": {"open_interest": {"status": "unavailable", "reason": "provider_scope_not_proven_comparable"},
                        "funding_rate": {"status": "unavailable", "reason": "provider_scope_not_proven_comparable"}}}


def _aggregate(packages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    timeframes = {timeframe: package["status"] for timeframe, package in packages.items()}
    statuses = list(timeframes.values())
    status = "invalid" if "invalid" in statuses else ("unavailable" if statuses and all(item == "unavailable" for item in statuses)
        else ("available" if statuses and all(item == "available" for item in statuses) else "partial"))
    return {"status": status, "timeframes": timeframes}


def _availability(series: Mapping[str, Any], indicators: Mapping[str, Any], snapshot_metrics: Mapping[str, Any], confirmations: Mapping[str, Any]) -> dict[str, Any]:
    oi_frames, funding_frames = series["open_interest_ohlc"]["timeframes"], series["funding_rate_ohlc"]["timeframes"]
    indicator_frames = indicators["open_interest"]["timeframes"]
    availability = {"open_interest_primary": _aggregate(oi_frames), "funding_rate_primary": _aggregate(funding_frames),
        "oi_delta": _aggregate({tf: frame["derived"]["oi_delta"] for tf, frame in oi_frames.items()}),
        "oi_change_24h_derived": _aggregate({tf: frame["derived"]["oi_change_24h"] for tf, frame in oi_frames.items()}),
        "oi_change_24h_reported": copy.deepcopy(snapshot_metrics["reported_24h_percent"])}
    for key in ("moving_averages", "bollinger_bands", "regression_channel", "bollinger_band_width", "macd", "rsi", "tsi", "adx",
                "stochastic", "williams_r", "atr", "cci", "wasserstein_distance", "oi_roc", "mfi"):
        availability[key] = _aggregate({tf: frame[key] for tf, frame in indicator_frames.items()})
    availability.update({"contract_type_split": {"status": "unavailable", "reason": "dated_futures_open_interest_not_separated_by_current_sources"},
        "funding_8h_aggregate": {"status": "unavailable", "reason": "cross_exchange_8h_weighting_not_defined"},
        "confirmations": {metric: {provider: payload.get("status", "unavailable") for provider, payload in confirmations[metric].items()}
                          for metric in ("open_interest", "funding_rate", "estimated_leverage_ratio")}})
    return availability


def _quality(series: Mapping[str, Any], indicators: Mapping[str, Any], snapshots: Mapping[str, Any], confirmations: Mapping[str, Any], availability: Mapping[str, Any]) -> dict[str, Any]:
    source_statuses = {f"{metric}.{timeframe}": frame["status"] for metric, metric_payload in series.items()
        for timeframe, frame in metric_payload["timeframes"].items()}
    calculation_statuses = {f"{name}.{timeframe}": package[name]["status"] for timeframe, package in indicators["open_interest"]["timeframes"].items()
        for name in ("moving_averages", "bollinger_bands", "regression_channel", "bollinger_band_width", "macd", "rsi", "tsi", "adx",
                     "stochastic", "williams_r", "atr", "cci", "wasserstein_distance", "oi_roc")}
    calculation_statuses.update({f"oi_delta.{timeframe}": frame["derived"]["oi_delta"]["status"] for timeframe, frame in series["open_interest_ohlc"]["timeframes"].items()})
    oi_change_statuses = {f"oi_change_24h.{timeframe}": frame["derived"]["oi_change_24h"]["status"]
        for timeframe, frame in series["open_interest_ohlc"]["timeframes"].items()}
    calculation_statuses.update(oi_change_statuses)
    optional_calculations = set()
    for timeframe, frame in series["open_interest_ohlc"]["timeframes"].items():
        records = frame.get("records", [])
        if len(records) < 2 or records[-1]["timestamp"] - records[0]["timestamp"] < 86_400:
            optional_calculations.add(f"oi_change_24h.{timeframe}")
    gaps_present = any(frame["coverage"]["gaps"] for metric in series.values() for frame in metric["timeframes"].values())
    required_calculation_statuses = [value for key, value in calculation_statuses.items() if key not in optional_calculations]
    required_statuses = list(source_statuses.values()) + required_calculation_statuses
    if any(status == "invalid" for status in source_statuses.values()):
        status = "invalid"
    elif any(item != "available" for item in required_statuses) or gaps_present or any(snapshots[name]["status"] != "available" for name in ("open_interest_by_exchange", "funding_rate_by_exchange")):
        status = "partial"
    else:
        status = "ok"
    optional_invalid = [f"{metric}.{provider}" for metric in ("open_interest", "funding_rate", "estimated_leverage_ratio") for provider, payload in confirmations[metric].items()
        if not isinstance(payload, Mapping) or payload.get("status") == "invalid"]
    snapshot_warnings = [f"snapshot_{name}_invalid_records" for name in
        ("open_interest_by_exchange", "funding_rate_by_exchange", "options_open_interest")
        if snapshots[name].get("invalid_records")]
    if optional_invalid and status == "ok" or snapshot_warnings and status == "ok":
        status = "partial"
    return {"status": status, "contract_complete": True,
        "data_complete": all(item == "available" for item in required_statuses) and not optional_invalid and not snapshot_warnings and all(value.get("status") != "unavailable" for value in availability.values() if isinstance(value, Mapping)),
        "source_statuses": source_statuses, "calculation_statuses": calculation_statuses,
        "optional_calculations": sorted(optional_calculations),
        "records_processed": {metric: {timeframe: frame["coverage"]["records"] for timeframe, frame in payload["timeframes"].items()} for metric, payload in series.items()},
        "gaps_present": gaps_present, "warnings": snapshot_warnings + [f"optional_confirmation_invalid:{item}" for item in optional_invalid], "errors": []}


class OpenInterestAndFundingProcessor:
    """Validate Input and deterministically calculate Processing v0.1."""

    def __init__(self, feature_builder: OpenInterestAndFundingFeatureBuilder | None = None) -> None:
        self.feature_builder = feature_builder or OpenInterestAndFundingFeatureBuilder()

    def process(self, input_contract: Mapping[str, Any]) -> dict[str, Any]:
        source = _input_contract(input_contract)
        context, series, indicator_frames = copy.deepcopy(dict(source["context"])), {}, {}
        reference_timestamp = context["reference_timestamp"]
        for metric_id in ("open_interest_ohlc", "funding_rate_ohlc"):
            frames = {}
            for timeframe in TIMEFRAMES:
                frames[timeframe], indicator = _processed_frame(metric_id, timeframe, source["series"][metric_id]["timeframes"][timeframe], reference_timestamp)
                if indicator is not None:
                    indicator_frames[timeframe] = indicator
            endpoint_id, unit = SOURCE_IDS[metric_id]
            series[metric_id] = {"provider": "coinglass", "endpoint_id": endpoint_id, "unit": unit, "timeframes": frames}
            if metric_id == "funding_rate_ohlc":
                series[metric_id].update(aggregation="open_interest_weighted", representation="percentage_points")
        snapshots, snapshot_metrics = _snapshot_sections(source.get("snapshots"))
        for timeframe in TIMEFRAMES:
            snapshot_metrics["current_open_interest"]["series_current_close_usd"][timeframe] = series["open_interest_ohlc"]["timeframes"][timeframe]["current"]["close"] if series["open_interest_ohlc"]["timeframes"][timeframe]["current"] else None
        snapshots["open_interest_by_exchange"]["reported_changes"] = copy.deepcopy(snapshot_metrics["reported_24h_percent"])
        snapshots["current_open_interest"] = snapshot_metrics["current_open_interest"]
        indicators = {"open_interest": {"timeframes": indicator_frames}}
        all_events = {timeframe: _events_for_timeframe(timeframe, series["open_interest_ohlc"]["timeframes"][timeframe],
            series["funding_rate_ohlc"]["timeframes"][timeframe], indicator_frames[timeframe]) for timeframe in TIMEFRAMES}
        by_id = {event["event_id"]: event for timeframe in TIMEFRAMES for event in all_events[timeframe]}
        events = {"by_id": by_id, "timeframes": {timeframe: {"event_ids": [event["event_id"] for event in all_events[timeframe]]} for timeframe in TIMEFRAMES}}
        confirmations = _confirmations(source.get("confirmations"))
        availability = _availability(series, indicators, snapshot_metrics, confirmations)
        quality = _quality(series, indicators, snapshots, confirmations, availability)
        sections = {"mode": source.get("mode"), "context": context, "series": series, "indicators": indicators, "events": events,
            "snapshots": snapshots, "confirmations": confirmations, "availability": availability, "quality": quality}
        output = _json_safe(self.feature_builder.build(sections))
        json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)
        return output


def process_open_interest_and_funding(input_contract: Mapping[str, Any]) -> dict[str, Any]:
    return OpenInterestAndFundingProcessor().process(input_contract)
