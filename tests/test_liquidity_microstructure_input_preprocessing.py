import json

from liquidity_microstructure_helpers import valid_fetcher
from processing_signals.input.liquidity_microstructure.liquidity_microstructure_data_raw_preprocessing import run_liquidity_microstructure_input


def test_normalizes_all_datasets_and_strict_json():
    output = run_liquidity_microstructure_input(fetcher=valid_fetcher, reference_timestamp=1_700_000_000)
    provider = output["providers"]["coinglass"]
    assert provider["orderbook"]["spot"]["records"][0]["timestamp"] == 1_700_000_000
    assert {row["range_percent"] for row in provider["order_depth"]["spot"]["records"]} == {1, 5, 10}
    assert provider["whale_activity"]["records"][0]["whale_index_value"] == -0.25
    assert provider["market_history"]["status"] == "unavailable"
    assert provider["market_history"]["reason"] == "no_data"
    assert provider["market_history"]["records"] == []
    assert provider["large_trades"]["spot"]["reason"] == "stream_warmup_in_progress"
    json.dumps(output, allow_nan=False)


def test_unverified_orderbook_sides_are_partial_and_not_guessed():
    def fetcher(**request):
        if request["endpoint_id"].endswith("orderbook_heatmap"):
            return {"code": 0, "data": [{"time": 1_700_000_000_000, "data": [[[100, 1]], [[101, 1]]]}]}
        return valid_fetcher(**request)
    output = run_liquidity_microstructure_input(fetcher=fetcher, reference_timestamp=1_700_000_000)
    dataset = output["providers"]["coinglass"]["orderbook"]["spot"]
    assert dataset["status"] == "partial"
    assert dataset["reason"] == "orderbook_side_mapping_unverified"
    assert "bid_levels" not in dataset["records"][0]
