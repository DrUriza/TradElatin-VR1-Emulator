from __future__ import annotations

from processing_signals.runtime.emulator import SyntheticProviderRouter


def test_unified_runtime_emulator_serves_etf_provider_raw() -> None:
    fetcher = SyntheticProviderRouter("runtime/contracts/input_raw").for_family("etf_exchange_flows")
    response = fetcher(provider="coinglass", endpoint_id="bitcoin_etf_flows", path="/api/etf/bitcoin/flow-history", params={})
    assert response["code"] == "0"
    assert response["data"]
    assert {"timestamp", "flow_usd", "price_usd", "etf_flows"}.issubset(response["data"][0])
