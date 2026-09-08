from fastapi import APIRouter, Header, HTTPException, Query
from threading import Lock
from fastapi.responses import JSONResponse

from app.core.history import InvalidQueryParameter, filter_by_time_range, validate_allowed_value
from app.core.series import Candle, aggregate_candles, coinglass_ms_to_seconds, interval_seconds
from app.core.simulation_engine import engine
from app.settings import EMULATOR_RECORD_COUNT, LIQUIDATIONS_HISTORY_SAMPLES_PER_PERIOD
from app.providers.coinglass.authentication import is_valid_coinglass_key
from app.providers.coinglass.serializers import (
    serialize_aggregated_cvd,
    serialize_aggregated_liquidation_history,
    serialize_error,
    serialize_etf_flows,
    serialize_etf_flows_history,
    serialize_etf_list,
    serialize_footprint,
    serialize_large_limit_orders,
    serialize_liquidation_map,
    serialize_liquidation_orders,
    serialize_long_short_ratio,
    serialize_ohlc,
    serialize_orderbook_heatmap,
)

router = APIRouter(tags=["CoinGlass"])

ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
ALLOWED_COIN_SYMBOLS = {"BTC", "ETH"}
ALLOWED_EXCHANGES = {"Binance", "OKX", "Bybit"}
ALLOWED_LIQUIDATION_MAP_EXCHANGES = ALLOWED_EXCHANGES | {"Hyperliquid"}
ALLOWED_INTERVALS = {"1m", "5m", "15m", "4h"}
CVD_ALLOWED_INTERVALS = {"5m", "15m", "4h"}
OI_ALLOWED_INTERVALS = {"5m", "15m", "4h"}
ALLOWED_MAP_RANGES = {"1d", "7d", "30d", "180d", "365d"}
EXCHANGE_WEIGHTS = {"Binance": 1.0, "OKX": 0.72, "Bybit": 0.64}

# Prices must behave like an exchange chart: closed candles are immutable and
# only the current live bucket may change between requests.
_PRICE_CANDLE_CACHE: dict[str, tuple[int, list[Candle]]] = {}
_PRICE_CANDLE_CACHE_LOCK = Lock()

# Footprint is polled by Liquidity's five-second table lane. Building 500
# historical candles for both Spot and Perpetual on every poll is unnecessary:
# closed buckets are immutable, while only the live bucket accumulates states.
_FOOTPRINT_CANDLE_CACHE: dict[str, tuple[int, list[Candle]]] = {}
_FOOTPRINT_CANDLE_CACHE_LOCK = Lock()

# CVD uses the same chart semantics as Prices: historical buckets are immutable
# and only the live bucket may be replaced until rollover.  Cache unscaled
# provider records so arbitrary exchange_list scale factors can be applied at
# serialization time without rebuilding the 500-point past.
_CVD_RECORD_CACHE: dict[tuple[bool, str], tuple[int, list[dict[str, float | int]]]] = {}
_CVD_RECORD_CACHE_LOCK = Lock()

# OI/Funding also use replace-live/append-new semantics.  This prevents a
# manual OI RELOAD from re-anchoring and rewriting the prior 499 closed bars.
_METRIC_CANDLE_CACHE: dict[tuple[str, str], tuple[int, list[Candle]]] = {}
_METRIC_CANDLE_CACHE_LOCK = Lock()

def _build_metric_candles(
    metric: str,
    interval: str,
    end_timestamp: int | None = None,
    *,
    anchor_state=None,
) -> list[Candle]:
    history = _history(
        None,
        end_timestamp,
        EMULATOR_RECORD_COUNT,
        interval,
        anchor_state=anchor_state,
    )
    getter = (lambda item: item.open_interest) if metric == "oi" else (lambda item: item.funding_rate)
    return aggregate_candles(history, interval, getter)[-EMULATOR_RECORD_COUNT:]

def _metric_candles(metric: str, interval: str, end_timestamp: int | None = None) -> list[Candle]:
    state = engine.get_state()
    requested_end = coinglass_ms_to_seconds(end_timestamp)
    if requested_end is not None and requested_end < state.timestamp - 1:
        return _build_metric_candles(metric, interval, requested_end)
    seconds = interval_seconds(interval)
    current_bucket = (state.timestamp // seconds) * seconds
    key = (metric, interval)
    current_value = float(state.open_interest if metric == "oi" else state.funding_rate)
    with _METRIC_CANDLE_CACHE_LOCK:
        cached_entry = _METRIC_CANDLE_CACHE.get(key)
        if cached_entry is None:
            candles = _build_metric_candles(
                metric, interval, state.timestamp, anchor_state=state
            )
            _METRIC_CANDLE_CACHE[key] = (state.timestamp, candles)
            return list(candles)
        last_seen, cached = cached_entry
        if not cached or state.timestamp < last_seen:
            candles = _build_metric_candles(
                metric, interval, state.timestamp, anchor_state=state
            )
            _METRIC_CANDLE_CACHE[key] = (state.timestamp, candles)
            return list(candles)
        last = cached[-1]
        if current_bucket == last.timestamp:
            live = Candle(timestamp=last.timestamp, open=last.open, high=max(last.high, current_value), low=min(last.low, current_value), close=current_value, volume=0.0, states=(state,))
            candles = [*cached[:-1], live]
        elif current_bucket > last.timestamp:
            generated = _build_metric_candles(
                metric, interval, state.timestamp, anchor_state=state
            )
            new_rows = [row for row in generated if row.timestamp > last.timestamp]
            candles = list(cached)
            previous_close = last.close
            for row in new_rows:
                delta = row.close - row.open
                close = previous_close + delta
                high_offset = max(row.high - row.open, delta, 0.0)
                low_offset = min(row.low - row.open, delta, 0.0)
                adjusted = Candle(timestamp=row.timestamp, open=previous_close, high=previous_close + high_offset, low=previous_close + low_offset, close=close, volume=0.0, states=row.states)
                candles.append(adjusted)
                previous_close = close
            if not new_rows:
                candles.append(Candle(timestamp=current_bucket, open=previous_close, high=max(previous_close,current_value), low=min(previous_close,current_value), close=current_value, volume=0.0, states=(state,)))
            candles = candles[-EMULATOR_RECORD_COUNT:]
        else:
            candles = _build_metric_candles(
                metric, interval, state.timestamp, anchor_state=state
            )
        _METRIC_CANDLE_CACHE[key] = (state.timestamp, candles)
        return list(candles)

def _build_cvd_records(
    interval: str,
    *,
    futures: bool,
    end_timestamp: int | None = None,
    anchor_state=None,
) -> list[dict[str, float | int]]:
    history = _history(
        None,
        end_timestamp,
        EMULATOR_RECORD_COUNT,
        interval,
        anchor_state=anchor_state,
    )
    seconds = interval_seconds(interval)
    buckets: dict[int, list] = {}
    for state in history:
        bucket = (int(state.timestamp) // seconds) * seconds
        buckets.setdefault(bucket, []).append(state)
    rows: list[dict[str, float | int]] = []
    for bucket in sorted(buckets):
        states = buckets[bucket]
        if futures:
            buy = sum(float(s.futures_taker_buy_volume_usd) for s in states)
            sell = sum(float(s.futures_taker_sell_volume_usd) for s in states)
            cvd = float(states[-1].futures_cumulative_volume_delta_usd)
        else:
            buy = sum(float(s.taker_buy_volume_usd) for s in states)
            sell = sum(float(s.taker_sell_volume_usd) for s in states)
            cvd = float(states[-1].cumulative_volume_delta_usd)
        rows.append({"timestamp": bucket, "buy": buy, "sell": sell, "cvd": cvd})
    return rows[-EMULATOR_RECORD_COUNT:]

def _cvd_records(interval: str, *, futures: bool, end_timestamp: int | None = None) -> list[dict[str, float | int]]:
    state = engine.get_state()
    requested_end = coinglass_ms_to_seconds(end_timestamp)
    if requested_end is not None and requested_end < state.timestamp - 1:
        return _build_cvd_records(interval, futures=futures, end_timestamp=requested_end)
    seconds = interval_seconds(interval)
    current_bucket = (state.timestamp // seconds) * seconds
    key = (bool(futures), interval)
    with _CVD_RECORD_CACHE_LOCK:
        cached_entry = _CVD_RECORD_CACHE.get(key)
        if cached_entry is None:
            rows = _build_cvd_records(
                interval,
                futures=futures,
                end_timestamp=state.timestamp,
                anchor_state=state,
            )
            _CVD_RECORD_CACHE[key] = (state.timestamp, rows)
            return [dict(r) for r in rows]
        last_seen, cached = cached_entry
        if not cached or state.timestamp < last_seen:
            rows = _build_cvd_records(
                interval,
                futures=futures,
                end_timestamp=state.timestamp,
                anchor_state=state,
            )
            _CVD_RECORD_CACHE[key] = (state.timestamp, rows)
            return [dict(r) for r in rows]
        rows = [dict(r) for r in cached]
        elapsed = max(1, state.timestamp - last_seen)
        buy_now = float(state.futures_taker_buy_volume_usd if futures else state.taker_buy_volume_usd) * elapsed
        sell_now = float(state.futures_taker_sell_volume_usd if futures else state.taker_sell_volume_usd) * elapsed
        cvd_now = float(state.futures_cumulative_volume_delta_usd if futures else state.cumulative_volume_delta_usd)
        if current_bucket == int(rows[-1]["timestamp"]):
            rows[-1]["buy"] = float(rows[-1]["buy"]) + buy_now
            rows[-1]["sell"] = float(rows[-1]["sell"]) + sell_now
            rows[-1]["cvd"] = cvd_now
        elif current_bucket > int(rows[-1]["timestamp"]):
            generated = _build_cvd_records(
                interval,
                futures=futures,
                end_timestamp=state.timestamp,
                anchor_state=state,
            )
            new_rows = [dict(r) for r in generated if int(r["timestamp"]) > int(rows[-1]["timestamp"])]
            previous_cvd = float(rows[-1]["cvd"])
            for row in new_rows:
                # Preserve the newly generated bucket delta while anchoring it
                # to the immutable cached cumulative path.
                delta = float(row["buy"]) - float(row["sell"])
                previous_cvd += delta
                row["cvd"] = previous_cvd
                rows.append(row)
            if not new_rows:
                rows.append({"timestamp": current_bucket, "buy": buy_now, "sell": sell_now, "cvd": cvd_now})
            rows = rows[-EMULATOR_RECORD_COUNT:]
        _CVD_RECORD_CACHE[key] = (state.timestamp, rows)
        return [dict(r) for r in rows]

def _serialize_cvd_records(rows: list[dict[str, float | int]], *, scale: float) -> dict:
    data = [
        {
            "time": int(row["timestamp"]) * 1000,
            "agg_taker_buy_vol": format(float(row["buy"]) * scale, ".12g"),
            "agg_taker_sell_vol": format(float(row["sell"]) * scale, ".12g"),
            "cum_vol_delta": format(float(row["cvd"]) * scale, ".12g"),
        }
        for row in rows
    ]
    return {"code": "0", "msg": "success", "data": data}


def _build_price_candles(
    interval: str,
    end_timestamp: int | None = None,
    *,
    anchor_state=None,
) -> list[Candle]:
    history = _history(
        None,
        end_timestamp,
        EMULATOR_RECORD_COUNT,
        interval,
        anchor_state=anchor_state,
    )
    return aggregate_candles(
        history, interval, lambda item: item.btc_price,
        lambda item: item.volume * item.btc_price,
    )[-EMULATOR_RECORD_COUNT:]


def _price_candles(interval: str, end_timestamp: int | None = None) -> list[Candle]:
    """Persistent 500-candle Prices window with replace-live/append-new semantics."""
    state = engine.get_state()
    requested_end = coinglass_ms_to_seconds(end_timestamp)
    if requested_end is not None and requested_end < state.timestamp - 1:
        return _build_price_candles(interval, requested_end)

    seconds = interval_seconds(interval)
    current_bucket = (state.timestamp // seconds) * seconds
    with _PRICE_CANDLE_CACHE_LOCK:
        cached_entry = _PRICE_CANDLE_CACHE.get(interval)
        if cached_entry is None:
            candles = _build_price_candles(
                interval, state.timestamp, anchor_state=state
            )
            _PRICE_CANDLE_CACHE[interval] = (state.timestamp, candles)
            return list(candles)

        last_seen_timestamp, cached = cached_entry
        if not cached or state.timestamp < last_seen_timestamp:
            candles = _build_price_candles(
                interval, state.timestamp, anchor_state=state
            )
            _PRICE_CANDLE_CACHE[interval] = (state.timestamp, candles)
            return list(candles)

        last = cached[-1]
        if current_bucket == last.timestamp:
            elapsed = max(0, state.timestamp - last_seen_timestamp)
            extra_volume = state.volume * state.btc_price * elapsed if elapsed > 0 else 0.0
            live = Candle(
                timestamp=last.timestamp, open=last.open,
                high=max(last.high, state.btc_price), low=min(last.low, state.btc_price),
                close=state.btc_price, volume=last.volume + extra_volume, states=(state,),
            )
            candles = [*cached[:-1], live]
        elif current_bucket > last.timestamp:
            generated = _build_price_candles(
                interval, state.timestamp, anchor_state=state
            )
            new_rows = [row for row in generated if row.timestamp > last.timestamp]
            candles = list(cached)
            previous_close = last.close
            for row in new_rows:
                adjusted = Candle(
                    timestamp=row.timestamp, open=previous_close,
                    high=max(row.high, row.close, previous_close),
                    low=min(row.low, row.close, previous_close),
                    close=row.close, volume=row.volume, states=row.states,
                )
                candles.append(adjusted)
                previous_close = adjusted.close
            if not new_rows:
                candles.append(Candle(
                    timestamp=current_bucket, open=previous_close,
                    high=max(previous_close, state.btc_price), low=min(previous_close, state.btc_price),
                    close=state.btc_price, volume=state.volume * state.btc_price, states=(state,),
                ))
            candles = candles[-EMULATOR_RECORD_COUNT:]
        else:
            candles = _build_price_candles(
                interval, state.timestamp, anchor_state=state
            )

        _PRICE_CANDLE_CACHE[interval] = (state.timestamp, candles)
        return list(candles)


def _footprint_candles(interval: str) -> list[Candle]:
    state = engine.get_state()
    seconds = interval_seconds(interval)
    current_bucket = (state.timestamp // seconds) * seconds
    with _FOOTPRINT_CANDLE_CACHE_LOCK:
        cached_entry = _FOOTPRINT_CANDLE_CACHE.get(interval)
        if cached_entry is None or state.timestamp < cached_entry[0]:
            history = _history(
                None,
                state.timestamp,
                EMULATOR_RECORD_COUNT,
                interval,
                anchor_state=state,
            )
            candles = aggregate_candles(history, interval, lambda item: item.btc_price)[-EMULATOR_RECORD_COUNT:]
        else:
            last_seen, cached = cached_entry
            candles = list(cached)
            if state.timestamp > last_seen:
                last = candles[-1]
                if current_bucket == last.timestamp:
                    candles[-1] = Candle(
                        timestamp=last.timestamp,
                        open=last.open,
                        high=max(last.high, state.btc_price),
                        low=min(last.low, state.btc_price),
                        close=state.btc_price,
                        volume=0.0,
                        states=(*last.states, state),
                    )
                elif current_bucket > last.timestamp:
                    candles.append(Candle(
                        timestamp=current_bucket,
                        open=last.close,
                        high=max(last.close, state.btc_price),
                        low=min(last.close, state.btc_price),
                        close=state.btc_price,
                        volume=0.0,
                        states=(state,),
                    ))
                    candles = candles[-EMULATOR_RECORD_COUNT:]
        _FOOTPRINT_CANDLE_CACHE[interval] = (state.timestamp, candles)
        return list(candles)


def _auth(api_key: str | None) -> None:
    if not is_valid_coinglass_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _history(
    start_time: int | None,
    end_time: int | None,
    limit: int,
    interval: str = "1m",
    *,
    samples_per_period: int = 12,
    anchor_state=None,
) -> list:
    """Return the Emulator's fixed 500-record provider window.

    The real API's limit/start parameters are accepted for compatibility, but
    integration mode deliberately returns a stable 500-point window on every
    historical request so downstream bootstrap/incremental runs never starve.
    """
    _ = (start_time, limit)
    end = coinglass_ms_to_seconds(end_time)
    seconds = interval_seconds(interval)
    anchor_end = end or engine.get_state().timestamp
    return engine.get_synthetic_history(
        interval_seconds=seconds,
        periods=EMULATOR_RECORD_COUNT,
        end_timestamp=anchor_end,
        samples_per_period=samples_per_period,
        anchor_state=anchor_state,
    )


def clear_runtime_caches() -> None:
    """Clear provider live-window caches after an explicit engine reset."""
    with _PRICE_CANDLE_CACHE_LOCK:
        _PRICE_CANDLE_CACHE.clear()
    with _FOOTPRINT_CANDLE_CACHE_LOCK:
        _FOOTPRINT_CANDLE_CACHE.clear()
    with _CVD_RECORD_CACHE_LOCK:
        _CVD_RECORD_CACHE.clear()
    with _METRIC_CANDLE_CACHE_LOCK:
        _METRIC_CANDLE_CACHE.clear()


def _bad_request(exc: InvalidQueryParameter) -> JSONResponse:
    return JSONResponse(status_code=400, content=serialize_error(str(exc), code="40000"))


def _validate_pair(exchange: str, symbol: str, interval: str | None = None) -> None:
    validate_allowed_value(exchange, ALLOWED_EXCHANGES, "exchange")
    validate_allowed_value(symbol, ALLOWED_SYMBOLS, "symbol")
    if interval is not None:
        validate_allowed_value(interval, ALLOWED_INTERVALS, "interval")


@router.get("/api/spot/price/history")
def get_spot_price_history(
    exchange: str = Query(default="Binance"),
    symbol: str = Query(default="BTCUSDT"),
    interval: str = Query(default="1m"),
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    _auth(cg_api_key)
    try:
        _validate_pair(exchange, symbol, interval)
        _ = (start_time, limit)
        candles = _price_candles(interval, end_time)
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_ohlc(candles, strings=False, include_volume=True)


def _cvd_history(exchange_list: str, symbol: str, interval: str, limit: int, unit: str, start_time: int | None, end_time: int | None, api_key: str | None, *, futures: bool):
    _auth(api_key)
    try:
        exchanges = [item.strip() for item in exchange_list.split(",") if item.strip()]
        if not exchanges:
            raise InvalidQueryParameter("exchange_list must contain at least one exchange")
        for exchange in exchanges:
            validate_allowed_value(exchange, ALLOWED_EXCHANGES, "exchange_list")
        validate_allowed_value(symbol, ALLOWED_COIN_SYMBOLS, "symbol")
        validate_allowed_value(interval, CVD_ALLOWED_INTERVALS, "interval")
        validate_allowed_value(unit, {"usd", "coin"}, "unit")
        _ = (start_time, limit)
        rows = _cvd_records(interval, futures=futures, end_timestamp=end_time)
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    scale = sum(EXCHANGE_WEIGHTS[item] for item in exchanges)
    if unit == "coin":
        scale /= max(engine.get_state().btc_price, 1.0)
    return _serialize_cvd_records(rows, scale=scale)


@router.get("/api/spot/aggregated-cvd/history")
def get_spot_aggregated_cvd(
    exchange_list: str = Query(default="Binance"),
    symbol: str = Query(default="BTC"),
    interval: str = Query(default="5m"),
    limit: int = Query(default=100, ge=1, le=4500),
    unit: str = Query(default="usd"),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _cvd_history(exchange_list, symbol, interval, limit, unit, start_time, end_time, cg_api_key, futures=False)


@router.get("/api/futures/aggregated-cvd/history")
def get_futures_aggregated_cvd(
    exchange_list: str = Query(default="Binance"),
    symbol: str = Query(default="BTC"),
    interval: str = Query(default="5m"),
    limit: int = Query(default=100, ge=1, le=4500),
    unit: str = Query(default="usd"),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _cvd_history(exchange_list, symbol, interval, limit, unit, start_time, end_time, cg_api_key, futures=True)


def _footprint(exchange: str, symbol: str, interval: str, limit: int, start_time: int | None, end_time: int | None, api_key: str | None, *, futures: bool):
    _auth(api_key)
    try:
        _validate_pair(exchange, symbol, interval)
        if start_time is None and end_time is None:
            candles = _footprint_candles(interval)
        else:
            history = _history(start_time, end_time, limit, interval)
            candles = aggregate_candles(history, interval, lambda item: item.btc_price)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_footprint(candles, scale=EXCHANGE_WEIGHTS[exchange], futures=futures)


@router.get("/api/spot/volume/footprint-history")
def get_spot_footprint(
    exchange: str = Query(default="Binance"),
    symbol: str = Query(default="BTCUSDT"),
    interval: str = Query(default="1m"),
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _footprint(exchange, symbol, interval, limit, start_time, end_time, cg_api_key, futures=False)


@router.get("/api/futures/volume/footprint-history")
def get_futures_footprint(
    exchange: str = Query(default="Binance"),
    symbol: str = Query(default="BTCUSDT"),
    interval: str = Query(default="1m"),
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _footprint(exchange, symbol, interval, limit, start_time, end_time, cg_api_key, futures=True)


def _metric_ohlc(symbol: str, interval: str, limit: int, start_time: int | None, end_time: int | None, api_key: str | None, metric: str):
    _auth(api_key)
    try:
        validate_allowed_value(symbol, ALLOWED_COIN_SYMBOLS, "symbol")
        validate_allowed_value(interval, OI_ALLOWED_INTERVALS, "interval")
        _ = (start_time, limit)
        candles = _metric_candles(metric, interval, end_time)
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_ohlc(candles, strings=True, include_volume=False)


@router.get("/api/futures/open-interest/aggregated-history")
def get_aggregated_open_interest(
    symbol: str = Query(default="BTC"),
    interval: str = Query(default="5m"),
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _metric_ohlc(symbol, interval, limit, start_time, end_time, cg_api_key, "oi")


@router.get("/api/futures/funding-rate/oi-weight-history")
def get_oi_weighted_funding_rate(
    symbol: str = Query(default="BTC"),
    interval: str = Query(default="5m"),
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _metric_ohlc(symbol, interval, limit, start_time, end_time, cg_api_key, "funding")


@router.get("/api/etf/bitcoin/flow-history")
def get_bitcoin_etf_flows(
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    _auth(cg_api_key)
    history = engine.get_synthetic_history(
        interval_seconds=86_400,
        periods=EMULATOR_RECORD_COUNT,
        end_timestamp=engine.get_state().timestamp,
        samples_per_period=1,
    )
    return serialize_etf_flows_history(history)


@router.get("/api/etf/bitcoin/list")
def get_bitcoin_etf_list(
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    _auth(cg_api_key)
    return serialize_etf_list(engine.get_state())


@router.get("/api/futures/liquidation/aggregated-history")
def get_aggregated_liquidation_history(
    exchange_list: str = Query(default="Binance"),
    symbol: str = Query(default="BTC"),
    interval: str = Query(default="4h"),
    limit: int = Query(default=1000, ge=1, le=1000),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    _auth(cg_api_key)
    try:
        exchanges = [item.strip() for item in exchange_list.split(",") if item.strip()]
        if not exchanges:
            raise InvalidQueryParameter("exchange_list must contain at least one exchange")
        for exchange in exchanges:
            validate_allowed_value(exchange, ALLOWED_EXCHANGES, "exchange_list")
        validate_allowed_value(symbol, ALLOWED_COIN_SYMBOLS, "symbol")
        validate_allowed_value(interval, ALLOWED_INTERVALS, "interval")
        history = _history(
            start_time, end_time, limit, interval,
            samples_per_period=LIQUIDATIONS_HISTORY_SAMPLES_PER_PERIOD,
        )
        candles = aggregate_candles(history, interval, lambda item: item.long_liquidations + item.short_liquidations)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_aggregated_liquidation_history(candles, scale=sum(EXCHANGE_WEIGHTS[item] for item in exchanges))


@router.get("/api/futures/liquidation/order")
def get_liquidation_order_events(
    exchange: str = Query(default="Binance"),
    symbol: str = Query(default="BTC"),
    min_liquidation_amount: float = Query(default=10_000, ge=0),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    _auth(cg_api_key)
    try:
        validate_allowed_value(exchange, ALLOWED_EXCHANGES, "exchange")
        if symbol not in ALLOWED_COIN_SYMBOLS | ALLOWED_SYMBOLS:
            raise InvalidQueryParameter(f"Invalid value for 'symbol': {symbol}")
        end = coinglass_ms_to_seconds(end_time) or engine.get_state().timestamp
        start = coinglass_ms_to_seconds(start_time) or (end - 86400)
        span = max(250, end - start)
        step = max(1, span // 249)
        history = engine.get_synthetic_history(
            interval_seconds=step, periods=250, end_timestamp=end, samples_per_period=1,
        )
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_liquidation_orders(history, exchange, symbol, min_liquidation_amount)


def _liquidation_map(symbol: str, range_: str, api_key: str | None, *, exchange: str | None = None):
    _auth(api_key)
    try:
        validate_allowed_value(range_, ALLOWED_MAP_RANGES, "range")
        if exchange is None:
            validate_allowed_value(symbol, ALLOWED_COIN_SYMBOLS, "symbol")
        else:
            validate_allowed_value(exchange, ALLOWED_LIQUIDATION_MAP_EXCHANGES, "exchange")
            validate_allowed_value(symbol, ALLOWED_SYMBOLS, "symbol")
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_liquidation_map(engine.get_state(), pair=exchange is not None, exchange=exchange, range_=range_)


@router.get("/api/futures/liquidation/aggregated-map")
def get_aggregated_liquidation_map(
    symbol: str = Query(default="BTC"),
    range_: str = Query(default="7d", alias="range"),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _liquidation_map(symbol, range_, cg_api_key)


@router.get("/api/futures/liquidation/map")
def get_pair_liquidation_map(
    exchange: str = Query(default="Binance"),
    symbol: str = Query(default="BTCUSDT"),
    range_: str = Query(default="7d", alias="range"),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _liquidation_map(symbol, range_, cg_api_key, exchange=exchange)


def _long_short_history(kind: str, exchange: str, symbol: str, interval: str, limit: int, start_time: int | None, end_time: int | None, api_key: str | None):
    _auth(api_key)
    try:
        _validate_pair(exchange, symbol, interval)
        history = _history(
            start_time, end_time, limit, interval,
            samples_per_period=LIQUIDATIONS_HISTORY_SAMPLES_PER_PERIOD,
        )
        candles = aggregate_candles(history, interval, lambda item: item.btc_price)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_long_short_ratio(candles, kind, exchange_scale=EXCHANGE_WEIGHTS[exchange])


@router.get("/api/futures/top-long-short-position-ratio/history")
def get_top_position_long_short_ratio(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="4h"),
    limit: int = Query(default=1000, ge=1, le=1000), start_time: int | None = Query(default=None), end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _long_short_history("top_position", exchange, symbol, interval, limit, start_time, end_time, cg_api_key)


@router.get("/api/futures/top-long-short-account-ratio/history")
def get_top_account_long_short_ratio(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="4h"),
    limit: int = Query(default=1000, ge=1, le=1000), start_time: int | None = Query(default=None), end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _long_short_history("top_account", exchange, symbol, interval, limit, start_time, end_time, cg_api_key)


@router.get("/api/futures/global-long-short-account-ratio/history")
def get_global_account_long_short_ratio(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="4h"),
    limit: int = Query(default=1000, ge=1, le=1000), start_time: int | None = Query(default=None), end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _long_short_history("global_account", exchange, symbol, interval, limit, start_time, end_time, cg_api_key)


def _orderbook_heatmap(exchange: str, symbol: str, interval: str, limit: int, start_time: int | None, end_time: int | None, api_key: str | None, *, futures: bool):
    _auth(api_key)
    try:
        _validate_pair(exchange, symbol, interval)
        history = _history(start_time, end_time, limit, interval)
        candles = aggregate_candles(history, interval, lambda item: item.btc_price)[-EMULATOR_RECORD_COUNT:]
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_orderbook_heatmap(candles, scale=EXCHANGE_WEIGHTS[exchange], futures=futures)


@router.get("/api/spot/orderbook/history")
def get_spot_orderbook_heatmap(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="4h"),
    limit: int = Query(default=100, ge=1, le=1000), start_time: int | None = Query(default=None), end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _orderbook_heatmap(exchange, symbol, interval, limit, start_time, end_time, cg_api_key, futures=False)


@router.get("/api/futures/orderbook/history")
def get_perpetual_orderbook_heatmap(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="4h"),
    limit: int = Query(default=100, ge=1, le=1000), start_time: int | None = Query(default=None), end_time: int | None = Query(default=None),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _orderbook_heatmap(exchange, symbol, interval, limit, start_time, end_time, cg_api_key, futures=True)


def _large_limit_orders(exchange: str, symbol: str, api_key: str | None, *, futures: bool):
    _auth(api_key)
    try:
        _validate_pair(exchange, symbol)
    except InvalidQueryParameter as exc:
        return _bad_request(exc)
    return serialize_large_limit_orders(engine.get_state(), exchange, symbol, futures=futures, scale=EXCHANGE_WEIGHTS[exchange])


@router.get("/api/spot/orderbook/large-limit-order")
def get_spot_large_limit_orders(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _large_limit_orders(exchange, symbol, cg_api_key, futures=False)


@router.get("/api/futures/orderbook/large-limit-order")
def get_perpetual_large_limit_orders(
    exchange: str = Query(default="Binance"), symbol: str = Query(default="BTCUSDT"),
    cg_api_key: str | None = Header(default=None, alias="CG-API-KEY"),
):
    return _large_limit_orders(exchange, symbol, cg_api_key, futures=True)
