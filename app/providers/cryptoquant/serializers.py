from datetime import datetime, timezone

from app.core.series import Candle


def _date(timestamp: int, window: str) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if window in {"day", "week", "month"}:
        return dt.strftime("%Y-%m-%d")
    if window == "hour":
        return dt.strftime("%Y-%m-%dT%H:00:00")
    return dt.strftime("%Y-%m-%dT%H:%M:00")


def _envelope(window: str, data: list[dict]) -> dict:
    return {"status": {"code": 200, "message": "success"}, "result": {"window": window, "data": data}}


def serialize_exchange_flow(candles: list[Candle], window: str, kind: str) -> dict:
    rows = []
    for candle in candles:
        states = candle.states
        if kind == "inflow":
            values = [item.exchange_inflow for item in states]
            total = sum(values)
            rows.append(
                {
                    "date": _date(candle.timestamp, window),
                    "inflow_total": total,
                    "inflow_top10": total * 0.63,
                    "inflow_mean": total / max(len(values), 1),
                }
            )
        elif kind == "outflow":
            values = [item.exchange_outflow for item in states]
            total = sum(values)
            rows.append(
                {
                    "date": _date(candle.timestamp, window),
                    "outflow_total": total,
                    "outflow_top10": total * 0.61,
                    "outflow_mean": total / max(len(values), 1),
                }
            )
        else:
            state = states[-1]
            rows.append(
                {
                    "date": _date(candle.timestamp, window),
                    "reserve": state.exchange_reserve,
                    "reserve_usd": state.exchange_reserve * state.btc_price,
                }
            )
    return _envelope(window, rows)


def serialize_mpi(candles: list[Candle], window: str) -> dict:
    return _envelope(
        window,
        [
            {
                "date": _date(candle.timestamp, window),
                "mpi": candle.close,
            }
            for candle in candles
        ],
    )


def serialize_error(message: str, code: int = 400) -> dict:
    return {"status": {"code": code, "message": message}, "result": None}
