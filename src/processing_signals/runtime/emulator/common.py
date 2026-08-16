from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def to_unix_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    if not text:
        return None
    # CryptoQuant also uses compact YYYYMMDD / YYYYMMDDTHHMMSS forms.
    # Parse those before generic numeric timestamps.
    for fmt in ("%Y%m%d", "%Y%m%dT%H%M%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    if text.isdigit():
        number = int(text)
        return number // 1000 if number > 10_000_000_000 else number
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def filter_scalar_records(rows: list[dict[str, Any]], *, start: Any = None, end: Any = None,
                          time_key: str = "time", time_is_ms: bool = False,
                          limit: int | None = None, newest: bool = True) -> list[dict[str, Any]]:
    start_s, end_s = to_unix_seconds(start), to_unix_seconds(end)
    out = []
    for row in rows:
        raw = row.get(time_key)
        if raw is None:
            out.append(row)
            continue
        ts = int(raw)
        if time_is_ms or ts > 10_000_000_000:
            ts //= 1000
        if start_s is not None and ts < start_s:
            continue
        if end_s is not None and ts > end_s:
            continue
        out.append(row)
    if limit is not None:
        if int(limit) <= 0:
            raise ValueError("limit must be positive")
        out = out[-int(limit):] if newest else out[:int(limit)]
    return out


def cq_filter(rows: list[dict[str, Any]], params: Mapping[str, Any]) -> list[dict[str, Any]]:
    start = params.get("from", params.get("start_time"))
    end = params.get("to", params.get("end_time"))
    start_s, end_s = to_unix_seconds(start), to_unix_seconds(end)
    out = []
    for row in rows:
        ts = to_unix_seconds(row.get("datetime", row.get("date")))
        if ts is not None and start_s is not None and ts < start_s:
            continue
        if ts is not None and end_s is not None and ts > end_s:
            continue
        out.append(row)
    limit = int(params.get("limit", len(out)))
    return out[-limit:]
