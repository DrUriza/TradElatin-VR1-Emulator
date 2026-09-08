import time

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.history import InvalidQueryParameter, filter_by_time_range, validate_allowed_value
from app.core.series import aggregate_candles, interval_seconds
from app.core.simulation_engine import engine
from app.settings import EMULATOR_RECORD_COUNT
from app.providers.glassnode.authentication import is_valid_glassnode_key
from app.providers.glassnode.serializers import serialize_error, serialize_metric, serialize_ohlc

router = APIRouter(tags=["Glassnode"])

ALLOWED_ASSETS = {"BTC", "ETH"}
ALLOWED_INTERVALS = {"10m", "1h", "24h", "1w", "1month"}
ALLOWED_CURRENCIES = {"NATIVE", "USD", "native", "usd"}


def _auth(api_key: str | None) -> None:
    if not is_valid_glassnode_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _history(a: str, i: str, s: int | None, u: int | None, f: str, timestamp_format: str) -> list:
    validate_allowed_value(a, ALLOWED_ASSETS, "a")
    validate_allowed_value(i, ALLOWED_INTERVALS, "i")
    validate_allowed_value(f, {"json"}, "f")
    validate_allowed_value(timestamp_format, {"unix", "humanized"}, "timestamp_format")
    _ = s
    end_timestamp = int(u or engine.get_state().timestamp)
    seconds = interval_seconds(i)
    return engine.get_synthetic_history(
        interval_seconds=seconds,
        periods=EMULATOR_RECORD_COUNT,
        end_timestamp=end_timestamp,
        samples_per_period=4,
    )


def _bad_request(exc: InvalidQueryParameter) -> JSONResponse:
    return JSONResponse(status_code=400, content=serialize_error(str(exc)))


def _scalar_metric(a: str, i: str, s: int | None, u: int | None, f: str, timestamp_format: str, api_key: str | None, getter, *, c: str | None = None):
    _auth(api_key)
    try:
        if c is not None:
            validate_allowed_value(c, ALLOWED_CURRENCIES, "c")
        history = _history(a, i, s, u, f, timestamp_format)
        candles = aggregate_candles(history, i, getter)
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return candles


@router.get("/v1/metrics/market/marketcap_usd")
def get_marketcap_usd(
    a: str = Query(default="BTC"), i: str = Query(default="1h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.btc_price)
    if isinstance(candles, JSONResponse):
        return candles
    synthetic_supply = 19_800_000.0
    return serialize_metric(candles, lambda candle: candle.close * synthetic_supply, timestamp_format=timestamp_format)


@router.get("/v1/metrics/derivatives/futures_estimated_leverage_ratio")
def get_futures_estimated_leverage_ratio(
    a: str = Query(default="BTC"), i: str = Query(default="1h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.open_interest)
    if isinstance(candles, JSONResponse):
        return candles

    def leverage(candle):
        state = candle.states[-1]
        reserve_usd = state.exchange_reserve * state.btc_price
        return state.open_interest / reserve_usd if reserve_usd else 0.0

    return serialize_metric(candles, leverage, timestamp_format=timestamp_format)


@router.get("/v1/metrics/derivatives/dvol_ohlc")
def get_dvol_ohlc(
    a: str = Query(default="BTC"), i: str = Query(default="1h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.dvol)
    if isinstance(candles, JSONResponse):
        return candles
    return serialize_ohlc(candles, timestamp_format=timestamp_format)


@router.get("/v1/metrics/distribution/balance_miners_sum")
def get_balance_miners_sum(
    a: str = Query(default="BTC"), i: str = Query(default="24h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), c: str = Query(default="NATIVE"), miner: str | None = Query(default=None),
    timestamp_format: str = Query(default="unix"), x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.miner_balance, c=c)
    if isinstance(candles, JSONResponse):
        return candles
    usd = c.upper() == "USD"
    return serialize_metric(candles, lambda candle: candle.close * candle.states[-1].btc_price if usd else candle.close, timestamp_format=timestamp_format)


@router.get("/v1/metrics/indicators/sopr")
def get_sopr(
    a: str = Query(default="BTC"), i: str = Query(default="24h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.sopr)
    if isinstance(candles, JSONResponse):
        return candles
    return serialize_metric(candles, lambda candle: candle.close, timestamp_format=timestamp_format)


@router.get("/v1/metrics/mining/hash_rate_mean")
def get_hash_rate_mean(
    a: str = Query(default="BTC"), i: str = Query(default="1h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.hash_rate)
    if isinstance(candles, JSONResponse):
        return candles
    return serialize_metric(candles, lambda candle: candle.close, timestamp_format=timestamp_format)


@router.get("/v1/metrics/mining/difficulty_latest")
def get_difficulty_latest(
    a: str = Query(default="BTC"), i: str = Query(default="1h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), timestamp_format: str = Query(default="unix"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.difficulty)
    if isinstance(candles, JSONResponse):
        return candles
    return serialize_metric(candles, lambda candle: candle.close, timestamp_format=timestamp_format)


@router.get("/v1/metrics/transactions/transfers_volume_from_miners_sum")
def get_transfers_volume_from_miners_sum(
    a: str = Query(default="BTC"), i: str = Query(default="24h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), c: str = Query(default="NATIVE"), miner: str | None = Query(default=None),
    timestamp_format: str = Query(default="unix"), x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.miner_transfer_volume, c=c)
    if isinstance(candles, JSONResponse):
        return candles
    usd = c.upper() == "USD"
    return serialize_metric(candles, lambda candle: candle.close * candle.states[-1].btc_price if usd else candle.close, timestamp_format=timestamp_format)


@router.get("/v1/metrics/mining/revenue_sum")
def get_revenue_sum(
    a: str = Query(default="BTC"), i: str = Query(default="24h"), s: int | None = Query(default=None), u: int | None = Query(default=None),
    f: str = Query(default="json"), c: str = Query(default="NATIVE"), miner: str | None = Query(default=None),
    timestamp_format: str = Query(default="unix"), x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    candles = _scalar_metric(a, i, s, u, f, timestamp_format, x_api_key, lambda item: item.miner_revenue, c=c)
    if isinstance(candles, JSONResponse):
        return candles
    usd = c.upper() == "USD"
    return serialize_metric(
        candles,
        lambda candle: candle.close if usd else candle.close / max(candle.states[-1].btc_price, 1.0),
        timestamp_format=timestamp_format,
    )
