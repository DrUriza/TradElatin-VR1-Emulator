"""Deterministic CoinGlass extraction plan for Liquidity Microstructure Input v0.1."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import time
from typing import Any

LIQUIDITY_MICROSTRUCTURE_FAMILY = "liquidity_microstructure"
PROVIDER                         = "coinglass"
REST_BASE_URL                    = "https://open-api-v4.coinglass.com"
TIMEFRAMES                       = ("1m", "5m", "15m", "1h")
TIMEFRAME_SECONDS                 = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
DEPTH_RANGES_PERCENT             = (1, 5, 10)
VALID_MODES                      = {"bootstrap", "incremental", "recovery"}

ENDPOINT_MANIFEST = {
    "spot_orderbook_heatmap": {"transport": "rest", "path": "/api/spot/orderbook/history"},
    "perpetual_orderbook_heatmap": {"transport": "rest", "path": "/api/futures/orderbook/history"},
    "spot_order_depth": {"transport": "rest", "path": "/api/spot/orderbook/ask-bids-history"},
    "perpetual_order_depth": {"transport": "rest", "path": "/api/futures/orderbook/ask-bids-history"},
    "spot_footprint": {"transport": "rest", "path": "/api/spot/volume/footprint-history"},
    "perpetual_footprint": {"transport": "rest", "path": "/api/futures/volume/footprint-history"},
    "spot_large_limit_orders": {"transport": "rest", "path": "/api/spot/orderbook/large-limit-order"},
    "perpetual_large_limit_orders": {"transport": "rest", "path": "/api/futures/orderbook/large-limit-order"},
    "whale_index": {"transport": "rest", "path": "/api/futures/whale-index/history"},
}

RawFetcher = Callable[..., Any]


def _request_id(request: Mapping[str, Any]) -> str:
    identity = {key: request.get(key) for key in ("provider", "transport", "endpoint_id", "path", "channel", "params", "dimensions")}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _request(endpoint_id: str, *, params: Mapping[str, Any], dimensions: Mapping[str, Any],
             collection_started_at: int | None = None, collection_ended_at: int | None = None) -> dict[str, Any]:
    manifest = ENDPOINT_MANIFEST[endpoint_id]
    request = {"provider": PROVIDER, "transport": manifest["transport"], "endpoint_id": endpoint_id,
               "path": manifest.get("path"), "channel": None, "params": dict(params), "dimensions": dict(dimensions)}
    if manifest["transport"] == "websocket":
        request["channel"] = manifest["channel_template"].format(exchange=dimensions["exchange"], symbol=dimensions["symbol"],
                                                                  min_volume_usd=params["min_volume_usd"])
        request["collection_started_at"] = collection_started_at
        request["collection_ended_at"] = collection_ended_at
    request["request_id"] = _request_id(request)
    return request


def build_liquidity_microstructure_fetch_plan(*, mode: str = "bootstrap", reference_timestamp: int | None = None,
                                               asset: str = "BTC", exchange: str = "Binance", spot_symbol: str = "BTCUSDT",
                                               perpetual_symbol: str = "BTCUSDT", timeframes: Sequence[str] = TIMEFRAMES,
                                               depth_ranges_percent: Sequence[int] = DEPTH_RANGES_PERCENT,
                                               history_limit: int = 100,
                                               hourly_history_limit: int | None = None,
                                               overlap_seconds: int = 300,
                                               recovery_requests: Sequence[str | Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError("invalid_liquidity_microstructure_mode")
    reference = int(reference_timestamp or time.time())

    def limit_for(timeframe: str) -> int:
        return int(hourly_history_limit if timeframe == "1h" and hourly_history_limit is not None else history_limit)

    def start_for(timeframe: str) -> int:
        if mode == "incremental":
            return reference - overlap_seconds
        seconds = TIMEFRAME_SECONDS.get(timeframe)
        if seconds is None:
            raise ValueError("invalid_timeframe")
        # ``history_limit`` is a record count.  Bootstrap/recovery therefore
        # requests enough wall-clock history to make that count possible at
        # each provider interval instead of using the old fixed 24h window.
        return reference - max(seconds * limit_for(timeframe), seconds)

    common = {"asset": asset, "exchange": exchange}
    plan: list[dict[str, Any]] = []
    for market_type, symbol, heatmap_id, depth_id in (
        ("spot", spot_symbol, "spot_orderbook_heatmap", "spot_order_depth"),
        ("perpetual", perpetual_symbol, "perpetual_orderbook_heatmap", "perpetual_order_depth"),
    ):
        for timeframe in timeframes:
            dimensions = {**common, "market_type": market_type, "symbol": symbol, "timeframe": timeframe, "range_percent": None}
            start = start_for(timeframe)
            params = {"exchange": exchange, "symbol": symbol, "interval": timeframe, "limit": limit_for(timeframe),
                      "start_time": start * 1000, "end_time": reference * 1000}
            plan.append(_request(heatmap_id, params=params, dimensions=dimensions))
            for range_percent in depth_ranges_percent:
                depth_dimensions = {**dimensions, "range_percent": int(range_percent)}
                plan.append(_request(depth_id, params={**params, "range": int(range_percent)}, dimensions=depth_dimensions))
    # Executed liquidity comes from CoinGlass footprint history.  The HMI keeps
    # the historical ``large_trades`` contract name, but the provider semantic
    # is an executed buy/sell price-bin feed, not an individual tape.
    for market_type, symbol, endpoint_id in (("spot", spot_symbol, "spot_footprint"),
                                              ("perpetual", perpetual_symbol, "perpetual_footprint")):
        dimensions = {**common, "market_type": market_type, "symbol": symbol, "timeframe": "1m", "range_percent": None}
        footprint_start = start_for("1m")
        plan.append(_request(endpoint_id, params={"exchange": exchange, "symbol": symbol, "interval": "1m",
                                                  "limit": min(int(history_limit), 1000),
                                                  "start_time": footprint_start * 1000, "end_time": reference * 1000},
                             dimensions=dimensions))

    # Whale order liquidity is sourced directly from CoinGlass Large Orderbook
    # instead of inferring "whales" from ordinary book levels.
    for market_type, symbol, endpoint_id in (("spot", spot_symbol, "spot_large_limit_orders"),
                                              ("perpetual", perpetual_symbol, "perpetual_large_limit_orders")):
        dimensions = {**common, "market_type": market_type, "symbol": symbol, "timeframe": "1m", "range_percent": None}
        plan.append(_request(endpoint_id, params={"exchange": exchange, "symbol": symbol}, dimensions=dimensions))

    # Whale Index remains useful as a temporal context series; it is no longer
    # used as a substitute for order-level whale liquidity.
    for timeframe in timeframes:
        dimensions = {**common, "market_type": "perpetual", "symbol": perpetual_symbol, "timeframe": timeframe, "range_percent": None}
        start = start_for(timeframe)
        params = {"exchange": exchange, "symbol": perpetual_symbol, "interval": timeframe, "limit": limit_for(timeframe),
                  "start_time": start * 1000, "end_time": reference * 1000}
        plan.append(_request("whale_index", params=params, dimensions=dimensions))
    if mode != "recovery":
        return plan
    requested = list(recovery_requests or [])
    if not requested:
        raise ValueError("recovery_requests_required")
    selected = []
    for request in plan:
        for target in requested:
            if isinstance(target, str) and target in {request["request_id"], request["endpoint_id"]}:
                selected.append(request)
                break
            if isinstance(target, Mapping) and all(request.get("dimensions", {}).get(key) == value for key, value in target.items()):
                selected.append(request)
                break
    return selected


def execute_liquidity_microstructure_raw_request(request: Mapping[str, Any], fetcher: RawFetcher) -> dict[str, Any]:
    result = {**deepcopy(dict(request)), "status": "ok", "response": None, "error": None, "warnings": []}
    try:
        response = fetcher(**deepcopy(dict(request)))
        result["response"] = response
    except Exception as exc:
        result["status"] = "error"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def extract_liquidity_microstructure_raw(*, fetcher: RawFetcher, mode: str = "bootstrap", **plan_arguments: Any) -> dict[str, Any]:
    plan = build_liquidity_microstructure_fetch_plan(mode=mode, **plan_arguments)
    return {"family": LIQUIDITY_MICROSTRUCTURE_FAMILY, "stage": "raw_extract", "mode": mode,
            "requests": [execute_liquidity_microstructure_raw_request(request, fetcher) for request in plan]}


class LiquidityMicrostructureRawExtractor:
    def __init__(self, fetcher: RawFetcher) -> None:
        self.fetcher = fetcher

    def build_plan(self, **kwargs: Any) -> list[dict[str, Any]]:
        return build_liquidity_microstructure_fetch_plan(**kwargs)

    def extract(self, **kwargs: Any) -> dict[str, Any]:
        return extract_liquidity_microstructure_raw(fetcher=self.fetcher, **kwargs)
