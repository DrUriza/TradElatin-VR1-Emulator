from collections.abc import Callable
from dataclasses import dataclass

from app.core.history import InvalidQueryParameter
from app.core.market_state import MarketState


INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "24h": 86400,
    "1d": 86400,
    "1w": 604800,
    "1month": 2592000,
    "min": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
}


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    states: tuple[MarketState, ...]


def interval_seconds(interval: str) -> int:
    try:
        return INTERVAL_SECONDS[interval]
    except KeyError as exc:
        raise InvalidQueryParameter(f"Unsupported interval/window: {interval}") from exc


def aggregate_candles(
    history: list[MarketState],
    interval: str,
    value_getter: Callable[[MarketState], float],
    volume_getter: Callable[[MarketState], float] | None = None,
) -> list[Candle]:
    seconds = interval_seconds(interval)
    buckets: dict[int, list[MarketState]] = {}
    for item in history:
        bucket = (item.timestamp // seconds) * seconds
        buckets.setdefault(bucket, []).append(item)

    candles: list[Candle] = []
    for timestamp in sorted(buckets):
        states = buckets[timestamp]
        values = [float(value_getter(item)) for item in states]
        volume = (
            sum(float(volume_getter(item)) for item in states)
            if volume_getter is not None
            else 0.0
        )
        candles.append(
            Candle(
                timestamp=timestamp,
                open=values[0],
                high=max(values),
                low=min(values),
                close=values[-1],
                volume=volume,
                states=tuple(states),
            )
        )
    return candles


def coinglass_ms_to_seconds(value: int | None) -> int | None:
    if value is None:
        return None
    # CoinGlass V4 time filters are milliseconds. Accept seconds too so the
    # V1 emulator remains friendly to existing local callers.
    return value // 1000 if value > 10_000_000_000 else value
