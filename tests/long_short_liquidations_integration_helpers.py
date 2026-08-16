from __future__ import annotations

from copy import deepcopy

from processing_signals.main.long_short_liquidations import run_long_short_liquidations_vertical

REFERENCE = 1_740_000_000
CONTEXT = {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT",
           "market": "futures", "price_precision": 2}
SIDE_IDS = ["pressure_score", "pressure_label", "dominant_side", "side_imbalance",
    "top_exchange_concentration", "max_event_spike", "max_single_liquidation",
    "nearest_long_cluster", "nearest_short_cluster"]


def _nominal_fetcher(**kwargs):
    endpoint_id = kwargs["endpoint_id"]
    if endpoint_id == "supported_exchange_pairs":
        return {"code": "0", "data": {"Binance": ["BTCUSDT"]}}
    if endpoint_id == "aggregated_liquidation_history":
        return {"code": "0", "data": [{"time": REFERENCE * 1000,
                "aggregated_long_liquidation_usd": 1, "aggregated_short_liquidation_usd": 2}]}
    if endpoint_id == "liquidation_exchange_list":
        return {"code": "0", "data": [{"exchange": "Binance", "liquidation_usd": 3,
                "long_liquidation_usd": 1, "short_liquidation_usd": 2}]}
    if endpoint_id == "pair_liquidation_history":
        return {"code": "0", "data": [{"time": REFERENCE * 1000,
                "long_liquidation_usd": 1, "short_liquidation_usd": 2}]}
    if endpoint_id == "liquidation_order_events":
        return {"code": "0", "data": [{"exchange_name": "Binance", "symbol": "BTCUSDT",
                "base_asset": "BTC", "side": 1, "price": 50_000, "usd_value": 10_000,
                "time": REFERENCE * 1000}]}
    if endpoint_id in {"aggregated_liquidation_map", "pair_liquidation_map"}:
        return {"code": "0", "data": {"data": {"50000": [[50_000, 10, None, None]]}}}
    if endpoint_id == "liquidation_max_pain":
        return {"code": "0", "data": [{"symbol": "BTC", "price": 50_000,
                "long_max_pain_liq_level": 1, "long_max_pain_liq_price": 49_000,
                "short_max_pain_liq_level": 2, "short_max_pain_liq_price": 51_000}]}
    if endpoint_id == "cryptoquant_liquidations":
        return {"status": {"code": 200}, "result": {"window": "hour", "data": [{
                "date": "2025-02-19T21:00:00Z", "long_liquidations": None,
                "short_liquidations": None, "long_liquidations_usd": 1,
                "short_liquidations_usd": 2}]}}
    return [{"t": REFERENCE, "v": 1}]



class SyntheticLiquidationsFetcher:
    def __init__(self, timestamp: int = REFERENCE, updated_value: float = 1.) -> None:
        self.timestamp, self.updated_value, self.calls = timestamp, updated_value, []

    def __call__(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        result = deepcopy(_nominal_fetcher(**kwargs))
        endpoint = kwargs["endpoint_id"]
        if endpoint in {"aggregated_liquidation_history", "pair_liquidation_history"}:
            long_key = "aggregated_long_liquidation_usd" if endpoint.startswith("aggregated") else "long_liquidation_usd"
            short_key = "aggregated_short_liquidation_usd" if endpoint.startswith("aggregated") else "short_liquidation_usd"
            result["data"] = [{"time": REFERENCE * 1000, long_key: self.updated_value, short_key: 2},
                              {"time": self.timestamp * 1000, long_key: 3, short_key: 4}]
        elif endpoint == "liquidation_order_events":
            result["data"][0]["time"] = self.timestamp * 1000
        return result


def vertical_arguments(timestamp: int = REFERENCE, *, mode: str = "bootstrap", fetcher=None, previous_state=None,
                       recovery_requests=None):
    fetcher = fetcher or SyntheticLiquidationsFetcher(timestamp)
    input_arguments = {"requested_mode": mode, "exchanges": ("Binance",),
        "exchange_pairs": {"Binance": "BTCUSDT"}, "cryptoquant_exchanges": ("binance",)}
    if recovery_requests is not None:
        input_arguments["recovery_requests"] = recovery_requests
    arguments = {"fetcher": fetcher, "input_arguments": input_arguments,
        "processing_arguments": {"reference_price_context": {"value": 50_000, "timestamp": timestamp,
            "source_family": "prices_ohlcv", "source_market": "spot", "source_timeframe": "1m",
            "price_field": "close", "is_closed_bar": True}},
        "contract_arguments": {"context": CONTEXT},
        "now_timestamp": timestamp,
        "runtime_metadata": {"data_mode": "synthetic", "is_demo": True, "cache_status": "disabled"}}
    if previous_state is not None:
        arguments["previous_state"] = previous_state
    return arguments


def run_vertical(timestamp: int = REFERENCE, **kwargs):
    return run_long_short_liquidations_vertical(**vertical_arguments(timestamp, **kwargs))
