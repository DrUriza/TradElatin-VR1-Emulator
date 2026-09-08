from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.history import InvalidQueryParameter, filter_by_time_range, validate_allowed_value
from app.core.series import aggregate_candles, interval_seconds
from app.core.simulation_engine import engine
from app.settings import EMULATOR_RECORD_COUNT
from app.providers.cryptoquant.authentication import is_valid_cryptoquant_token
from app.providers.cryptoquant.serializers import serialize_error, serialize_exchange_flow, serialize_mpi

router = APIRouter(tags=["CryptoQuant"])

ALLOWED_EXCHANGES = {"binance", "okx", "bybit", "all_exchange", "spot_exchange", "derivative_exchange"}
ALLOWED_WINDOWS = {"min", "hour", "day", "week", "month"}


def _auth(authorization: str | None) -> None:
    if not is_valid_cryptoquant_token(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _parse_date(value: str, param_name: str) -> int:
    formats = ("%Y%m%dT%H%M%S", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
    for fmt in formats:
        try:
            return int(datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError as exc:
        raise InvalidQueryParameter(f"Invalid date for '{param_name}': {value}") from exc


def _query_history(window: str, from_: str | None, to: str | None, limit: int, format_: str) -> tuple[list, str]:
    validate_allowed_value(window, ALLOWED_WINDOWS, "window")
    validate_allowed_value(format_, {"json"}, "format")
    # Keep accepting provider-native range/limit params, but the Emulator
    # intentionally serves a deterministic rolling 500-record window.
    _ = (from_, limit)
    end_timestamp = _parse_date(to, "to") if to else engine.get_state().timestamp
    seconds = interval_seconds(window)
    history = engine.get_synthetic_history(
        interval_seconds=seconds,
        periods=EMULATOR_RECORD_COUNT,
        end_timestamp=end_timestamp,
        samples_per_period=1,
    )
    return history, window


def _bad_request(exc: InvalidQueryParameter) -> JSONResponse:
    return JSONResponse(status_code=400, content=serialize_error(str(exc)))


def _exchange_flow(kind: str, exchange: str, window: str, from_: str | None, to: str | None, limit: int, format_: str, authorization: str | None):
    _auth(authorization)
    try:
        validate_allowed_value(exchange, ALLOWED_EXCHANGES, "exchange")
        history, window = _query_history(window, from_, to, limit, format_)
        getter = {
            "inflow": lambda item: item.exchange_inflow,
            "outflow": lambda item: item.exchange_outflow,
            "reserve": lambda item: item.exchange_reserve,
        }[kind]
        candles = aggregate_candles(history, window, getter)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_exchange_flow(candles, window, kind)


@router.get("/btc/exchange-flows/inflow")
def get_exchange_inflow(
    exchange: str = Query(default="all_exchange"), window: str = Query(default="day"),
    from_: str | None = Query(default=None, alias="from"), to: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100000), format_: str = Query(default="json", alias="format"),
    authorization: str | None = Header(default=None),
):
    return _exchange_flow("inflow", exchange, window, from_, to, limit, format_, authorization)


@router.get("/btc/exchange-flows/outflow")
def get_exchange_outflow(
    exchange: str = Query(default="all_exchange"), window: str = Query(default="day"),
    from_: str | None = Query(default=None, alias="from"), to: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100000), format_: str = Query(default="json", alias="format"),
    authorization: str | None = Header(default=None),
):
    return _exchange_flow("outflow", exchange, window, from_, to, limit, format_, authorization)


@router.get("/btc/exchange-flows/reserve")
def get_exchange_reserve(
    exchange: str = Query(default="all_exchange"), window: str = Query(default="day"),
    from_: str | None = Query(default=None, alias="from"), to: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100000), format_: str = Query(default="json", alias="format"),
    authorization: str | None = Header(default=None),
):
    return _exchange_flow("reserve", exchange, window, from_, to, limit, format_, authorization)


@router.get("/btc/flow-indicator/mpi")
def get_mpi(
    window: str = Query(default="day"),
    from_: str | None = Query(default=None, alias="from"), to: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100000), format_: str = Query(default="json", alias="format"),
    authorization: str | None = Header(default=None),
):
    _auth(authorization)
    try:
        if window != "day":
            raise InvalidQueryParameter("Invalid value for 'window': MPI supports only 'day'")
        history, window = _query_history(window, from_, to, limit, format_)
        candles = aggregate_candles(history, window, lambda item: item.mpi)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_mpi(candles, window)
