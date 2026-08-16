from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping

from .common import cq_filter
from .fixture_store import FixtureStore


def _with_rows(payload: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = deepcopy(dict(payload))
    out["result"] = deepcopy(dict(payload["result"]))
    out["result"]["data"] = rows
    return out


ETF_HISTORY_ROWS = {
    ("exchange_netflow", "day"): 730,
    ("exchange_reserve", "day"): 730,
    ("exchange_reserve", "hour"): 17_544,
}


def _parse_cryptoquant_time(row: Mapping[str, Any]) -> tuple[str, datetime] | None:
    field = "datetime" if "datetime" in row else "date" if "date" in row else None
    if field is None:
        return None
    value = str(row[field])
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return field, parsed.astimezone(timezone.utc)


def _provider_shaped_history(rows: list[dict[str, Any]], *, endpoint_id: str, window: str) -> list[dict[str, Any]]:
    """Extend ETF emulator history deterministically without copying fixture rows."""
    target = ETF_HISTORY_ROWS.get((endpoint_id, window))
    if target is None or len(rows) >= target or not rows:
        return deepcopy(rows)
    time_info = _parse_cryptoquant_time(rows[0])
    if time_info is None:
        return deepcopy(rows)
    time_field, first_timestamp = time_info
    step = timedelta(hours=1) if window == "hour" else timedelta(days=1)
    seed = rows[0]
    missing = target - len(rows)
    generated: list[dict[str, Any]] = []
    numeric_fields = [name for name, value in seed.items()
                      if name != "blockheight" and isinstance(value, (int, float)) and not isinstance(value, bool)]
    for index in range(missing):
        distance = missing - index
        timestamp = first_timestamp - step * distance
        phase = index / 17.0
        record = deepcopy(seed)
        record[time_field] = (timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if time_field == "datetime"
                              else timestamp.strftime("%Y-%m-%d"))
        for offset, field in enumerate(numeric_fields):
            base = float(seed[field])
            scale = max(abs(base) * 0.00035, 0.000001)
            record[field] = round(base + scale * (0.15 * index + math.sin(phase + offset)), 6)
        if "blockheight" in record and isinstance(record["blockheight"], int):
            blocks_per_step = 6 if window == "hour" else 144
            record["blockheight"] = max(1, int(seed["blockheight"]) - distance * blocks_per_step)
        generated.append(record)
    return [*generated, *deepcopy(rows)]


def fetch(store: FixtureStore, family: str, *, endpoint_id: str, path: str,
          params: Mapping[str, Any], request: Mapping[str, Any]) -> Any:
    if family == "etf_exchange_flows":
        window = str(params.get("window", "day"))
        payload = store.load("cryptoquant", family, endpoint_id, f"{window}_raw.json")
        rows = _provider_shaped_history(list(payload["result"]["data"]), endpoint_id=endpoint_id, window=window)
        return _with_rows(payload, cq_filter(rows, params))

    if family == "long_short_liquidations":
        if endpoint_id != "cryptoquant_liquidations":
            raise ValueError(f"unsupported CryptoQuant liquidation endpoint: {endpoint_id}")
        exchange = str(params.get("exchange", "all_exchange"))
        parts = ("aggregate", "hour_raw.json") if exchange == "all_exchange" else ("exchange", exchange, "hour_raw.json")
        payload = store.load("cryptoquant", family, *parts)
        return _with_rows(payload, cq_filter(list(payload["result"]["data"]), params))

    if family == "on_chain_miners":
        if endpoint_id == "miner_entity_list":
            return store.load("cryptoquant", family, "miner_entity_list", "raw.json")
        if endpoint_id == "miner_outflow":
            payload = store.load("cryptoquant", family, "miner_outflow", str(params["miner"]), "day_raw.json")
        else:
            payload = store.load("cryptoquant", family, endpoint_id, "day_raw.json")
        return _with_rows(payload, cq_filter(list(payload["result"]["data"]), params))

    if family == "open_interest_and_funding":
        payload = store.load("cryptoquant", family, endpoint_id, "hour_raw.json")
        return _with_rows(payload, cq_filter(list(payload["result"]["data"]), params))

    if family == "cvd_volume_orderflow":
        if endpoint_id != "taker_buy_sell_stats":
            raise ValueError(f"unsupported CryptoQuant CVD endpoint: {endpoint_id}")
        payload = store.load("cryptoquant", family, "taker_buy_sell_stats", "hour_raw.json")
        return _with_rows(payload, cq_filter(list(payload["result"]["data"]), params))

    raise ValueError(f"unsupported CryptoQuant family: {family}")
