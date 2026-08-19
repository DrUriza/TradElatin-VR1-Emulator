"""Raw acquisition for Volatility / Market Regimes.

Volatility owns implied-volatility primitives only.  Realized volatility is
calculated from Prices and Glassnode RV is retained only as a bootstrap
confirmation.  Long/short positioning is owned entirely by Liquidations.
"""
from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

VOLATILITY_MARKET_REGIMES_FAMILY = "volatility_market_regimes"
GLASSNODE_PROVIDER = "glassnode"
GLASSNODE_REALIZED_VOL_ENDPOINT_ID = "realized_volatility"
GLASSNODE_DVOL_ENDPOINT_ID = "dvol_ohlc"
BASE_INTERVAL = "1h"
INTERVAL_SECONDS = 3600
BOOTSTRAP_HISTORY_DAYS = 730
INCREMENTAL_HOURS = 2
VALID_MODES = {"bootstrap", "incremental", "recovery"}

ENDPOINT_MANIFEST = {
    (GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID): "/v1/metrics/market/realized_volatility_1_week",
    (GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID): "/v1/metrics/derivatives/dvol_ohlc",
}
ENDPOINT_NORMALIZATION = {
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


def _instruction(endpoint_id: str, start: int, end: int) -> dict[str, Any]:
    key = (GLASSNODE_PROVIDER, endpoint_id)
    if key not in ENDPOINT_MANIFEST:
        raise ValueError("unknown_provider_endpoint")
    return {
        "request_id": f"glassnode:{endpoint_id}:{start}:{end}",
        "provider": GLASSNODE_PROVIDER,
        "endpoint_id": endpoint_id,
        "path": ENDPOINT_MANIFEST[key],
        "params": build_glassnode_params(start_timestamp=start, end_timestamp=end),
        "dimensions": {"asset": "BTC", "interval": BASE_INTERVAL},
        "normalization": copy.deepcopy(ENDPOINT_NORMALIZATION[key]),
    }


def build_volatility_market_regimes_fetch_plan(
    *, mode: str, reference_timestamp: int,
    recovery_requests: Sequence[Mapping[str, Any]] | None = None,
    bootstrap_history_days: int = BOOTSTRAP_HISTORY_DAYS,
    incremental_hours: int = INCREMENTAL_HOURS,
    existing_contract: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError("unsupported_mode")
    reference = _timestamp(reference_timestamp, "reference_timestamp")
    if mode == "recovery":
        if not isinstance(recovery_requests, Sequence) or isinstance(recovery_requests, (str, bytes, bytearray)) or not recovery_requests:
            raise ValueError("recovery_requests_required")
        output: list[dict[str, Any]] = []
        for target in recovery_requests:
            if not isinstance(target, Mapping) or target.get("provider") != GLASSNODE_PROVIDER:
                raise ValueError("invalid_recovery_request")
            endpoint = target.get("endpoint_id")
            if (GLASSNODE_PROVIDER, endpoint) not in ENDPOINT_MANIFEST:
                raise ValueError("unknown_recovery_target")
            start = _timestamp(target.get("start_timestamp"), "start_timestamp")
            end = _timestamp(target.get("end_timestamp"), "end_timestamp")
            if start >= end:
                raise ValueError("invalid_recovery_range")
            output.append(_instruction(str(endpoint), max(0, start - INTERVAL_SECONDS), end + INTERVAL_SECONDS))
        return output

    duration = _positive_int(bootstrap_history_days if mode == "bootstrap" else incremental_hours, "history") * (
        86400 if mode == "bootstrap" else INTERVAL_SECONDS
    )
    start = max(0, reference - duration)
    requests = [_instruction(GLASSNODE_DVOL_ENDPOINT_ID, start, reference)]
    if mode == "bootstrap":
        requests.append(_instruction(GLASSNODE_REALIZED_VOL_ENDPOINT_ID, start, reference))
    if mode != "incremental" or not isinstance(existing_contract, Mapping):
        return requests
    glassnode = existing_contract.get("providers", {}).get("glassnode", {})
    reference_bucket = reference - reference % INTERVAL_SECONDS
    filtered = []
    for request in requests:
        name = "dvol" if request["endpoint_id"] == GLASSNODE_DVOL_ENDPOINT_ID else "realized_volatility"
        last = glassnode.get(name, {}).get("last_available_timestamp") if isinstance(glassnode, Mapping) else None
        if type(last) is int and last - last % INTERVAL_SECONDS >= reference_bucket:
            continue
        filtered.append(request)
    return filtered


class VolatilityMarketRegimesRawExtractor:
    def __init__(self, fetcher: VolatilityMarketRegimesFetcher, *, clock: Callable[[], Any] | None = None) -> None:
        self.fetcher = fetcher
        self.clock = clock

    def build_fetch_plan(self, **kwargs: Any) -> list[dict[str, Any]]:
        return build_volatility_market_regimes_fetch_plan(**kwargs)

    def execute_request(self, instruction: Mapping[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(dict(instruction))
        try:
            response = self.fetcher(provider=request["provider"], endpoint_id=request["endpoint_id"],
                                    path=request["path"], params=copy.deepcopy(request["params"]))
            request.update(status="ok", response=copy.deepcopy(response), error=None, warnings=[])
        except Exception as exc:
            request.update(status="error", response=None, error=f"{type(exc).__name__}: {exc}", warnings=[])
        return request

    def run(self, *, mode: str, reference_timestamp: int,
            recovery_requests: Sequence[Mapping[str, Any]] | None = None,
            bootstrap_history_days: int = BOOTSTRAP_HISTORY_DAYS,
            incremental_hours: int = INCREMENTAL_HOURS,
            existing_contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
        execution_timestamp = _clock(self.clock)
        plan = self.build_fetch_plan(mode=mode, reference_timestamp=reference_timestamp,
                                     recovery_requests=recovery_requests,
                                     bootstrap_history_days=bootstrap_history_days,
                                     incremental_hours=incremental_hours,
                                     existing_contract=existing_contract)
        return {"family": VOLATILITY_MARKET_REGIMES_FAMILY, "stage": "extracted_raw", "mode": mode,
                "reference_timestamp": _timestamp(reference_timestamp, "reference_timestamp"),
                "execution_timestamp": execution_timestamp,
                "requests": [self.execute_request(item) for item in plan]}


def extract_volatility_market_regimes_raw(*, fetcher: VolatilityMarketRegimesFetcher, mode: str,
                                          reference_timestamp: int,
                                          recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                          clock: Callable[[], Any] | None = None,
                                          existing_contract: Mapping[str, Any] | None = None,
                                          **kwargs: Any) -> dict[str, Any]:
    return VolatilityMarketRegimesRawExtractor(fetcher, clock=clock).run(
        mode=mode, reference_timestamp=reference_timestamp, recovery_requests=recovery_requests,
        existing_contract=existing_contract, **kwargs)
