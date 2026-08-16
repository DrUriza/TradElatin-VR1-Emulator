"""Injectable raw extraction for Volatility / Market Regimes.

Providers frozen for this family:
- CoinGlass: top-position long/short ratio (positioning context)
- Glassnode: 1-week realized volatility (primary realized-vol source)
- Glassnode: DVOL OHLC (implied-volatility context / widget)
"""
from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

VOLATILITY_MARKET_REGIMES_FAMILY = "volatility_market_regimes"
COINGLASS_PROVIDER = "coinglass"
GLASSNODE_PROVIDER = "glassnode"
COINGLASS_POSITIONING_ENDPOINT_ID = "top_position_long_short_ratio"
GLASSNODE_REALIZED_VOL_ENDPOINT_ID = "realized_volatility"
GLASSNODE_DVOL_ENDPOINT_ID = "dvol_ohlc"
BASE_INTERVAL = "1h"
INTERVAL_SECONDS = 3600
BOOTSTRAP_HISTORY_DAYS = 730
INCREMENTAL_HOURS = 12
COINGLASS_MAX_LIMIT = 1000
VALID_MODES = {"bootstrap", "incremental", "recovery"}

ENDPOINT_MANIFEST = {
    (COINGLASS_PROVIDER, COINGLASS_POSITIONING_ENDPOINT_ID): "/api/futures/top-long-short-position-ratio/history",
    (GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID): "/v1/metrics/market/realized_volatility_1_week",
    (GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID): "/v1/metrics/derivatives/dvol_ohlc",
}
ENDPOINT_NORMALIZATION = {
    (COINGLASS_PROVIDER, COINGLASS_POSITIONING_ENDPOINT_ID): {
        "timestamp_unit": "milliseconds", "value_scale": "provider_percent"
    },
    (GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID): {
        "timestamp_unit": "seconds", "value_scale": "fraction_to_percent"
    },
    (GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID): {
        "timestamp_unit": "seconds", "value_scale": "volatility_index_points", "shape": "ohlc"
    },
}

VolatilityMarketRegimesFetcher = Callable[..., Mapping[str, Any] | Sequence[Any]]


def _timestamp(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}_must_be_non_negative_int")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name}_must_be_positive_int")
    return value


def _clock(clock: Callable[[], Any] | None) -> int:
    value = time.time() if clock is None else clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid_clock")
    return int(value)


def build_coinglass_positioning_params(*, start_timestamp: int, end_timestamp: int, limit: int) -> dict[str, Any]:
    start = _timestamp(start_timestamp, "start_timestamp")
    end = _timestamp(end_timestamp, "end_timestamp")
    size = _positive_int(limit, "limit")
    if start >= end or size > COINGLASS_MAX_LIMIT:
        raise ValueError("invalid_coinglass_window")
    return {
        "exchange": "Binance", "symbol": "BTCUSDT", "interval": BASE_INTERVAL, "limit": size,
        "start_time": start * 1000, "end_time": end * 1000,
    }


def build_glassnode_params(*, start_timestamp: int, end_timestamp: int) -> dict[str, Any]:
    start = _timestamp(start_timestamp, "start_timestamp")
    end = _timestamp(end_timestamp, "end_timestamp")
    if start >= end:
        raise ValueError("invalid_glassnode_window")
    return {"a": "BTC", "s": start, "u": end, "i": BASE_INTERVAL, "f": "json", "timestamp_format": "unix"}


def build_glassnode_realized_volatility_params(*, start_timestamp: int, end_timestamp: int) -> dict[str, Any]:
    return build_glassnode_params(start_timestamp=start_timestamp, end_timestamp=end_timestamp)


def build_glassnode_dvol_params(*, start_timestamp: int, end_timestamp: int) -> dict[str, Any]:
    return build_glassnode_params(start_timestamp=start_timestamp, end_timestamp=end_timestamp)


def _instruction(provider: str, endpoint_id: str, start: int, end: int, page: int = 1,
                 *, coinglass_limit: int | None = None) -> dict[str, Any]:
    key = (provider, endpoint_id)
    if key not in ENDPOINT_MANIFEST:
        raise ValueError("unknown_provider_endpoint")
    if provider == COINGLASS_PROVIDER:
        params = build_coinglass_positioning_params(
            start_timestamp=start, end_timestamp=end, limit=coinglass_limit or 1,
        )
        dimensions = {"exchange": "Binance", "symbol": "BTCUSDT", "interval": BASE_INTERVAL}
    elif provider == GLASSNODE_PROVIDER:
        params = build_glassnode_params(start_timestamp=start, end_timestamp=end)
        dimensions = {"asset": "BTC", "interval": BASE_INTERVAL}
    else:
        raise ValueError("unsupported_provider")
    return {
        "request_id": f"{provider}:{endpoint_id}:{start}:{end}:page:{page:04d}",
        "provider": provider,
        "endpoint_id": endpoint_id,
        "path": ENDPOINT_MANIFEST[key],
        "params": params,
        "dimensions": dimensions,
        "normalization": copy.deepcopy(ENDPOINT_NORMALIZATION[key]),
    }


def _coinglass_chunks(start: int, end: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cursor, page = start, 1
    while cursor < end:
        chunk_end = min(end, cursor + (COINGLASS_MAX_LIMIT - 1) * INTERVAL_SECONDS)
        limit = (chunk_end - cursor) // INTERVAL_SECONDS + 1
        output.append(_instruction(
            COINGLASS_PROVIDER, COINGLASS_POSITIONING_ENDPOINT_ID,
            cursor, chunk_end, page, coinglass_limit=limit,
        ))
        if chunk_end == end:
            break
        cursor, page = chunk_end, page + 1
    return output


def build_volatility_market_regimes_fetch_plan(
    *, mode: str, reference_timestamp: int,
    recovery_requests: Sequence[Mapping[str, Any]] | None = None,
    bootstrap_history_days: int = BOOTSTRAP_HISTORY_DAYS,
    incremental_hours: int = INCREMENTAL_HOURS,
) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError("unsupported_mode")
    reference = _timestamp(reference_timestamp, "reference_timestamp")
    if mode == "recovery":
        if not isinstance(recovery_requests, Sequence) or isinstance(recovery_requests, (str, bytes, bytearray)) or not recovery_requests:
            raise ValueError("recovery_requests_required")
        output: list[dict[str, Any]] = []
        for target in recovery_requests:
            if not isinstance(target, Mapping):
                raise ValueError("invalid_recovery_request")
            provider, endpoint = target.get("provider"), target.get("endpoint_id")
            if (provider, endpoint) not in ENDPOINT_MANIFEST:
                raise ValueError("unknown_recovery_target")
            start = _timestamp(target.get("start_timestamp"), "start_timestamp")
            end = _timestamp(target.get("end_timestamp"), "end_timestamp")
            if start >= end:
                raise ValueError("invalid_recovery_range")
            padded_start, padded_end = max(0, start - INTERVAL_SECONDS), end + INTERVAL_SECONDS
            if provider == COINGLASS_PROVIDER:
                output.extend(_coinglass_chunks(padded_start, padded_end))
            else:
                output.append(_instruction(str(provider), str(endpoint), padded_start, padded_end))
        return output

    duration = _positive_int(
        bootstrap_history_days if mode == "bootstrap" else incremental_hours, "history"
    ) * (86400 if mode == "bootstrap" else INTERVAL_SECONDS)
    start = max(0, reference - duration)
    return [
        *_coinglass_chunks(start, reference),
        _instruction(GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID, start, reference),
        _instruction(GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID, start, reference),
    ]


class VolatilityMarketRegimesRawExtractor:
    def __init__(self, fetcher: VolatilityMarketRegimesFetcher, *, clock: Callable[[], Any] | None = None) -> None:
        self.fetcher = fetcher
        self.clock = clock

    def build_fetch_plan(self, **kwargs: Any) -> list[dict[str, Any]]:
        return build_volatility_market_regimes_fetch_plan(**kwargs)

    def execute_request(self, instruction: Mapping[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(dict(instruction))
        try:
            response = self.fetcher(
                provider=request["provider"], endpoint_id=request["endpoint_id"],
                path=request["path"], params=copy.deepcopy(request["params"]),
            )
            request.update(status="ok", response=copy.deepcopy(response), error=None, warnings=[])
        except Exception as exc:
            request.update(status="error", response=None, error=f"{type(exc).__name__}: {exc}", warnings=[])
        return request

    def run(
        self, *, mode: str, reference_timestamp: int,
        recovery_requests: Sequence[Mapping[str, Any]] | None = None,
        bootstrap_history_days: int = BOOTSTRAP_HISTORY_DAYS,
        incremental_hours: int = INCREMENTAL_HOURS,
    ) -> dict[str, Any]:
        execution_timestamp = _clock(self.clock)
        plan = self.build_fetch_plan(
            mode=mode, reference_timestamp=reference_timestamp,
            recovery_requests=recovery_requests, bootstrap_history_days=bootstrap_history_days,
            incremental_hours=incremental_hours,
        )
        requests = [self.execute_request(instruction) for instruction in plan]
        return {
            "family": VOLATILITY_MARKET_REGIMES_FAMILY,
            "stage": "extracted_raw",
            "mode": mode,
            "reference_timestamp": _timestamp(reference_timestamp, "reference_timestamp"),
            "execution_timestamp": execution_timestamp,
            "requests": requests,
        }


def extract_volatility_market_regimes_raw(
    *, fetcher: VolatilityMarketRegimesFetcher, mode: str, reference_timestamp: int,
    recovery_requests: Sequence[Mapping[str, Any]] | None = None,
    clock: Callable[[], Any] | None = None, **kwargs: Any,
) -> dict[str, Any]:
    return VolatilityMarketRegimesRawExtractor(fetcher, clock=clock).run(
        mode=mode, reference_timestamp=reference_timestamp,
        recovery_requests=recovery_requests, **kwargs,
    )
