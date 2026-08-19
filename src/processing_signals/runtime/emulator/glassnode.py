from __future__ import annotations

from typing import Any, Mapping

from .common import filter_scalar_records
from .fixture_store import FixtureStore


def fetch(store: FixtureStore, family: str, *, endpoint_id: str, path: str,
          params: Mapping[str, Any], request: Mapping[str, Any]) -> Any:
    interval = str(params.get("i", "1h"))

    if family == "prices_ohlcv":
        if endpoint_id not in {"price_usd_ohlc", "marketcap_usd"}:
            raise ValueError(f"unsupported Glassnode Prices endpoint: {endpoint_id}")
        rows = store.load("glassnode", family, endpoint_id, f"{interval}_raw.json")
    elif family == "etf_exchange_flows":
        rows = store.load("glassnode", family, endpoint_id, f"{interval}_raw.json")
    elif family == "long_short_liquidations":
        mapping = {
            "glassnode_long_liquidations": "futures_liquidated_volume_long_sum",
            "glassnode_short_liquidations": "futures_liquidated_volume_short_sum",
            "glassnode_total_liquidations": "futures_liquidated_total_volume_sum",
            "glassnode_long_liquidation_dominance": "futures_liquidated_volume_long_relative",
        }
        rows = store.load("glassnode", family, mapping[endpoint_id], "1h_raw.json")
    elif family == "on_chain_miners":
        mapping = {
            "balance_miners_sum": ("balance_miners_sum", f"{interval}_raw.json"),
            "sopr": ("sopr", f"{interval}_raw.json"),
            "hash_rate_mean": ("hash_rate_mean", f"{interval}_raw.json"),
            "difficulty_latest": ("difficulty_latest", f"{interval}_raw.json"),
            "balance_miners_change": ("balance_miners_change", f"{interval}_raw.json"),
            "transfers_volume_from_miners_sum": ("transfers_volume_from_miners_sum", f"{interval}_raw.json"),
            "miners_unspent_supply": ("miners_unspent_supply", "24h_raw.json"),
            "revenue_sum": ("revenue_sum", "24h_usd_raw.json"),
            "revenue_from_fees": ("revenue_from_fees", "24h_raw.json"),
        }
        metric, filename = mapping[endpoint_id]
        rows = store.load("glassnode", family, metric, filename)
    elif family == "open_interest_and_funding":
        rows = store.load("glassnode", family, endpoint_id, "1h_raw.json")
    elif family == "volatility_market_regimes":
        mapping = {
            "realized_volatility": "realized_volatility_1_week",
            "dvol_ohlc": "dvol_ohlc",
        }
        if endpoint_id not in mapping:
            raise ValueError(f"unsupported Glassnode volatility endpoint: {endpoint_id}")
        rows = store.load("glassnode", family, mapping[endpoint_id], "1h_raw.json")
    elif family == "cvd_volume_orderflow":
        rows = store.load("glassnode", family, endpoint_id, "1h_raw.json")
    else:
        raise ValueError(f"unsupported Glassnode family: {family}")

    return filter_scalar_records(list(rows), start=params.get("s"), end=params.get("u"), time_key="t")
