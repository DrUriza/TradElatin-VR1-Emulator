from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResamplingClass(str, Enum):
    RESAMPLEABLE_OHLC = "RESAMPLEABLE_OHLC"
    RESAMPLEABLE_ADDITIVE = "RESAMPLEABLE_ADDITIVE"
    RESAMPLEABLE_STATE = "RESAMPLEABLE_STATE"
    NATIVE_ONLY = "NATIVE_ONLY"
    RANGE_FILTER = "RANGE_FILTER"
    ROLLING_METRIC = "ROLLING_METRIC"


@dataclass(frozen=True)
class EndpointPolicy:
    resampling_class: ResamplingClass
    state_aggregator: str | None = None
    incremental_action: str = "FETCH_NATIVE"


_EXACT = {
    "spot_ohlcv": EndpointPolicy(ResamplingClass.RESAMPLEABLE_OHLC, incremental_action="FETCH_1M_15M_1D"),
    "futures_ohlcv": EndpointPolicy(ResamplingClass.RESAMPLEABLE_OHLC, incremental_action="FETCH_1M_15M_1D"),
    "spot_cvd": EndpointPolicy(ResamplingClass.RESAMPLEABLE_ADDITIVE, incremental_action="FETCH_1M_15M"),
    "futures_cvd": EndpointPolicy(ResamplingClass.RESAMPLEABLE_ADDITIVE, incremental_action="FETCH_1M_15M"),
    "spot_orderbook_heatmap": EndpointPolicy(ResamplingClass.RESAMPLEABLE_STATE, "last", "FETCH_1M_1H"),
    "perpetual_orderbook_heatmap": EndpointPolicy(ResamplingClass.RESAMPLEABLE_STATE, "last", "FETCH_1M_1H"),
    "spot_order_depth": EndpointPolicy(ResamplingClass.RESAMPLEABLE_STATE, "last", "FETCH_1M_1H"),
    "perpetual_order_depth": EndpointPolicy(ResamplingClass.RESAMPLEABLE_STATE, "last", "FETCH_1M_1H"),
    "whale_index": EndpointPolicy(ResamplingClass.NATIVE_ONLY),
    "dvol_ohlc": EndpointPolicy(ResamplingClass.NATIVE_ONLY),
    "realized_volatility_1_week": EndpointPolicy(ResamplingClass.ROLLING_METRIC),
}


def endpoint_policy(endpoint_id: str) -> EndpointPolicy:
    endpoint = str(endpoint_id).lower()
    if endpoint in _EXACT:
        return _EXACT[endpoint]
    if "funding" in endpoint or "whale" in endpoint:
        return EndpointPolicy(ResamplingClass.NATIVE_ONLY)
    if any(token in endpoint for token in ("map", "max_pain", "list", "snapshot")):
        return EndpointPolicy(ResamplingClass.RANGE_FILTER, incremental_action="RANGE_REFRESH")
    if any(token in endpoint for token in ("rolling", "realized_volatility", "ratio", "sopr", "mpi")):
        return EndpointPolicy(ResamplingClass.ROLLING_METRIC)
    if any(token in endpoint for token in ("ohlc", "price_history", "open_interest")):
        return EndpointPolicy(ResamplingClass.RESAMPLEABLE_OHLC)
    if any(token in endpoint for token in ("volume", "inflow", "outflow", "netflow", "footprint", "liquidation")):
        return EndpointPolicy(ResamplingClass.RESAMPLEABLE_ADDITIVE)
    return EndpointPolicy(ResamplingClass.NATIVE_ONLY)
