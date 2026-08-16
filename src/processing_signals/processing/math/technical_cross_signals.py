from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def detect_numeric_crosses(
    *,
    timestamps: Sequence[int],
    first_values: Sequence[Any],
    second_values: Sequence[Any],
    first_series: str,
    second_series: str,
) -> list[dict[str, Any]]:
    """Detect mathematical series crossings without semantic presentation."""
    if not (len(timestamps) == len(first_values) == len(second_values)):
        raise ValueError("timestamps and cross series must have equal length")
    events: list[dict[str, Any]] = []
    previous_difference: float | None = None
    for timestamp, first, second in zip(timestamps, first_values, second_values, strict=True):
        first_number  = _finite(first)
        second_number = _finite(second)
        if first_number is None or second_number is None:
            previous_difference = None
            continue
        difference = first_number - second_number
        direction  = 0
        suffix     = ""
        if previous_difference is not None:
            if previous_difference <= 0 < difference:
                direction, suffix = 1, "above"
            elif previous_difference >= 0 > difference:
                direction, suffix = -1, "below"
        if direction:
            events.append(
                {
                    "timestamp": int(timestamp),
                    "cross_id": f"{first_series}_{suffix}_{second_series}",
                    "first_series": first_series,
                    "second_series": second_series,
                    "direction": direction,
                    "previous_difference": previous_difference,
                    "current_difference": difference,
                }
            )
        previous_difference = difference
    return events


def detect_cross_pairs(
    *,
    timestamps: Sequence[int],
    series: Mapping[str, Sequence[Any]],
    pairs: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for first, second in pairs:
        if first not in series or second not in series:
            continue
        events.extend(
            detect_numeric_crosses(
                timestamps=timestamps,
                first_values=series[first],
                second_values=series[second],
                first_series=first,
                second_series=second,
            )
        )
    return sorted(events, key=lambda event: (event["timestamp"], event["first_series"], event["second_series"]))


# Compatibility APIs retained for existing consumers. Prices uses only the
# presentation-neutral functions above.
def detect_series_crosses(
    fast_series: Sequence[Mapping[str, Any]],
    slow_series: Sequence[Mapping[str, Any]],
    *,
    event_type: str,
    id_prefix: str,
    fast_series_name: str,
    slow_series_name: str,
) -> list[dict[str, Any]]:
    fast       = {int(item["timestamp"]): item.get("value") for item in fast_series}
    slow       = {int(item["timestamp"]): item.get("value") for item in slow_series}
    timestamps = sorted(set(fast) & set(slow))
    numeric    = detect_numeric_crosses(
        timestamps=timestamps,
        first_values=[fast[timestamp] for timestamp in timestamps],
        second_values=[slow[timestamp] for timestamp in timestamps],
        first_series=fast_series_name,
        second_series=slow_series_name,
    )
    return [
        {
            "id": f"{id_prefix}_{event['timestamp']}_{'bullish' if event['direction'] == 1 else 'bearish'}",
            "timestamp": event["timestamp"],
            "event_type": event_type,
            "direction": "bullish" if event["direction"] == 1 else "bearish",
            "marker": "arrow_up" if event["direction"] == 1 else "arrow_down",
            "fast_series": fast_series_name,
            "slow_series": slow_series_name,
        }
        for event in numeric
    ]


def detect_zero_crosses(
    series: Sequence[Mapping[str, Any]],
    *,
    event_type: str,
    id_prefix: str,
    positive_event: str,
    negative_event: str,
    event_field: str,
    anchor_series: str,
    value_field: str,
) -> list[dict[str, Any]]:
    values     = [item.get("value") for item in series]
    timestamps = [int(item["timestamp"]) for item in series]
    events     = detect_numeric_crosses(
        timestamps=timestamps,
        first_values=values,
        second_values=[0.0] * len(values),
        first_series=anchor_series,
        second_series="zero",
    )
    output = []
    for event in events:
        regime = positive_event if event["direction"] == 1 else negative_event
        value  = next(item.get("value") for item in series if int(item["timestamp"]) == event["timestamp"])
        output.append(
            {
                "id": f"{id_prefix}_{event['timestamp']}_{regime}",
                "timestamp": event["timestamp"],
                "event_type": event_type,
                event_field: regime,
                value_field: value,
                "anchor_series": anchor_series,
                "direction": "neutral",
                "marker": "diamond",
            }
        )
    return output


def attach_ma_crosses_to_ohlc(
    candles: Sequence[Mapping[str, Any]],
    indicator_series: Mapping[str, Sequence[Mapping[str, Any]]],
    pairs: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    output              = [deepcopy(dict(candle)) | {"ma_crosses": []} for candle in candles]
    candle_by_timestamp = {int(candle["timestamp"]): candle for candle in output}
    atr                 = {
        int(item["timestamp"]): float(item["value"])
        for item in indicator_series.get("atr_14", [])
        if _finite(item.get("value")) is not None
    }
    for first, second in pairs:
        events = detect_series_crosses(
            indicator_series.get(first, []),
            indicator_series.get(second, []),
            event_type="ma_cross",
            id_prefix=f"{first}_x_{second}",
            fast_series_name=first,
            slow_series_name=second,
        )
        for event in events:
            candle = candle_by_timestamp.get(event["timestamp"])
            if candle is None or not candle.get("is_closed", True):
                continue
            offset        = atr.get(event["timestamp"])
            quality_flags = []
            if offset is None:
                offset = max(float(candle["high"]) - float(candle["low"]), 1e-9) * 0.1
                quality_flags.append("marker_offset_without_atr")
            marker_price = (
                float(candle["low"]) - offset * 0.2
                if event["direction"] == "bullish"
                else float(candle["high"]) + offset * 0.2
            )
            candle["ma_crosses"].append(
                event
                | {
                    "pair": f"{first}_x_{second}",
                    "marker_price": marker_price,
                    "quality_flags": quality_flags,
                }
            )
    return output


def detect_indicator_crosses(source: Any, indicators: Any) -> dict[str, list[dict[str, Any]]]:
    timestamps = [int(value) for value in source["timestamp"].tolist()]
    closed     = [bool(value) for value in source.get("is_closed", [True] * len(timestamps))]
    output: dict[str, list[dict[str, Any]]] = {"stochastic": [], "macd": [], "adx": []}

    def pair_cross_events(first: str, second: str) -> list[dict[str, Any]]:
        if first not in indicators or second not in indicators:
            return []
        return detect_numeric_crosses(
            timestamps=timestamps,
            first_values=indicators[first].tolist(),
            second_values=indicators[second].tolist(),
            first_series=first,
            second_series=second,
        )

    for event in pair_cross_events("stoch_k_14", "stoch_d_14"):
        index   = timestamps.index(event["timestamp"])
        k_value = _finite(indicators["stoch_k_14"].iloc[index])
        if not closed[index] or k_value is None or not (k_value <= 20 or k_value >= 80):
            continue
        direction = "bullish" if event["direction"] == 1 else "bearish"
        output["stochastic"].append(
            {
                "id": f"stochastic_cross_{event['timestamp']}_{direction}",
                "timestamp": event["timestamp"],
                "event_type": "stochastic_cross",
                "direction": direction,
                "zone": "oversold" if k_value <= 20 else "overbought",
                "marker": "arrow_up" if direction == "bullish" else "arrow_down",
            }
        )

    for event in pair_cross_events("macd", "macd_signal"):
        index = timestamps.index(event["timestamp"])
        if not closed[index]:
            continue
        direction = "bullish" if event["direction"] == 1 else "bearish"
        output["macd"].append(
            {
                "id": f"macd_signal_cross_{event['timestamp']}_{direction}",
                "timestamp": event["timestamp"],
                "event_type": "macd_signal_cross",
                "direction": direction,
                "marker": "arrow_up" if direction == "bullish" else "arrow_down",
                "histogram_value": _finite(indicators["macd_hist"].iloc[index]),
                "zero_zone": "below_zero" if float(indicators["macd"].iloc[index - 1]) < 0 else "above_zero",
            }
        )

    adx_values = indicators["adx_14"].tolist() if "adx_14" in indicators else []
    if not adx_values:
        return output
    for index in range(1, len(timestamps)):
        if not closed[index]:
            continue
        previous_adx, current_adx = _finite(adx_values[index - 1]), _finite(adx_values[index])
        if previous_adx is not None and current_adx is not None:
            regime = None
            if previous_adx < 25 <= current_adx:
                regime = "strength_on"
            elif previous_adx >= 25 > current_adx:
                regime = "strength_off"
            if regime:
                output["adx"].append(
                    {
                        "id": f"adx_threshold_cross_{timestamps[index]}_{regime}",
                        "timestamp": timestamps[index],
                        "event_type": "adx_threshold_cross",
                        "direction": "neutral",
                        "marker": "diamond",
                    }
                )
    for event in pair_cross_events("plus_di_14", "minus_di_14"):
        index = timestamps.index(event["timestamp"])
        if not closed[index]:
            continue
        direction = "bullish" if event["direction"] == 1 else "bearish"
        output["adx"].append(
            {
                "id": f"di_cross_{event['timestamp']}_{direction}",
                "timestamp": event["timestamp"],
                "event_type": "di_cross",
                "direction": direction,
                "marker": "arrow_up" if direction == "bullish" else "arrow_down",
                "strength_confirmed": bool(_finite(indicators["adx_14"].iloc[index]) is not None and float(indicators["adx_14"].iloc[index]) >= 25),
            }
        )
    for key in output:
        output[key].sort(key=lambda event: (event["timestamp"], event["event_type"]))
    return output
