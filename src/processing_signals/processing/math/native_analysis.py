from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

import numpy as np
import pandas as pd


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def aligned(values: Sequence[Any], length: int) -> list[float | None]:
    raw = list(values)
    if len(raw) < length:
        raw = [None] * (length - len(raw)) + raw
    elif len(raw) > length:
        raw = raw[-length:]
    return [finite(value) for value in raw]


def series(values: Sequence[Any]) -> pd.Series:
    return pd.Series([np.nan if finite(v) is None else float(v) for v in values], dtype="float64")


def rolling_zscore(values: Sequence[Any], window: int = 30, min_periods: int | None = None) -> list[float | None]:
    s = series(values)
    minimum = int(min_periods or window)
    mean = s.rolling(window, min_periods=minimum).mean()
    std = s.rolling(window, min_periods=minimum).std(ddof=0).replace(0.0, np.nan)
    out = (s - mean) / std
    return [finite(v) for v in out.tolist()]


def rolling_percentile(values: Sequence[Any], window: int = 90, min_periods: int | None = None) -> list[float | None]:
    s = series(values)
    minimum = int(min_periods or max(5, min(window, 20)))

    def rank(x: np.ndarray) -> float:
        current = x[-1]
        valid = x[np.isfinite(x)]
        if not np.isfinite(current) or len(valid) == 0:
            return np.nan
        return float(np.sum(valid <= current) / len(valid))

    out = s.rolling(window, min_periods=minimum).apply(rank, raw=True)
    return [finite(v) for v in out.tolist()]


def difference(values: Sequence[Any], periods: int = 1) -> list[float | None]:
    out = series(values).diff(periods)
    return [finite(v) for v in out.tolist()]


def pct_change(values: Sequence[Any], periods: int = 1, scale: float = 100.0) -> list[float | None]:
    out = series(values).pct_change(periods=periods, fill_method=None) * float(scale)
    return [finite(v) for v in out.tolist()]


def rolling_mean(values: Sequence[Any], window: int, min_periods: int | None = None) -> list[float | None]:
    out = series(values).rolling(window, min_periods=min_periods or window).mean()
    return [finite(v) for v in out.tolist()]


def rolling_std(values: Sequence[Any], window: int, min_periods: int | None = None) -> list[float | None]:
    out = series(values).rolling(window, min_periods=min_periods or window).std(ddof=0)
    return [finite(v) for v in out.tolist()]


def normalize_01(values: Sequence[Any], window: int = 90) -> list[float | None]:
    s = series(values)
    lo = s.rolling(window, min_periods=max(5, min(window, 20))).min()
    hi = s.rolling(window, min_periods=max(5, min(window, 20))).max()
    den = (hi - lo).replace(0.0, np.nan)
    out = (s - lo) / den
    return [finite(v) for v in out.tolist()]


def rolling_wasserstein(values: Sequence[Any], recent_window: int = 20, reference_window: int = 100) -> list[float | None]:
    """Small dependency-free 1D Wasserstein proxy on equal quantile grids.

    For each point, compare a recent sample with the immediately preceding
    reference sample. Samples are independently sorted and linearly sampled on
    a shared quantile grid. This preserves the intended distribution-shift
    semantics without requiring scipy.
    """
    raw = [finite(v) for v in values]
    output: list[float | None] = [None] * len(raw)
    required = recent_window + reference_window
    for end in range(required - 1, len(raw)):
        ref = [v for v in raw[end - required + 1:end - recent_window + 1] if v is not None]
        recent = [v for v in raw[end - recent_window + 1:end + 1] if v is not None]
        if len(ref) < max(5, reference_window // 2) or len(recent) < max(5, recent_window // 2):
            continue
        n = max(len(ref), len(recent), 20)
        q = np.linspace(0.0, 1.0, n)
        ref_q = np.quantile(np.asarray(ref, dtype=float), q)
        recent_q = np.quantile(np.asarray(recent, dtype=float), q)
        output[end] = float(np.mean(np.abs(ref_q - recent_q)))
    return output


def latest(values: Sequence[Any]) -> float | None:
    for value in reversed(values):
        number = finite(value)
        if number is not None:
            return number
    return None


def align_by_timestamp(
    left_timestamps: Sequence[int], right_records: Sequence[Mapping[str, Any]], field: str,
    *, timestamp_field: str = "timestamp",
) -> list[float | None]:
    lookup: dict[int, float] = {}
    for row in right_records:
        try:
            ts = int(row[timestamp_field])
        except (KeyError, TypeError, ValueError):
            continue
        value = finite(row.get(field))
        if value is not None:
            lookup[ts] = value
    return [lookup.get(int(ts)) for ts in left_timestamps]


def interpolated_cross(
    *, previous_timestamp: int, timestamp: int,
    previous_first: Any, previous_second: Any,
    first: Any, second: Any,
) -> dict[str, float | int | None]:
    p1, p2, c1, c2 = map(finite, (previous_first, previous_second, first, second))
    if None in (p1, p2, c1, c2):
        return {"interpolation_fraction": None, "event_timestamp_exact": None, "event_value_exact": None}
    previous_difference = p1 - p2
    current_difference = c1 - c2
    denominator = current_difference - previous_difference
    if denominator == 0:
        fraction = 1.0
    else:
        fraction = -previous_difference / denominator
    fraction = max(0.0, min(1.0, float(fraction)))
    event_timestamp = float(previous_timestamp) + fraction * (float(timestamp) - float(previous_timestamp))
    event_value = p1 + fraction * (c1 - p1)
    return {
        "interpolation_fraction": fraction,
        "event_timestamp_exact": event_timestamp,
        "event_value_exact": event_value,
        "previous_first_value": p1,
        "previous_second_value": p2,
        "previous_difference": previous_difference,
        "current_difference": current_difference,
    }


def support_resistance_levels(
    highs: Sequence[Any], lows: Sequence[Any], closes: Sequence[Any], *, lookback: int = 120, levels: int = 3,
) -> dict[str, list[float]]:
    """Return nearest clustered local supports/resistances around current close.

    This is intentionally a market-structure calculation rather than classic
    pivot-point arithmetic. Candidate swing extrema are clustered within a
    small relative tolerance, ranked by touches, recency and distance, then the
    nearest levels below/above current price are selected.
    """
    h = [finite(v) for v in highs][-lookback:]
    l = [finite(v) for v in lows][-lookback:]
    c = [finite(v) for v in closes][-lookback:]
    valid_close = next((v for v in reversed(c) if v is not None), None)
    if valid_close is None:
        return {"support": [], "resistance": []}
    candidates: list[tuple[float, int, str]] = []
    n = len(c)
    radius = 2
    for i in range(radius, n - radius):
        if l[i] is not None:
            local = [v for v in l[i-radius:i+radius+1] if v is not None]
            if local and l[i] == min(local):
                candidates.append((float(l[i]), i, "support"))
        if h[i] is not None:
            local = [v for v in h[i-radius:i+radius+1] if v is not None]
            if local and h[i] == max(local):
                candidates.append((float(h[i]), i, "resistance"))
    tolerance = max(abs(valid_close) * 0.0025, 1e-12)
    clusters: list[dict[str, Any]] = []
    for value, index, kind in candidates:
        found = None
        for cluster in clusters:
            if cluster["kind"] == kind and abs(cluster["value"] - value) <= tolerance:
                found = cluster
                break
        if found is None:
            clusters.append({"value": value, "kind": kind, "touches": 1, "last_index": index})
        else:
            count = found["touches"]
            found["value"] = (found["value"] * count + value) / (count + 1)
            found["touches"] += 1
            found["last_index"] = max(found["last_index"], index)
    supports = [x for x in clusters if x["kind"] == "support" and x["value"] < valid_close]
    resistances = [x for x in clusters if x["kind"] == "resistance" and x["value"] > valid_close]
    supports.sort(key=lambda x: (valid_close - x["value"], -x["touches"], -x["last_index"]))
    resistances.sort(key=lambda x: (x["value"] - valid_close, -x["touches"], -x["last_index"]))

    support_values = [float(x["value"]) for x in supports[:levels]]
    resistance_values = [float(x["value"]) for x in resistances[:levels]]

    # Sparse swing clusters can legitimately produce fewer than the three
    # horizontal levels required by Screen A. Fill only the missing slots with
    # the nearest distinct observed lows/highs from the same lookback window.
    # This remains a direct market-data calculation; no HMI placeholder values
    # or synthetic offsets are introduced.
    def fill_nearest(selected: list[float], observed: Sequence[float | None], *, below: bool) -> list[float]:
        candidates = sorted(
            {float(v) for v in observed if v is not None and ((v < valid_close) if below else (v > valid_close))},
            key=lambda v: (valid_close - v) if below else (v - valid_close),
        )
        for value in candidates:
            fallback_tolerance = max(abs(valid_close) * 0.0002, 1e-12)
            if any(abs(value - existing) <= fallback_tolerance for existing in selected):
                continue
            selected.append(value)
            if len(selected) >= levels:
                break
        return selected[:levels]

    support_values = sorted(fill_nearest(support_values, l, below=True), reverse=True)[:levels]
    resistance_values = sorted(fill_nearest(resistance_values, h, below=False))[:levels]

    # In edge regimes (for example price sitting at a 500-bar low) there may
    # simply be fewer than N observed extrema below/above spot. Screen A still
    # requires three deterministic levels, so complete the sparse side with a
    # volatility/range-derived projection. This fallback is explicitly marked
    # as calculated rather than pretending an observed swing existed.
    finite_highs = [float(v) for v in h if v is not None]
    finite_lows = [float(v) for v in l if v is not None]
    span = (max(finite_highs) - min(finite_lows)) if finite_highs and finite_lows else 0.0
    step = max(span / max(8, levels * 4), abs(valid_close) * 0.0025, 1e-9)
    fallback_used = False
    k = 1
    while len(support_values) < levels:
        value = float(valid_close - step * k)
        k += 1
        if any(abs(value - existing) <= max(abs(valid_close) * 0.0002, 1e-12) for existing in support_values):
            continue
        support_values.append(value)
        fallback_used = True
    k = 1
    while len(resistance_values) < levels:
        value = float(valid_close + step * k)
        k += 1
        if any(abs(value - existing) <= max(abs(valid_close) * 0.0002, 1e-12) for existing in resistance_values):
            continue
        resistance_values.append(value)
        fallback_used = True
    support_values = sorted(support_values, reverse=True)[:levels]
    resistance_values = sorted(resistance_values)[:levels]
    return {
        "support": support_values,
        "resistance": resistance_values,
        "method": "swing_clusters_with_range_fallback" if fallback_used else "swing_clusters",
        "fallback_used": fallback_used,
    }


def score_to_probability(score: Any, scale: float = 1.0) -> float | None:
    value = finite(score)
    if value is None:
        return None
    x = max(-20.0, min(20.0, value / max(scale, 1e-12)))
    return float(100.0 / (1.0 + math.exp(-x)))
