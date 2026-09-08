from datetime import datetime, timezone

from app.core.series import Candle


def _timestamp(value: int, timestamp_format: str):
    if timestamp_format == "humanized":
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def serialize_metric(candles: list[Candle], getter, *, timestamp_format: str = "unix") -> list[dict]:
    return [{"t": _timestamp(candle.timestamp, timestamp_format), "v": getter(candle)} for candle in candles]


def serialize_ohlc(candles: list[Candle], *, timestamp_format: str = "unix") -> list[dict]:
    return [
        {
            "t": _timestamp(candle.timestamp, timestamp_format),
            "o": {
                "o": candle.open,
                "h": candle.high,
                "l": candle.low,
                "c": candle.close,
            },
        }
        for candle in candles
    ]


def serialize_error(message: str) -> dict:
    return {"error": message}
