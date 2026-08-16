"""Orchestration and contracts for CVD volume/order-flow Processing v0.1."""
from __future__ import annotations

import copy
import math
import time

import pandas as pd
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from processing_signals.processing.math.technical_cross_signals import detect_cross_pairs
from processing_signals.processing.prices_ohlcv.prices_ohlcv_processor import (
    PRICE_INDICATOR_CONFIG, build_regression_channel_indicator, calculate_prices_indicator_package,
)
from processing_signals.processing.math.indicators.trend.moving_averages import ema


from .cvd_volume_orderflow_feature_builder import (
    BASE_TIMEFRAMES, CVD_VOLUME_ORDERFLOW_FAMILY, DELTA_MA_PERIOD, FLOW_EFFICIENCY_PERIOD, MARKETS, PROCESSING_STAGE,
    PROCESSING_VERSION, SOURCE_FACTOR, SOURCE_TIMEFRAME, TARGET_TIMEFRAMES, TIMEFRAME_SECONDS, CvdVolumeOrderflowFeatureBuilder,
    volume_features,
)


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _clock_timestamp(clock: Callable[[], Any] | None) -> int:
    value = time.time() if clock is None else clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid_clock")
    return int(value)


def _iso_utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class CvdVolumeOrderflowProcessor:
    def __init__(self, *, feature_builder: CvdVolumeOrderflowFeatureBuilder | None = None,
                 clock: Callable[[], Any] | None = None) -> None:
        self.feature_builder = feature_builder or CvdVolumeOrderflowFeatureBuilder()
        self.clock           = clock

    def validate_input_contract(self, input_contract: Any) -> dict[str, Any]:
        if not isinstance(input_contract, Mapping):
            raise ValueError("input_contract_must_be_mapping")
        if input_contract.get("family") != CVD_VOLUME_ORDERFLOW_FAMILY:
            raise ValueError("incompatible_family")
        if input_contract.get("stage") != "input":
            raise ValueError("incompatible_stage")
        if input_contract.get("mode") not in {"bootstrap", "incremental", "recovery"}:
            raise ValueError("invalid_mode")
        context, markets = input_contract.get("context"), input_contract.get("markets")
        if not isinstance(context, Mapping) or not isinstance(markets, Mapping) or set(("spot", "futures")) - set(markets):
            raise ValueError("invalid_input_structure")
        reference = context.get("reference_timestamp")
        if type(reference) is not int or reference < 0:
            raise ValueError("invalid_reference_timestamp")
        normalized = {}
        for market in ("spot", "futures"):
            payload = markets.get(market)
            if not isinstance(payload, Mapping):
                raise ValueError("invalid_market_structure")
            timeframes = payload.get("cvd", {}).get("timeframes") if isinstance(payload.get("cvd"), Mapping) else None
            if not isinstance(timeframes, Mapping) or set(BASE_TIMEFRAMES) - set(timeframes):
                raise ValueError("missing_base_timeframes")
            normalized[market] = {}
            for timeframe in BASE_TIMEFRAMES:
                timeframe_payload = timeframes[timeframe]
                if not isinstance(timeframe_payload, Mapping) or not _sequence(timeframe_payload.get("records")):
                    raise ValueError("invalid_timeframe_payload")
                normalized[market][timeframe] = self.feature_builder.validate_base_records(timeframe_payload["records"])
                if any(row["timestamp"] > reference for row in normalized[market][timeframe]):
                    raise ValueError("timestamp_after_reference_timestamp")
        return normalized

    def build_context(self, input_contract: Mapping[str, Any], processing_timestamp: int) -> dict[str, Any]:
        context = input_contract["context"]
        required = ("base_asset", "pair_symbol", "data_mode", "is_demo", "reference_timestamp", "requested_at", "execution_timestamp")
        if any(key not in context for key in required):
            raise ValueError("incomplete_input_context")
        return {"base_asset": context["base_asset"], "pair_symbol": context["pair_symbol"], "markets": list(MARKETS),
            "base_timeframes": list(BASE_TIMEFRAMES), "available_timeframes": list(TARGET_TIMEFRAMES), "data_mode": context["data_mode"],
            "is_demo": context["is_demo"], "reference_timestamp": context["reference_timestamp"], "input_requested_at": context["requested_at"],
            "input_execution_timestamp": context["execution_timestamp"], "processing_timestamp": processing_timestamp,
            "processing_requested_at": _iso_utc(processing_timestamp)}

    def build_parameters(self) -> dict[str, Any]:
        return {"source_timeframes": copy.deepcopy(SOURCE_TIMEFRAME), "source_factors": copy.deepcopy(SOURCE_FACTOR),
            "delta_ma_period": DELTA_MA_PERIOD, "flow_efficiency_period": FLOW_EFFICIENCY_PERIOD,
            "delta_ma": {"method": "simple_moving_average", "period": DELTA_MA_PERIOD, "source_field": "volume_delta_usd"},
            "flow_efficiency": {"method": "net_delta_displacement_over_absolute_delta_path", "period": FLOW_EFFICIENCY_PERIOD, "unit": "decimal"},
            "cvd_anchor_method": "zero_before_first_available_record",
            "resampling_alignment": "utc_epoch", "recalculation_policy": "full_history_deterministic_rebuild",
            "cvd_ohlc": {"construction": "derived_from_interval_volume_delta_path", "native_ohlc": False},
            "provider_reference_used_in_calculation": False}

    @staticmethod
    def _percentile(values: Sequence[Any], current: Any) -> float | None:
        valid = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
        if not valid or not isinstance(current, (int, float)) or not math.isfinite(current):
            return None
        return sum(value <= float(current) for value in valid) / len(valid) * 100.0

    @staticmethod
    def _wasserstein_first_differences(closes: Sequence[float], *, recent_window: int = 20, reference_window: int = 100) -> list[float | None]:
        """Rolling dimensionless 1-D Wasserstein distance on CVD first differences.

        The reference window immediately precedes the recent window.  The raw
        empirical transport distance is normalized by the reference population
        standard deviation so CVD level/scale changes do not dominate the state.
        """
        differences = [float(right) - float(left) for left, right in zip(closes, closes[1:])]
        output: list[float | None] = [None] * len(closes)

        def quantile(sorted_values: list[float], q: float) -> float:
            if len(sorted_values) == 1:
                return sorted_values[0]
            position = q * (len(sorted_values) - 1)
            lower = int(math.floor(position))
            upper = min(lower + 1, len(sorted_values) - 1)
            weight = position - lower
            return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight

        required = recent_window + reference_window
        for close_index in range(1, len(closes)):
            diff_end = close_index
            if diff_end < required:
                continue
            recent = sorted(differences[diff_end - recent_window:diff_end])
            reference = sorted(differences[diff_end - required:diff_end - recent_window])
            if not recent or not reference:
                continue
            samples = max(len(recent), len(reference))
            distance = sum(
                abs(quantile(recent, (idx + 0.5) / samples) - quantile(reference, (idx + 0.5) / samples))
                for idx in range(samples)
            ) / samples
            mean = sum(reference) / len(reference)
            variance = sum((value - mean) ** 2 for value in reference) / len(reference)
            scale = max(math.sqrt(variance), 1e-12)
            output[close_index] = distance / scale
        return output

    @staticmethod
    def _augment_cvd_indicators(package: dict[str, Any], candles: Sequence[Mapping[str, Any]], market: str, timeframe: str) -> dict[str, Any]:
        """Add the CVD-only SP indicators and remove volume-dependent metrics."""
        package = copy.deepcopy(package)
        package.pop("mfi", None)
        package.pop("fibonacci_levels", None)
        package["regression_channel"] = build_regression_channel_indicator(
            records=[dict(row) for row in candles], market_type=f"cvd_{market}", timeframe=timeframe,
            window=30, deviation_multiplier=2.0,
        )

        tsi_payload = package.get("tsi", {})
        tsi_values = tsi_payload.get("series", {}).get("tsi", []) if isinstance(tsi_payload, Mapping) else []
        if isinstance(tsi_values, Sequence) and not isinstance(tsi_values, (str, bytes, bytearray)):
            signal_series = ema(pd.Series(list(tsi_values), dtype="float64"), span=13).tolist()
            signal = [None if pd.isna(value) else float(value) for value in signal_series]
            tsi_payload.setdefault("series", {})["signal"] = signal
            tsi_payload.setdefault("current", {})["signal"] = next((value for value in reversed(signal) if value is not None), None)

        bollinger = package.get("bollinger_bands", {}).get("series", {})
        upper = list(bollinger.get("upper", []))
        middle = list(bollinger.get("middle", []))
        lower = list(bollinger.get("lower", []))
        closes = [float(row["close"]) for row in candles]
        widths: list[float | None] = []
        period = 20
        for index, (u, m, l) in enumerate(zip(upper, middle, lower)):
            if u is None or m is None or l is None or index + 1 < period:
                widths.append(None)
                continue
            window = closes[index + 1 - period:index + 1]
            mean_abs = sum(abs(value) for value in window) / period
            mean = sum(window) / period
            std = math.sqrt(sum((value - mean) ** 2 for value in window) / period)
            denominator = mean_abs + std
            widths.append(None if denominator <= 0 else (float(u) - float(l)) / denominator)
        timestamps = [int(row["timestamp"]) for row in candles]
        package["bollinger_band_width"] = {
            "indicator_id": "bollinger_band_width",
            "parameters": {"period": 20, "standard_deviations": 2.0, "normalization": "rolling_mean_abs_close_plus_std"},
            "timestamps": timestamps,
            "series": {"bollinger_band_width": widths},
            "current": {"bollinger_band_width": next((value for value in reversed(widths) if value is not None), None)},
            "warmup_records": period,
            "source": {"market_type": f"cvd_{market}", "timeframe": timeframe, "is_synthetic_source": False},
            "quality": {
                "status": "ok" if any(value is not None for value in widths) else "insufficient_data",
                "valid_points": sum(value is not None for value in widths),
                "null_points": sum(value is None for value in widths),
                "required_records": period,
                "available_records": len(candles),
                "warnings": [],
            },
            "calculation": {"module": __name__, "function": "rolling_bollinger_band_width",
                "parameters": {"period": 20, "standard_deviations": 2.0}, "records": len(candles)},
        }

        wasserstein = CvdVolumeOrderflowProcessor._wasserstein_first_differences(closes)
        package["wasserstein_distance"] = {
            "indicator_id": "wasserstein_distance",
            "parameters": {"input": "close first differences", "recent_window_differences": 20, "reference_window_differences": 100},
            "timestamps": timestamps,
            "series": {"wasserstein_distance": wasserstein},
            "current": {"wasserstein_distance": next((value for value in reversed(wasserstein) if value is not None), None)},
            "warmup_records": 121,
            "source": {"market_type": f"cvd_{market}", "timeframe": timeframe, "is_synthetic_source": False},
            "quality": {
                "status": "ok" if any(value is not None for value in wasserstein) else "insufficient_data",
                "valid_points": sum(value is not None for value in wasserstein),
                "null_points": sum(value is None for value in wasserstein),
                "required_records": 121,
                "available_records": len(candles),
                "warnings": [],
            },
            "calculation": {"module": __name__, "function": "rolling_wasserstein_first_differences",
                "parameters": {"recent_window_differences": 20, "reference_window_differences": 100}, "records": len(candles)},
        }
        return package

    def build_technical_analysis(self, markets: Mapping[str, Any]) -> dict[str, Any]:
        """Precompute the frozen Screen-B CVD indicator family from true CVD OHLC."""
        output: dict[str, Any] = {}
        pairs = (
            ("ema_9", "ema_21"), ("ema_9", "ema_50"), ("ema_21", "ema_50"),
            ("sma_20", "sma_50"), ("sma_20", "sma_100"), ("sma_20", "sma_200"),
            ("sma_50", "sma_100"), ("sma_50", "sma_200"), ("sma_100", "sma_200"),
            ("wma_20", "wma_50"), ("regression_middle", "bollinger_middle"),
            ("macd", "signal"), ("di_plus", "di_minus"), ("k", "d"),
        )
        config = copy.deepcopy(PRICE_INDICATOR_CONFIG)
        config["sma_periods"] = (20, 50, 100, 200)
        for market in MARKETS:
            timeframes: dict[str, Any] = {}
            for timeframe in TARGET_TIMEFRAMES:
                source = markets[market]["timeframes"][timeframe]
                candles = [
                    {"timestamp": row["timestamp"], **copy.deepcopy(row["cvd_ohlc_usd"]),
                     "volume_usd": row.get("total_volume_usd", 0.0)}
                    for row in source["records"]
                ]
                indicators = calculate_prices_indicator_package(
                    records=candles, market_type=f"cvd_{market}", timeframe=timeframe, config=config
                ) if candles else {}
                if candles:
                    indicators = self._augment_cvd_indicators(indicators, candles, market, timeframe)
                cross_series: dict[str, Any] = {}
                for group in ("moving_averages", "macd", "adx", "stochastic"):
                    payload = indicators.get(group, {})
                    if isinstance(payload, Mapping) and isinstance(payload.get("series"), Mapping):
                        cross_series.update(payload["series"])
                regression_middle = indicators.get("regression_channel", {}).get("series", {}).get("middle")
                bollinger_middle = indicators.get("bollinger_bands", {}).get("series", {}).get("middle")
                if regression_middle is not None:
                    cross_series["regression_middle"] = regression_middle
                if bollinger_middle is not None:
                    cross_series["bollinger_middle"] = bollinger_middle
                timestamps = [row["timestamp"] for row in candles]
                crosses = detect_cross_pairs(timestamps=timestamps, series=cross_series, pairs=pairs) if candles else []
                index_by_timestamp = {int(timestamp): index for index, timestamp in enumerate(timestamps)}
                for event in crosses:
                    index = index_by_timestamp.get(int(event["timestamp"]))
                    first_name, second_name = str(event.get("first_series")), str(event.get("second_series"))
                    if index is not None:
                        first_values, second_values = cross_series.get(first_name, []), cross_series.get(second_name, [])
                        event["first_value"] = first_values[index] if index < len(first_values) else None
                        event["second_value"] = second_values[index] if index < len(second_values) else None
                timeframes[timeframe] = {
                    "status": source["status"], "reason": source.get("reason"),
                    "source_path": f"processing.markets.{market}.timeframes.{timeframe}.records",
                    "source_field": "cvd_ohlc_usd", "timestamps": timestamps, "indicators": indicators,
                    "cross_candidates": crosses, "calculation_history_records": len(candles),
                    "minimum_warmup_records": 200, "recalculate_in_hmi": False,
                }
            output[market] = {"source_chart_id": f"cvd_{market}", "title": f"CVD {market.title()}", "timeframes": timeframes}
        return {
            "analysis_id": "cvd_three_candle_technical_analysis",
            "contract_version": "1.0.0",
            "source": "CVD OHLC close/high/low series",
            "markets": output, "recalculate_in_hmi": False, "calculation_owner": "Processing",
        }

    def build_cross_market(self, markets: Mapping[str, Any]) -> dict[str, Any]:
        """Build KPI-level cross-market flow without collapsing Spot/Futures charts."""
        windows: dict[str, Any] = {}
        for window in ("1h", "24h"):
            spot = markets["spot"]["window_summaries"][window]
            futures = markets["futures"]["window_summaries"][window]
            buy = float(spot.get("taker_buy_volume_usd", 0.0) or 0.0) + float(futures.get("taker_buy_volume_usd", 0.0) or 0.0)
            sell = float(spot.get("taker_sell_volume_usd", 0.0) or 0.0) + float(futures.get("taker_sell_volume_usd", 0.0) or 0.0)
            features = volume_features(buy, sell)

            expected = 4 if window == "1h" else 96
            spot_rows = markets["spot"]["timeframes"]["15m"]["records"][-expected:]
            futures_rows = markets["futures"]["timeframes"]["15m"]["records"][-expected:]
            spot_by_ts = {row["timestamp"]: row for row in spot_rows}
            futures_by_ts = {row["timestamp"]: row for row in futures_rows}
            timestamps = sorted(set(spot_by_ts) & set(futures_by_ts))
            combined_deltas = [spot_by_ts[t]["volume_delta_usd"] + futures_by_ts[t]["volume_delta_usd"] for t in timestamps]
            denominator = sum(abs(value) for value in combined_deltas)
            efficiency = abs(sum(combined_deltas)) / denominator if denominator else None
            complete = (len(timestamps) == expected and len(spot_rows) == expected and len(futures_rows) == expected
                and all(not spot_by_ts[t].get("is_partial") and not futures_by_ts[t].get("is_partial") for t in timestamps))
            status = "available" if complete else ("partial" if timestamps else "unavailable")
            windows[window] = {
                **features,
                "flow_efficiency": {"value": efficiency, "status": "available" if efficiency is not None else "unavailable",
                    "reason": None if efficiency is not None else "zero_absolute_delta_path"},
                "records_expected": expected, "records_used": len(timestamps), "coverage_complete": complete,
                "first_timestamp": timestamps[0] if timestamps else None, "last_timestamp": timestamps[-1] if timestamps else None,
                "status": status, "reason": None if status == "available" else "cross_market_window_incomplete",
            }

        spot_1h = markets["spot"]["window_summaries"]["1h"]
        futures_1h = markets["futures"]["window_summaries"]["1h"]
        spot_volume = float(spot_1h.get("total_volume_usd", 0.0) or 0.0)
        futures_volume = float(futures_1h.get("total_volume_usd", 0.0) or 0.0)
        ratio = None if spot_volume <= 0 else futures_volume / spot_volume
        ratio_status = "available" if ratio is not None else "unavailable"

        spot_fp = markets["spot"]["footprint_summaries"]["1h"]
        futures_fp = markets["futures"]["footprint_summaries"]["1h"]
        base = float(spot_fp.get("base_volume", 0.0) or 0.0) + float(futures_fp.get("base_volume", 0.0) or 0.0)
        quote = float(spot_fp.get("quote_volume", 0.0) or 0.0) + float(futures_fp.get("quote_volume", 0.0) or 0.0)
        vwap = None if base <= 0 else quote / base
        fp_status = "available" if vwap is not None and all(p.get("status") == "available" for p in (spot_fp, futures_fp)) else (
            "partial" if vwap is not None else "unavailable")
        footprint = {
            "vwap_usd": vwap, "base_volume": base, "quote_volume": quote,
            "records_used": int(spot_fp.get("records_used", 0) or 0) + int(futures_fp.get("records_used", 0) or 0),
            "levels_used": int(spot_fp.get("levels_used", 0) or 0) + int(futures_fp.get("levels_used", 0) or 0),
            "status": fp_status, "reason": None if fp_status == "available" else "cross_market_footprint_partial",
            "calculation_basis": "combined_spot_futures_normalized_footprint", "aggregation_scope": "cross_market",
        }
        return {
            "window_summaries": windows,
            "volume_ratios": {"futures_vs_spot": {"1h": {
                "value": ratio, "status": ratio_status, "reason": None if ratio_status == "available" else "spot_volume_zero",
                "futures_volume_usd": futures_volume, "spot_volume_usd": spot_volume,
                "timestamp": max(spot_1h.get("last_timestamp") or 0, futures_1h.get("last_timestamp") or 0) or None,
            }}},
            "footprint_summaries": {"1h": footprint},
        }

    @staticmethod
    def build_provider_reconciliation(input_contract: Mapping[str, Any], markets: Mapping[str, Any]) -> dict[str, Any]:
        """Keep provider roles explicit without changing Spot/Futures visual semantics."""
        output: dict[str, Any] = {
            "policy": {
                "spot_cvd_semantic_primary": "glassnode",
                "granular_candle_source": "coinglass",
                "futures_flow_primary": "coinglass",
                "futures_glassnode_mapping": "pending_schema_verification",
                "hmi_ohlc_owner": "Processing",
            },
            "spot": {}, "futures": {},
        }
        confirmations = input_contract.get("markets", {}).get("spot", {}).get("confirmations", {}).get("glassnode", {})
        derived_1h = markets.get("spot", {}).get("timeframes", {}).get("1h", {}).get("records", [])
        derived_by_ts = {row.get("timestamp"): row for row in derived_1h if isinstance(row, Mapping)}
        for metric in ("spot_cvd_sum", "spot_vd_sum", "spot_buying_volume_sum", "spot_selling_volume_sum"):
            payload = confirmations.get(metric, {}) if isinstance(confirmations, Mapping) else {}
            records = payload.get("records", []) if isinstance(payload, Mapping) else []
            matched = 0
            for row in records:
                if isinstance(row, Mapping) and row.get("timestamp") in derived_by_ts:
                    matched += 1
            output["spot"][metric] = {
                "status": payload.get("status", "unavailable") if isinstance(payload, Mapping) else "unavailable",
                "reason": payload.get("reason") if isinstance(payload, Mapping) else "source_unavailable",
                "records": len(records) if isinstance(records, list) else 0, "matched_1h_records": matched,
                "provider": "glassnode",
            }
        cq = input_contract.get("markets", {}).get("futures", {}).get("confirmations", {}).get("cryptoquant", {})
        output["futures"]["cryptoquant_taker_buy_sell"] = {
            "status": cq.get("status", "unavailable") if isinstance(cq, Mapping) else "unavailable",
            "reason": cq.get("reason") if isinstance(cq, Mapping) else "source_unavailable",
            "records": len(cq.get("records", [])) if isinstance(cq, Mapping) and isinstance(cq.get("records"), list) else 0,
            "provider": "cryptoquant",
        }
        return output

    def evaluate_availability(self, records: Sequence[Mapping[str, Any]], *, input_status: str = "available",
                              alignment_complete: bool = True) -> tuple[str, str | None]:
        if not records:
            return "unavailable", "no_records"
        if input_status == "invalid":
            return "invalid", "input_dataset_invalid"
        if input_status in {"partial", "unavailable"}:
            return "partial", "input_dataset_partial"
        if not alignment_complete:
            return "partial", "spot_futures_timestamp_misalignment"
        current = records[-1]
        if any(row["is_partial"] for row in records):
            return "partial", "incomplete_source_bucket"
        if current["continuity_status"] == "broken":
            return "partial", "cvd_continuity_broken_by_missing_intervals"
        if current["delta_ma_21_usd"] is None or current["flow_efficiency"]["status"] != "available":
            return "partial", "rolling_warmup_incomplete"
        return "available", None

    def _timeframe_contract(self, target: str, feature: Mapping[str, Any], *, input_status: str,
                            alignment_complete: bool = True) -> dict[str, Any]:
        # build_market_features creates this list solely for the resulting
        # Processing contract.  Transfer that ownership instead of cloning the
        # full history; this method only reads it.  ``current`` remains copied so
        # callers can mutate that contractual snapshot independently of records.
        records = feature["records"]
        status, reason = self.evaluate_availability(records, input_status=input_status, alignment_complete=alignment_complete)
        return {"status": status, "reason": reason, "source_timeframe": SOURCE_TIMEFRAME[target], "target_timeframe": target,
            "interval_seconds": TIMEFRAME_SECONDS[target], "source_factor": SOURCE_FACTOR[target], "records_available": len(records),
            "first_timestamp": records[0]["timestamp"] if records else None, "last_timestamp": records[-1]["timestamp"] if records else None,
            "current_timestamp": records[-1]["timestamp"] if records else None,
            "complete_records": sum(not row["is_partial"] for row in records), "partial_records": sum(row["is_partial"] for row in records),
            "gap_count": len(feature["continuity_breaks"]), "continuity_break_count": len(feature["continuity_breaks"]),
            "continuity_breaks": copy.deepcopy(feature["continuity_breaks"]), "anchor_method": feature["anchor_method"],
            "anchor_timestamp": feature["anchor_timestamp"], "anchor_value_usd": feature["anchor_value_usd"],
            "history_relative": feature["history_relative"], "construction": feature["construction"], "native_ohlc": feature["native_ohlc"],
            "provider_reference_used_in_calculation": False, "records": records,
            "current": copy.deepcopy(records[-1]) if records else None}

    def process_market(self, market: str, base_records: Mapping[str, Sequence[Mapping[str, Any]]],
                       input_market: Mapping[str, Any]) -> dict[str, Any]:
        declared_gaps = {source: input_market["cvd"]["timeframes"][source].get("gaps", []) for source in BASE_TIMEFRAMES}
        features = self.feature_builder.build_market_features(base_records, declared_gaps)
        timeframes = {}
        for target in TARGET_TIMEFRAMES:
            source = SOURCE_TIMEFRAME[target]
            input_status = input_market["cvd"]["timeframes"][source].get("status", "available")
            timeframes[target] = self._timeframe_contract(target, features[target], input_status=input_status)
        summaries = {name: self.feature_builder.build_fixed_window_summary(timeframes["15m"]["records"], name) for name in ("1h", "24h")}
        footprint = self.feature_builder.build_footprint_vwap(input_market.get("footprint"))
        availability = {"timeframes": {target: {"status": payload["status"], "reason": payload["reason"]} for target, payload in timeframes.items()},
            "window_summaries": {name: {"status": payload["status"], "reason": payload["reason"]} for name, payload in summaries.items()},
            "footprint_vwap": {"status": footprint["status"], "reason": footprint["reason"]}}
        return {"timeframes": timeframes, "window_summaries": summaries, "footprint_summaries": {"1h": footprint},
            "price_vs_vwap": {}, "availability": availability}

    def evaluate_quality(self, markets: Mapping[str, Any], input_quality: Mapping[str, Any]) -> dict[str, Any]:
        core = [markets[market]["timeframes"][timeframe]["status"] for market in MARKETS for timeframe in TARGET_TIMEFRAMES]
        enrichments = [markets[market]["footprint_summaries"]["1h"]["status"] for market in MARKETS]
        enrichments.extend(markets[market]["price_vs_vwap"]["status"] for market in MARKETS)
        summaries = [markets[market]["window_summaries"][window]["status"] for market in MARKETS for window in ("1h", "24h")]
        no_safe_base = all(markets[market]["timeframes"][timeframe]["status"] == "unavailable"
            for market in ("spot", "futures") for timeframe in BASE_TIMEFRAMES)
        core_status = "invalid" if no_safe_base or "invalid" in core or input_quality.get("status") == "invalid" else (
            "available" if all(item == "available" for item in core + summaries) else "partial")
        enrichment_status = "available" if all(item == "available" for item in enrichments) else "partial"
        # Footprint/price-reference enrichments are optional. They are reported
        # independently but do not downgrade a complete CVD core contract.
        status = "invalid" if core_status == "invalid" else ("ok" if core_status == "available" else "partial")
        warnings = [f"{market}.{timeframe}:{markets[market]['timeframes'][timeframe]['reason']}" for market in MARKETS for timeframe in TARGET_TIMEFRAMES
            if markets[market]["timeframes"][timeframe]["status"] != "available"]
        warnings.extend(f"input_quality:{input_quality.get('status')}" for _ in [0] if input_quality.get("status") not in {None, "ok"})
        errors = [item for item in warnings if "invalid" in item]
        return {"status": status, "core_status": core_status, "enrichment_status": enrichment_status,
            "warnings": warnings, "errors": errors}

    def run(self, input_contract: Mapping[str, Any], *, price_reference_by_market: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        normalized = self.validate_input_contract(input_contract)
        processing_timestamp = _clock_timestamp(self.clock)
        input_markets = input_contract["markets"]
        spot = self.process_market("spot", normalized["spot"], input_markets["spot"])
        futures = self.process_market("futures", normalized["futures"], input_markets["futures"])
        markets = {"spot": spot, "futures": futures}
        references = price_reference_by_market or {}
        if not isinstance(references, Mapping) or set(references) - set(MARKETS):
            raise ValueError("invalid_price_reference_markets")
        for market in MARKETS:
            markets[market]["price_vs_vwap"] = self.feature_builder.build_price_vs_vwap(
                markets[market]["footprint_summaries"]["1h"], references.get(market))
            markets[market]["availability"]["price_vs_vwap"] = {"status": markets[market]["price_vs_vwap"]["status"],
                "reason": markets[market]["price_vs_vwap"]["reason"]}
        cross_market = self.build_cross_market(markets)
        provider_reconciliation = self.build_provider_reconciliation(input_contract, markets)
        quality = self.evaluate_quality(markets, input_contract.get("quality", {}))
        return {"family": CVD_VOLUME_ORDERFLOW_FAMILY, "stage": PROCESSING_STAGE, "version": PROCESSING_VERSION,
            "mode": input_contract["mode"], "context": self.build_context(input_contract, processing_timestamp),
            "parameters": self.build_parameters(), "markets": markets, "cross_market": cross_market,
            "provider_reconciliation": provider_reconciliation,
            "technical_analysis": self.build_technical_analysis(markets), "quality": quality}


def process_cvd_volume_orderflow(input_contract: Mapping[str, Any], *, price_reference_by_market: Mapping[str, Mapping[str, Any]] | None = None,
                                 clock: Callable[[], Any] | None = None) -> dict[str, Any]:
    return CvdVolumeOrderflowProcessor(clock=clock).run(input_contract, price_reference_by_market=price_reference_by_market)
