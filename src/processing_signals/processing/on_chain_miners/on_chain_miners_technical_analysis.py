from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from processing_signals.processing.math.technical_cross_signals import detect_numeric_crosses
# Reuse the generic OHLC indicator mathematics already validated by OI/Funding.
# This module rewrites all OI-specific metadata before exposing the package.
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_processor import _indicator_packages

TARGETS = {
    "miner_reserve": ("miner_reserve_btc", "BTC"),
    "sopr_7d": ("sopr_7d", "ratio"),
    "hashrate": ("hashrate_eh_s", "EH/s"),
    "difficulty": ("difficulty_t", "T"),
}
MA_PAIRS = (
    ("ema_9", "ema_21"), ("ema_9", "ema_50"), ("ema_21", "ema_50"),
    ("sma_20", "sma_50"), ("sma_20", "sma_100"), ("sma_20", "sma_200"),
    ("sma_50", "sma_100"), ("sma_50", "sma_200"), ("sma_100", "sma_200"),
    ("wma_20", "wma_50"),
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return 0.0 if float(value) == 0.0 else float(value)


def _normalize_package_metadata(package: Mapping[str, Any], *, chart_id: str, unit: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(package))
    out["source"] = {"family": "on_chain_miners", "chart_id": chart_id, "resolution": "1d", "unit": unit,
                     "ohlc_origin": "processing_derived", "provider": "glassnode"}
    units = out.get("units")
    if isinstance(units, dict):
        for key in list(units):
            if key in {"middle", "upper", "lower", "macd", "signal", "histogram", "atr"}:
                units[key] = unit
    calculation = out.get("calculation")
    if isinstance(calculation, str):
        out["calculation"] = calculation.replace("open_interest", chart_id)
    return out


def _events(chart_id: str, candles: Sequence[Mapping[str, Any]], indicators: Mapping[str, Any]) -> list[dict[str, Any]]:
    timestamps = [int(c["timestamp"]) for c in candles]
    by_ts = {ts: index for index, ts in enumerate(timestamps)}
    events: dict[str, dict[str, Any]] = {}

    def add(group: str, first: str, second: str, package: Mapping[str, Any], *, indicator_id: str | None = None,
            stochastic_gate: bool = False) -> None:
        series = package.get("series", {}) if isinstance(package, Mapping) else {}
        first_values = series.get(first, []) if isinstance(series, Mapping) else []
        second_values = series.get(second, []) if isinstance(series, Mapping) else []
        if not (isinstance(first_values, list) and isinstance(second_values, list) and len(first_values) == len(timestamps) == len(second_values)):
            return
        for cross in detect_numeric_crosses(timestamps=timestamps, first_values=first_values, second_values=second_values,
                                            first_series=first, second_series=second):
            index = by_ts[cross["timestamp"]]
            if stochastic_gate:
                k_value = _finite(first_values[index])
                if k_value is None or not (k_value <= 20 or k_value >= 80):
                    continue
            relation = "above" if int(cross["direction"]) == 1 else "below"
            event_id = f"{first}_{relation}_{second}"
            uid = f"{chart_id}:{cross['timestamp']}:technical_cross:{event_id}"
            events[uid] = {
                "event_uid": uid, "event_id": event_id, "event_type": "technical_cross", "event_group": group,
                "timestamp": int(cross["timestamp"]), "signal": "bullish" if int(cross["direction"]) == 1 else "bearish",
                "cross_direction": relation, "label": event_id.replace("_", " ").upper(),
                "selection_requirements": [first, second],
                "source": {"market": "general", "chart_id": chart_id, "range": "calculation_history"},
                "calculation": {"first_series": first, "second_series": second,
                                "previous_difference": _finite(cross.get("previous_difference")),
                                "current_difference": _finite(cross.get("current_difference")),
                                "first_value": _finite(first_values[index]), "second_value": _finite(second_values[index])},
                "importance": {"score": 0.88 if group in {"moving_average_cross", "channel_cross"} else 0.72},
                "display": {"screen_a": group in {"moving_average_cross", "channel_cross"}, "screen_b": True},
            }
            if indicator_id:
                events[uid]["indicator_id"] = indicator_id

    moving = indicators.get("moving_averages", {})
    for first, second in MA_PAIRS:
        add("moving_average_cross", first, second, moving)

    reg = indicators.get("regression_channel", {})
    bb = indicators.get("bollinger_bands", {})
    reg_series = reg.get("series", {}) if isinstance(reg, Mapping) else {}
    bb_series = bb.get("series", {}) if isinstance(bb, Mapping) else {}
    if isinstance(reg_series, Mapping) and isinstance(bb_series, Mapping):
        package = {"series": {"regression_middle": reg_series.get("middle", []), "bollinger_middle": bb_series.get("middle", [])}}
        add("channel_cross", "regression_middle", "bollinger_middle", package)

    add("macd_cross", "macd", "signal", indicators.get("macd", {}), indicator_id="macd")
    add("stochastic_cross", "k", "d", indicators.get("stochastic", {}), indicator_id="stochastic", stochastic_gate=True)
    add("adx_cross", "di_plus", "di_minus", indicators.get("adx", {}), indicator_id="adx")
    return sorted(events.values(), key=lambda item: (item["timestamp"], item["event_group"], item["event_uid"]))


def build_on_chain_technical_analysis(series: Mapping[str, Any]) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for chart_id, (series_id, unit) in TARGETS.items():
        payload = series.get(series_id, {}) if isinstance(series, Mapping) else {}
        candles = payload.get("daily_candles", []) if isinstance(payload, Mapping) else []
        candles = [copy.deepcopy(dict(c)) for c in candles if isinstance(c, Mapping)]
        if not candles:
            targets[chart_id] = {"status": "unavailable", "unit": unit, "candles": [], "indicators": {}, "events": []}
            continue
        packages = _indicator_packages(candles, [(0, len(candles))], [], "1d", str(payload.get("status", "available")))
        keep = ("moving_averages", "bollinger_bands", "regression_channel", "bollinger_band_width", "macd", "rsi", "tsi",
                "adx", "stochastic", "williams_r", "atr", "cci", "wasserstein_distance")
        indicators = {name: _normalize_package_metadata(packages[name], chart_id=chart_id, unit=unit) for name in keep}
        targets[chart_id] = {"status": str(payload.get("status", "available")), "unit": unit, "candles": candles,
                             "indicators": indicators, "events": _events(chart_id, candles, indicators)}
    return {"status": "available" if all(x.get("status") in {"available", "partial"} for x in targets.values()) else "partial",
            "recalculate_in_hmi": False, "targets": targets}
