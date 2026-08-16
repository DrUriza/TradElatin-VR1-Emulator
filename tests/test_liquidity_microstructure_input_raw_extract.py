from processing_signals.input.liquidity_microstructure.liquidity_microstructure_data_raw_extract import (
    ENDPOINT_MANIFEST, build_liquidity_microstructure_fetch_plan, extract_liquidity_microstructure_raw,
)


def test_manifest_and_deterministic_plan_cover_eight_feeds():
    assert tuple(ENDPOINT_MANIFEST) == ("spot_orderbook_heatmap", "perpetual_orderbook_heatmap", "spot_order_depth",
                                        "perpetual_order_depth", "spot_large_trades", "perpetual_large_trades",
                                        "whale_index", "market_data_history")
    first = build_liquidity_microstructure_fetch_plan(reference_timestamp=1_700_000_000)
    second = build_liquidity_microstructure_fetch_plan(reference_timestamp=1_700_000_000)
    assert first == second
    assert len(first) == 38
    assert {item["dimensions"]["range_percent"] for item in first if item["endpoint_id"].endswith("order_depth")} == {1, 5, 10}
    assert {item["transport"] for item in first} == {"rest", "websocket"}
    assert all("api_key" not in repr(item).lower() for item in first)


def test_failures_are_isolated_and_recovery_is_directed():
    def fetcher(**request):
        if request["endpoint_id"] == "whale_index":
            raise RuntimeError("down")
        return [] if request["transport"] == "websocket" else {"code": 0, "data": []}
    bundle = extract_liquidity_microstructure_raw(fetcher=fetcher, reference_timestamp=1_700_000_000)
    assert any(item["status"] == "error" for item in bundle["requests"])
    recovery = build_liquidity_microstructure_fetch_plan(mode="recovery", reference_timestamp=1_700_000_000,
                                                          recovery_requests=["market_data_history"])
    assert recovery == []
