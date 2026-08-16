"""Raw extraction for the canonical ETF and exchange flows Input family."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

FAMILY = "etf_exchange_flows"
VALID_MODES = {"bootstrap", "incremental", "recovery"}
PROVIDERS = ("coinglass", "cryptoquant", "glassnode")
SUPPORTED_WINDOWS = {"hour", "day"}
SUPPORTED_INTERVALS = {"1h", "24h"}
RECOVERY_ALLOWED_FIELDS = {
    "coinglass": {"provider", "endpoint_id", "start_time", "end_time", "limit", "ticker", "symbol"},
    "cryptoquant": {"provider", "endpoint_id", "window", "start_time", "end_time", "limit", "exchange_scope"},
    "glassnode": {"provider", "endpoint_id", "interval", "start_time", "end_time", "asset"},
}
BOOTSTRAP_LIMITS = {"cryptoquant_hour": 48, "cryptoquant_day": 120}
INCREMENTAL_LIMITS = {"cryptoquant_hour": 48, "cryptoquant_day": 8}
ETF_HISTORICAL_BOOTSTRAP_LIMITS = {
    ("exchange_netflow", "day"): 730,
    ("exchange_reserve", "day"): 730,
    # One extra day allows Processing to discard partial boundary days while
    # retaining 730 complete UTC candles.
    ("exchange_reserve", "hour"): 17_544,
}
ProviderFetcher = Callable[..., Mapping[str, Any] | Sequence[Any]]

ENDPOINT_SPECS = {
    "coinglass": {
        "bitcoin_etf_flows": {"path": "/api/etf/bitcoin/flow-history", "widgets": ["ETF Net Flow KPI", "ETF Flow Daily", "ETF Flow by Provider", "Cumulative ETF Net Flow"]},
        "bitcoin_etf_list": {"path": "/api/etf/bitcoin/list", "widgets": ["fund catalog", "AUM and holdings source"]},
        "bitcoin_etf_net_assets_history": {"path": "/api/etf/bitcoin/net-assets/history", "widgets": ["Total ETF AUM", "net assets history"]},
        "bitcoin_etf_premium_discount_history": {"path": "/api/etf/bitcoin/premium-discount/history", "widgets": ["GBTC Premium/Discount"]},
        "exchange_balance_list": {"path": "/api/exchange/balance/list", "widgets": ["Exchange Balance KPI", "exchange snapshot"]},
        "exchange_balance_chart": {"path": "/api/exchange/balance/chart", "widgets": ["Exchange Balance History"]},
    },
    "cryptoquant": {
        "exchange_inflow": {"path": "/btc/exchange-flows/inflow", "widgets": ["Exchange Inflow 24H"]},
        "exchange_outflow": {"path": "/btc/exchange-flows/outflow", "widgets": ["Exchange Outflow 24H"]},
        "exchange_netflow": {"path": "/btc/exchange-flows/netflow", "widgets": ["Exchange Net Flow Daily"]},
        "exchange_reserve": {"path": "/btc/exchange-flows/reserve", "widgets": ["Exchange Balance secondary source"]},
    },
    "glassnode": {
        "exchange_inflow": {"path": "/v1/metrics/transactions/transfers_volume_to_exchanges_sum", "widgets": ["secondary exchange inflow"]},
        "exchange_outflow": {"path": "/v1/metrics/transactions/transfers_volume_from_exchanges_sum", "widgets": ["secondary exchange outflow"]},
        "exchange_netflow": {"path": "/v1/metrics/transactions/transfers_volume_exchanges_net", "widgets": ["secondary exchange netflow"]},
        "exchange_balance": {"path": "/v1/metrics/distribution/balance_exchanges", "widgets": ["secondary exchange balance"]},
        "us_spot_etf_flows_net": {"path": "/v1/metrics/institutions/us_spot_etf_flows_net", "widgets": ["secondary ETF net flow"]},
    },
}
PRIMARY_ENDPOINT_IDS = tuple((*ENDPOINT_SPECS["coinglass"], *ENDPOINT_SPECS["cryptoquant"]))
SECONDARY_ENDPOINT_IDS = tuple(ENDPOINT_SPECS["glassnode"])


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid_{name}")
    return value


def _validate_recovery_requests(recovery_requests: Sequence[Mapping[str, Any]] | None, *,
                                exchange_scope: str | None, symbol: str) -> list[Mapping[str, Any]]:
    if (not isinstance(recovery_requests, Sequence) or isinstance(recovery_requests, (str, bytes))
            or not recovery_requests):
        raise ValueError("recovery_requests_required")
    validated = []
    for item in recovery_requests:
        if not isinstance(item, Mapping):
            raise ValueError("invalid_recovery_request")
        provider, endpoint = item.get("provider"), item.get("endpoint_id")
        if provider not in ENDPOINT_SPECS or endpoint not in ENDPOINT_SPECS[provider]:
            raise ValueError("invalid_recovery_endpoint")
        unknown = set(item) - RECOVERY_ALLOWED_FIELDS[provider]
        if unknown:
            raise ValueError("unsupported_recovery_field")
        for field in ("start_time", "end_time"):
            if field in item:
                _positive_int(item[field], field)
        if "limit" in item:
            _positive_int(item["limit"], "limit")
        if "start_time" in item and "end_time" in item and item["start_time"] > item["end_time"]:
            raise ValueError("invalid_time_range")
        if provider == "coinglass":
            unsupported = {field for field in ("start_time", "end_time", "limit") if field in item}
            if unsupported:
                raise ValueError("unsupported_coinglass_recovery_field")
            build_coinglass_params(endpoint, symbol=item.get("symbol", symbol), ticker=item.get("ticker", "GBTC"))
        elif provider == "cryptoquant":
            build_cryptoquant_params(exchange_scope=item.get("exchange_scope", exchange_scope), window=item.get("window"),
                limit=item.get("limit", 100), start_time=item.get("start_time"), end_time=item.get("end_time"))
        else:
            build_glassnode_params(interval=item.get("interval"), asset=item.get("asset", symbol),
                start_time=item.get("start_time"), end_time=item.get("end_time"))
        validated.append(item)
    return validated


def _utc_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def build_coinglass_params(endpoint_id: str, *, symbol: str = "BTC", ticker: str = "GBTC") -> dict[str, Any]:
    if endpoint_id not in ENDPOINT_SPECS["coinglass"]:
        raise ValueError("unknown_coinglass_endpoint")
    if endpoint_id in {"exchange_balance_list", "exchange_balance_chart"}:
        return {"symbol": symbol}
    if endpoint_id == "bitcoin_etf_premium_discount_history":
        return {"ticker": ticker}
    return {}


def build_cryptoquant_params(*, exchange_scope: str, window: str, limit: int,
                             start_time: int | None = None, end_time: int | None = None) -> dict[str, Any]:
    if not isinstance(exchange_scope, str) or not exchange_scope:
        raise ValueError("invalid_exchange_scope")
    if window not in SUPPORTED_WINDOWS:
        raise ValueError("invalid_cryptoquant_window")
    params = {"exchange": exchange_scope, "window": window, "limit": _positive_int(limit, "limit"), "format": "json"}
    if start_time is not None:
        params["from"] = datetime.fromtimestamp(_positive_int(start_time, "start_time"), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if end_time is not None:
        params["to"] = datetime.fromtimestamp(_positive_int(end_time, "end_time"), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ValueError("invalid_time_range")
    return params


def build_glassnode_params(*, interval: str, asset: str = "BTC", start_time: int | None = None,
                           end_time: int | None = None) -> dict[str, Any]:
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError("invalid_glassnode_interval")
    params = {"a": asset, "i": interval, "f": "json"}
    if start_time is not None:
        params["s"] = _positive_int(start_time, "start_time")
    if end_time is not None:
        params["u"] = _positive_int(end_time, "end_time")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ValueError("invalid_time_range")
    return params


def _request(provider: str, endpoint_id: str, params: Mapping[str, Any], variant: str | None = None) -> dict[str, Any]:
    return {"provider": provider, "endpoint_id": endpoint_id, "path": ENDPOINT_SPECS[provider][endpoint_id]["path"],
            "params": deepcopy(dict(params)), "variant": variant}


def build_etf_exchange_flows_fetch_plan(*, mode: str, exchange_scope: str | None, symbol: str = "BTC",
                                        include_secondary: bool = False,
                                        recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                        bootstrap_limits: Mapping[str, int] | None = None,
                                        incremental_limits: Mapping[str, int] | None = None) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError("invalid_mode")
    if mode == "recovery":
        recovery_requests = _validate_recovery_requests(recovery_requests, exchange_scope=exchange_scope, symbol=symbol)
        plan = []
        for item in recovery_requests:
            provider, endpoint = item.get("provider"), item.get("endpoint_id")
            start, end, limit = item.get("start_time"), item.get("end_time"), item.get("limit", 100)
            if provider == "cryptoquant":
                params = build_cryptoquant_params(exchange_scope=item.get("exchange_scope", exchange_scope),
                    window=item.get("window"), limit=limit, start_time=start, end_time=end)
                variant = item.get("window")
            elif provider == "glassnode":
                params = build_glassnode_params(interval=item.get("interval"), asset=item.get("asset", symbol),
                                                start_time=start, end_time=end)
                variant = item.get("interval")
            else:
                params = build_coinglass_params(endpoint, symbol=item.get("symbol", symbol), ticker=item.get("ticker", "GBTC"))
                variant = item.get("ticker") if endpoint == "bitcoin_etf_net_assets_history" else None
            plan.append(_request(provider, endpoint, params, variant))
        return plan
    if not exchange_scope:
        raise ValueError("exchange_scope_required")
    limits = {**(BOOTSTRAP_LIMITS if mode == "bootstrap" else INCREMENTAL_LIMITS),
              **dict((bootstrap_limits if mode == "bootstrap" else incremental_limits) or {})}
    plan = [_request("coinglass", endpoint, build_coinglass_params(endpoint, symbol=symbol)) for endpoint in ENDPOINT_SPECS["coinglass"]]
    for endpoint in ENDPOINT_SPECS["cryptoquant"]:
        for window in ("day", "hour"):
            limit = limits[f"cryptoquant_{window}"]
            if mode == "bootstrap":
                limit = max(limit, ETF_HISTORICAL_BOOTSTRAP_LIMITS.get((endpoint, window), limit))
            plan.append(_request("cryptoquant", endpoint, build_cryptoquant_params(exchange_scope=exchange_scope,
                window=window, limit=_positive_int(limit, "limit")), window))
    if include_secondary:
        for endpoint in ENDPOINT_SPECS["glassnode"]:
            for interval in (("24h",) if endpoint == "us_spot_etf_flows_net" else ("1h", "24h")):
                plan.append(_request("glassnode", endpoint, build_glassnode_params(interval=interval, asset=symbol), interval))
    return plan


def sanitize_provider_error(error: Any) -> str:
    text = str(error)
    for token in ("Authorization", "CG-API-KEY", "api_key", "apikey", "token"):
        if token.lower() in text.lower():
            return "provider_error_redacted"
    return text[:500]


def extract_endpoint_raw(*, fetcher: ProviderFetcher, request: Mapping[str, Any], fetched_at: str) -> dict[str, Any]:
    try:
        response = fetcher(provider=request["provider"], endpoint_id=request["endpoint_id"], path=request["path"],
                           params=deepcopy(request["params"]))
        if not isinstance(response, (Mapping, Sequence)) or isinstance(response, (str, bytes)):
            raise TypeError("unsupported_provider_body")
        body, status, error = deepcopy(response), "ok", None
    except Exception as exc:
        body, status, error = None, "error", sanitize_provider_error(exc)
    return {"status": status, "path": request["path"], "params": deepcopy(request["params"]),
            "response": body, "error": error, "fetched_at": fetched_at}


def extract_etf_exchange_flows_raw(*, fetcher: ProviderFetcher, mode: str, exchange_scope: str | None,
                                   symbol: str = "BTC", include_secondary: bool = False,
                                   recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                   data_mode: str = "live", is_demo: bool = False, now: int,
                                   bootstrap_limits: Mapping[str, int] | None = None,
                                   incremental_limits: Mapping[str, int] | None = None) -> dict[str, Any]:
    if data_mode not in {"live", "synthetic"} or (data_mode == "synthetic" and is_demo is not True):
        raise ValueError("invalid_data_mode")
    timestamp = _positive_int(now, "now")
    plan = build_etf_exchange_flows_fetch_plan(mode=mode, exchange_scope=exchange_scope, symbol=symbol,
        include_secondary=include_secondary, recovery_requests=recovery_requests,
        bootstrap_limits=bootstrap_limits, incremental_limits=incremental_limits)
    requested_at, raw = _utc_iso(timestamp), {provider: {} for provider in PROVIDERS}
    for request in plan:
        entry = extract_endpoint_raw(fetcher=fetcher, request=request, fetched_at=requested_at)
        target = raw[request["provider"]].setdefault(request["endpoint_id"], {})
        if request["variant"] is None:
            raw[request["provider"]][request["endpoint_id"]] = entry
        else:
            target[str(request["variant"])] = entry
    return {
        "schema": {"id": "trad_elatin.etf_exchange_flows.extracted_raw.v1", "version": "1.0.0"},
        "family": FAMILY,
        "stage": "extracted_raw",
        "mode": mode,
        "data_mode": data_mode,
        "is_demo": is_demo,
        "context": {
            "asset": symbol,
            "exchange_scope": exchange_scope,
            "include_secondary": include_secondary,
        },
        "requested_at": requested_at,
        "raw": raw,
    }


class EtfExchangeFlowsRawExtractor:
    def __init__(self, *, fetcher: ProviderFetcher, exchange_scope: str | None, symbol: str = "BTC",
                 include_secondary: bool = False, data_mode: str = "live", is_demo: bool = False) -> None:
        self.options = {"fetcher": fetcher, "exchange_scope": exchange_scope, "symbol": symbol,
                        "include_secondary": include_secondary, "data_mode": data_mode, "is_demo": is_demo}

    def run(self, *, mode: str, now: int, recovery_requests=None, bootstrap_limits=None, incremental_limits=None):
        return extract_etf_exchange_flows_raw(mode=mode, now=now, recovery_requests=recovery_requests,
            bootstrap_limits=bootstrap_limits, incremental_limits=incremental_limits, **self.options)
