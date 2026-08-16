from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable

from .policies import ResamplingClass, endpoint_policy


@dataclass(frozen=True)
class DirtyWindow:
    family: str
    source_timeframe: str
    target_timeframe: str
    start_timestamp: int
    end_timestamp: int
    cause: str
    sealed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

# Maximum configured dependency context used by the existing Prices indicator
# package. The values describe input points, not values copied from a golden.
PRICE_INDICATOR_LOOKBACKS = {
    "sma": 200, "ema": 50, "bollinger": 20, "rsi": 14,
    "macd": 35, "adx": 28, "atr": 14, "stochastic": 17,
    "tsi": 43, "mfi": 14, "cci": 20, "williams_r": 14,
    "regression_channel": 100, "wasserstein": 120,
}


def _bucket(timestamp: int, timeframe: str) -> tuple[int, int]:
    seconds = SECONDS[timeframe]
    start = timestamp - timestamp % seconds
    return start, start + seconds


def plan_dirty_windows(rows: Iterable[dict[str, Any]], *, reference_timestamp: int,
                       endpoint_id: str = "spot_ohlcv") -> list[DirtyWindow]:
    result: list[DirtyWindow] = []
    policy = endpoint_policy(endpoint_id)
    for row in rows:
        family = str(row["family"])
        source = str(row.get("source_timeframe") or "native")
        start, end = int(row["start_timestamp"]), int(row["end_timestamp"])
        result.append(DirtyWindow(family, source, source, start, end, str(row["cause"]), True))
        if family != "prices_ohlcv" or source != "1m" or policy.resampling_class != ResamplingClass.RESAMPLEABLE_OHLC:
            continue
        # 15m and 1d are native incremental feeds for Prices. Only derived 5m,
        # 1h and 4h buckets can be dirtied by the 1m stream.
        for target in ("5m", "1h", "4h"):
            bucket_start, bucket_end = _bucket(start, target)
            source_is_final_member = start + SECONDS[source] == bucket_end
            sealed = source_is_final_member and bucket_end <= int(reference_timestamp)
            if sealed:
                result.append(DirtyWindow(family, source, target, bucket_start, bucket_end - 1,
                                          "RESAMPLED_BUCKET_CHANGE", True))
    unique = {(item.family, item.source_timeframe, item.target_timeframe,
               item.start_timestamp, item.end_timestamp, item.cause): item for item in result}
    return list(unique.values())


def recompute_plan(windows: Iterable[DirtyWindow]) -> dict[str, Any]:
    items = list(windows)
    timeframes = sorted({item.target_timeframe for item in items})
    maximum_context = max(PRICE_INDICATOR_LOOKBACKS.values())
    return {
        "mode": "INCREMENTAL_WINDOWED" if items else "NOOP",
        "timeframes": timeframes,
        "required_lookback_points": maximum_context if items else 0,
        "indicator_points_recomputed": sum(maximum_context + 2 for _ in timeframes),
        "event_context_points": 2 if items else 0,
    }
