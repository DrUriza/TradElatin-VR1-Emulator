from __future__ import annotations

import json
from pathlib import Path

import pytest

from processing_signals.runtime.emulator import SUPPORTED_FAMILIES, SyntheticProviderRouter


FAMILIES = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


def test_raw_fixture_inventory_is_exactly_eight_families_and_json_safe() -> None:
    root = Path("runtime/contracts/input_raw")
    files = sorted(root.rglob("*.json"))
    assert files, "fixture inventory must not be empty"
    for path in files:
        json.loads(path.read_text(encoding="utf-8"))
    assert SUPPORTED_FAMILIES == FAMILIES


def test_router_rejects_unknown_family_and_provider() -> None:
    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    with pytest.raises(ValueError, match="unsupported family"):
        router.for_family("unknown")
    fetcher = router.for_family("prices_ohlcv")
    with pytest.raises(ValueError, match="unsupported provider"):
        fetcher(provider="unknown", endpoint_id="x", path="/x", params={})


def test_prices_fetcher_preserves_provider_envelope_and_limit() -> None:
    fetcher = SyntheticProviderRouter("runtime/contracts/input_raw").for_family("prices_ohlcv")
    response = fetcher(
        provider="coinglass",
        endpoint_id="spot_ohlcv",
        path="/api/spot/price/history",
        params={"symbol": "BTCUSDT", "exchange": "Binance", "interval": "1m", "limit": 7},
    )
    assert response["code"] == "0"
    assert len(response["data"]) == 7
    assert set(response["data"][0]) == {"time", "open", "high", "low", "close", "volume_usd"}
