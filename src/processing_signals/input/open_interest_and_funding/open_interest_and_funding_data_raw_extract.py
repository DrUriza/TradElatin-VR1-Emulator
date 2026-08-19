"""Raw extraction for the open-interest and funding Input vertical."""
from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

FAMILY             = "open_interest_and_funding"
SCREEN_TIMEFRAMES  = ("1m", "5m", "15m", "1h", "4h", "1d")
TIMEFRAME_SECONDS  = {"1m": 60, "5m": 300, "15m": 900, "1h": 3_600, "4h": 14_400, "1d": 86_400}
VALID_MODES        = {"bootstrap", "incremental", "recovery"}
BOOTSTRAP_LIMIT    = 1000
BOOTSTRAP_TIMEFRAMES = ("1m", "15m", "1h", "4h", "1d")
INCREMENTAL_LIMITS = {"1m": 15}

ENDPOINTS = {
    "aggregated_open_interest_ohlc": {"provider": "coinglass", "endpoint_id": "aggregated_open_interest_ohlc", "path": "/api/futures/open-interest/aggregated-history", "request_kind": "timeframe_series", "raw_shape": "code_msg_data_list_ohlc", "required": True, "canonical_id": "open_interest_ohlc"},
    "oi_weighted_funding_rate_ohlc": {"provider": "coinglass", "endpoint_id": "oi_weighted_funding_rate_ohlc", "path": "/api/futures/funding-rate/oi-weight-history", "request_kind": "timeframe_series", "raw_shape": "code_msg_data_list_ohlc_rate", "required": True, "canonical_id": "funding_rate_ohlc"},
    "open_interest_exchange_list": {"provider": "coinglass", "endpoint_id": "open_interest_exchange_list", "path": "/api/futures/open-interest/exchange-list", "request_kind": "snapshot", "raw_shape": "code_msg_data_exchange_oi_list", "required": True, "canonical_id": "open_interest_exchange_list"},
    "funding_rate_exchange_list": {"provider": "coinglass", "endpoint_id": "funding_rate_exchange_list", "path": "/api/futures/funding-rate/exchange-list", "request_kind": "snapshot", "raw_shape": "code_msg_data_nested_funding_exchange_list", "required": True, "canonical_id": "funding_rate_exchange_list"},
    "options_info": {"provider": "coinglass", "endpoint_id": "options_info", "path": "/api/option/info", "request_kind": "snapshot", "raw_shape": "code_msg_data_options_info", "required": False, "canonical_id": "options_info"},
    "cryptoquant_open_interest": {"provider": "cryptoquant", "endpoint_id": "open_interest", "path": "/btc/market-data/open-interest", "request_kind": "confirmation_series", "raw_shape": "status_result_data_open_interest", "required": False, "canonical_id": "cryptoquant_open_interest"},
    "cryptoquant_funding_rates": {"provider": "cryptoquant", "endpoint_id": "funding_rates", "path": "/btc/market-data/funding-rates", "request_kind": "confirmation_series", "raw_shape": "status_result_data_funding_rates", "required": False, "canonical_id": "cryptoquant_funding_rates"},
    "glassnode_futures_open_interest_sum": {"provider": "glassnode", "endpoint_id": "futures_open_interest_sum", "path": "/v1/metrics/derivatives/futures_open_interest_sum", "request_kind": "confirmation_series", "raw_shape": "list_t_v_scalar", "required": False, "canonical_id": "glassnode_futures_open_interest_sum"},
    "glassnode_futures_funding_rate_perpetual": {"provider": "glassnode", "endpoint_id": "futures_funding_rate_perpetual", "path": "/v1/metrics/derivatives/futures_funding_rate_perpetual", "request_kind": "confirmation_series", "raw_shape": "list_t_v_scalar", "required": False, "canonical_id": "glassnode_futures_funding_rate_perpetual"},
    "glassnode_futures_estimated_leverage_ratio": {"provider": "glassnode", "endpoint_id": "futures_estimated_leverage_ratio", "path": "/v1/metrics/derivatives/futures_estimated_leverage_ratio", "request_kind": "confirmation_series", "raw_shape": "list_t_v_scalar", "required": False, "canonical_id": "glassnode_futures_estimated_leverage_ratio"},
}

OpenInterestAndFundingFetcher = Callable[..., Mapping[str, Any] | Sequence[Any]]


def _timestamp(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer timestamp")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _iso_utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y%m%dT%H%M%S")


def build_coinglass_history_params(*, timeframe: str, limit: int, start_timestamp: int | None = None, end_timestamp: int | None = None) -> dict[str, Any]:
    if timeframe not in SCREEN_TIMEFRAMES:
        raise ValueError("unsupported timeframe")
    params: dict[str, Any] = {"symbol": "BTC", "interval": timeframe, "limit": _positive_int(limit, "limit")}
    if start_timestamp is not None:
        params["start_time"] = _timestamp(start_timestamp, "start_timestamp") * 1000
    if end_timestamp is not None:
        params["end_time"] = _timestamp(end_timestamp, "end_timestamp") * 1000
    if start_timestamp is not None and end_timestamp is not None and start_timestamp > end_timestamp:
        raise ValueError("start_timestamp must not exceed end_timestamp")
    return params


def build_cryptoquant_params(*, from_timestamp: int, to_timestamp: int, limit: int, window: str = "hour") -> dict[str, Any]:
    start, end = _timestamp(from_timestamp, "from_timestamp"), _timestamp(to_timestamp, "to_timestamp")
    if start > end or window != "hour":
        raise ValueError("invalid CryptoQuant range or window")
    return {"exchange": "all_exchange", "window": window, "from": _compact_utc(start), "to": _compact_utc(end), "limit": _positive_int(limit, "limit"), "format": "json"}


def build_glassnode_params(*, from_timestamp: int, to_timestamp: int, currency: str | None = None) -> dict[str, Any]:
    start, end = _timestamp(from_timestamp, "from_timestamp"), _timestamp(to_timestamp, "to_timestamp")
    if start > end:
        raise ValueError("from_timestamp must not exceed to_timestamp")
    params: dict[str, Any] = {"a": "BTC", "i": "1h", "s": start, "u": end}
    if currency is not None:
        if currency not in {"USD", "NATIVE"}:
            raise ValueError("unsupported Glassnode currency")
        params["c"] = currency
    return params


def _resolve_existing(existing_state: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if existing_state is None:
        return None
    if not isinstance(existing_state, Mapping):
        raise ValueError("existing_state must be an Input contract or vertical bundle")
    candidate = existing_state.get("input", existing_state)
    if not isinstance(candidate, Mapping) or candidate.get("family") != FAMILY or candidate.get("stage") != "input":
        raise ValueError("existing_state is incompatible")
    return candidate


def _last_timestamp(existing: Mapping[str, Any] | None, metric_id: str, timeframe: str) -> int | None:
    if existing is None:
        return None
    records = existing.get("series", {}).get(metric_id, {}).get("timeframes", {}).get(timeframe, {}).get("records", [])
    timestamps = [record.get("timestamp") for record in records if isinstance(record, Mapping) and type(record.get("timestamp")) is int]
    return max(timestamps, default=None)


def _request(spec: Mapping[str, Any], *, metric_id: str, timeframe: str | None, start: int | None, end: int | None, params: Mapping[str, Any], suffix: str) -> dict[str, Any]:
    return {"request_id": f"{spec['provider']}:{metric_id}:{suffix}", "canonical_id": spec["canonical_id"], "metric_id": metric_id,
            "provider": spec["provider"], "endpoint_id": spec["endpoint_id"], "path": spec["path"], "request_kind": spec["request_kind"],
            "required": spec["required"], "timeframe": timeframe, "from_timestamp": start, "to_timestamp": end, "params": copy.deepcopy(dict(params))}


def build_open_interest_and_funding_fetch_plan(*, mode: str, reference_timestamp: int, existing_state: Mapping[str, Any] | None = None,
                                                recovery_requests: Sequence[Mapping[str, Any]] | None = None, include_snapshots: bool = True,
                                                include_confirmations: bool = True, refresh_secondary: bool = False) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError("unsupported mode")
    reference = _timestamp(reference_timestamp, "reference_timestamp")
    existing = _resolve_existing(existing_state)
    if mode == "recovery":
        if not isinstance(recovery_requests, Sequence) or isinstance(recovery_requests, (str, bytes)) or not recovery_requests:
            raise ValueError("recovery_requests must be a non-empty sequence")
        validated = []
        for item in recovery_requests:
            if not isinstance(item, Mapping) or set(item) - {"metric_id", "timeframe", "start_timestamp", "end_timestamp", "limit"}:
                raise ValueError("invalid recovery request")
            metric, timeframe = item.get("metric_id"), item.get("timeframe")
            if metric not in {"open_interest_ohlc", "funding_rate_ohlc"} or timeframe not in SCREEN_TIMEFRAMES:
                raise ValueError("unsupported recovery metric or timeframe")
            start, end = _timestamp(item.get("start_timestamp"), "start_timestamp"), _timestamp(item.get("end_timestamp"), "end_timestamp")
            if start > end:
                raise ValueError("recovery range is inverted")
            if start > reference or end > reference:
                raise ValueError("recovery_range_after_reference_timestamp")
            interval = TIMEFRAME_SECONDS[timeframe]
            expanded_start, expanded_end = max(0, start - interval), min(reference, end + interval)
            limit = item.get("limit", max(1, (expanded_end - expanded_start) // interval + 1))
            validated.append((metric, timeframe, expanded_start, expanded_end, _positive_int(limit, "limit")))
        plan = []
        seen = set()
        for metric, timeframe, start, end, limit in validated:
            identity = (metric, timeframe, start, end, limit)
            if identity in seen:
                continue
            seen.add(identity)
            key = "aggregated_open_interest_ohlc" if metric == "open_interest_ohlc" else "oi_weighted_funding_rate_ohlc"
            spec = ENDPOINTS[key]
            plan.append(_request(spec, metric_id=metric, timeframe=timeframe, start=start, end=end,
                                 params=build_coinglass_history_params(timeframe=timeframe, limit=limit, start_timestamp=start, end_timestamp=end),
                                 suffix=f"{timeframe}:{start}:{end}:limit:{limit}"))
        return plan

    plan: list[dict[str, Any]] = []
    primary_timeframes = BOOTSTRAP_TIMEFRAMES if mode == "bootstrap" else ("1m",)
    for metric, key in (("open_interest_ohlc", "aggregated_open_interest_ohlc"), ("funding_rate_ohlc", "oi_weighted_funding_rate_ohlc")):
        spec = ENDPOINTS[key]
        for timeframe in primary_timeframes:
            limit = BOOTSTRAP_LIMIT if mode == "bootstrap" else INCREMENTAL_LIMITS[timeframe]
            end = reference
            existing_last = _last_timestamp(existing, metric, timeframe)
            start = max(0, (existing_last if mode == "incremental" and existing_last is not None else end - (limit - 1) * TIMEFRAME_SECONDS[timeframe]) - TIMEFRAME_SECONDS[timeframe])
            plan.append(_request(spec, metric_id=metric, timeframe=timeframe, start=start, end=end,
                                 params=build_coinglass_history_params(timeframe=timeframe, limit=limit, start_timestamp=start, end_timestamp=end), suffix=timeframe))
    # The two exchange snapshots drive current OI/Funding widgets and remain live.
    # Options and confirmation providers are secondary and are refreshed only on
    # bootstrap or an explicit secondary pass.
    if include_snapshots:
        for key, params in (("open_interest_exchange_list", {"symbol": "BTC"}), ("funding_rate_exchange_list", {})):
            spec = ENDPOINTS[key]
            plan.append(_request(spec, metric_id=key, timeframe=None, start=None, end=reference, params=params, suffix="snapshot"))
        if mode != "incremental" or refresh_secondary:
            spec = ENDPOINTS["options_info"]
            plan.append(_request(spec, metric_id="options_info", timeframe=None, start=None, end=reference, params={"symbol": "BTC"}, suffix="snapshot"))
    if include_confirmations and (mode != "incremental" or refresh_secondary):
        start = max(0, reference - (BOOTSTRAP_LIMIT - 1) * 3_600)
        for key in ("cryptoquant_open_interest", "cryptoquant_funding_rates"):
            spec = ENDPOINTS[key]
            plan.append(_request(spec, metric_id=key, timeframe="hour", start=start, end=reference,
                                 params=build_cryptoquant_params(from_timestamp=start, to_timestamp=reference, limit=BOOTSTRAP_LIMIT), suffix="hour"))
        for key in ("glassnode_futures_open_interest_sum", "glassnode_futures_funding_rate_perpetual", "glassnode_futures_estimated_leverage_ratio"):
            spec = ENDPOINTS[key]
            currency = "USD" if key == "glassnode_futures_open_interest_sum" else None
            plan.append(_request(spec, metric_id=key, timeframe="1h", start=start, end=reference,
                                 params=build_glassnode_params(from_timestamp=start, to_timestamp=reference, currency=currency), suffix="1h"))
    return plan


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+", "Authorization: [REDACTED]", message)
    message = re.sub(r"(?i)\bauthorization\b\s+(?:bearer[\s-]+)?[^\s,;]+", "Authorization [REDACTED]", message)
    message = re.sub(r"(?i)\bbearer\b\s+[^\s,;]+", "Bearer [REDACTED]", message)
    message = re.sub(r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret)\b(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", message)
    return message


def _execute_request(*, fetcher: OpenInterestAndFundingFetcher, request: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: copy.deepcopy(request[key]) for key in ("request_id", "canonical_id", "metric_id", "provider", "endpoint_id", "path", "request_kind", "required", "timeframe", "params", "from_timestamp", "to_timestamp")}
    try:
        response = fetcher(provider=request["provider"], endpoint_id=request["endpoint_id"], path=request["path"], params=copy.deepcopy(request["params"]))
        payload.update(status="ok", response=copy.deepcopy(response), error=None)
    except Exception as exc:  # provider failures are isolated by contract
        payload.update(status="error", response=None, error={"type": type(exc).__name__, "message": _safe_error_message(exc)})
    return payload


class OpenInterestAndFundingRawExtractor:
    def __init__(self, fetcher: OpenInterestAndFundingFetcher) -> None:
        if not callable(fetcher):
            raise ValueError("fetcher must be callable")
        self.fetcher = fetcher

    def extract(self, *, mode: str, reference_timestamp: int, existing_state: Mapping[str, Any] | None = None,
                recovery_requests: Sequence[Mapping[str, Any]] | None = None, include_snapshots: bool = True, include_confirmations: bool = True,
                data_mode: str = "live", is_demo: bool = False, execution_timestamp: int | None = None,
                refresh_secondary: bool = False) -> dict[str, Any]:
        if data_mode not in {"live", "synthetic"} or type(is_demo) is not bool or (data_mode == "synthetic" and not is_demo):
            raise ValueError("invalid data_mode/is_demo combination")
        reference = _timestamp(reference_timestamp, "reference_timestamp")
        execution = _timestamp(int(time.time()) if execution_timestamp is None else execution_timestamp, "execution_timestamp")
        plan = build_open_interest_and_funding_fetch_plan(mode=mode, reference_timestamp=reference, existing_state=existing_state,
            recovery_requests=recovery_requests, include_snapshots=include_snapshots, include_confirmations=include_confirmations,
            refresh_secondary=refresh_secondary)
        raw = {"series": {metric: {"provider": "coinglass", "endpoint_id": endpoint, "timeframes": {}} for metric, endpoint in
                (("open_interest_ohlc", "aggregated_open_interest_ohlc"), ("funding_rate_ohlc", "oi_weighted_funding_rate_ohlc"))},
               "snapshots": {}, "confirmations": {}}
        for request in plan:
            payload = _execute_request(fetcher=self.fetcher, request=request)
            if request["request_kind"] == "timeframe_series":
                raw["series"][request["metric_id"]]["timeframes"][request["timeframe"]] = payload
            elif request["request_kind"] == "snapshot":
                raw["snapshots"][request["metric_id"]] = payload
            else:
                raw["confirmations"][request["metric_id"]] = payload
        return {"family": FAMILY, "stage": "raw_input", "mode": mode, "context": {"asset": "BTC", "exchange_scope": "all_exchanges",
                "primary_provider": "coinglass", "confirmation_providers": ["cryptoquant", "glassnode"], "data_mode": data_mode, "is_demo": is_demo,
                "reference_timestamp": reference, "execution_timestamp": execution, "requested_at": _iso_utc(execution),
                "include_snapshots": include_snapshots, "include_confirmations": include_confirmations,
                "refresh_secondary": bool(refresh_secondary)}, "raw": raw}


def extract_open_interest_and_funding_raw(*, fetcher: OpenInterestAndFundingFetcher, mode: str, reference_timestamp: int,
                                          existing_state: Mapping[str, Any] | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                          include_snapshots: bool = True, include_confirmations: bool = True, data_mode: str = "live",
                                          is_demo: bool = False, execution_timestamp: int | None = None,
                                          refresh_secondary: bool = False) -> dict[str, Any]:
    return OpenInterestAndFundingRawExtractor(fetcher).extract(mode=mode, reference_timestamp=reference_timestamp, existing_state=existing_state,
        recovery_requests=recovery_requests, include_snapshots=include_snapshots, include_confirmations=include_confirmations,
        data_mode=data_mode, is_demo=is_demo, execution_timestamp=execution_timestamp, refresh_secondary=refresh_secondary)
