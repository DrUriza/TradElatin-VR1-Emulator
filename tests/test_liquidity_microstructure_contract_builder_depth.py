from liquidity_microstructure_contract_builder_helpers import bundle, runtime
# ruff: noqa: E702
from processing_signals.classification.liquidity_microstructure.liquidity_microstructure_contract_builder import build_liquidity_microstructure_screen_contract


def test_depth_filters_preserve_cumulative_values_and_summaries():
    source = bundle(); current = source["processing"]["markets"]["perpetual"]["orderbook"]["timeframes"]["1m"]["current"]
    current["bid_levels"][1]["cumulative_quantity_base"] = 987.6
    result = build_liquidity_microstructure_screen_contract(source, runtime_context=runtime(), display_point_limit=1)
    assert result["charts"]["order_depth"]["records"][0]["distance_percent"] <= 1
    assert result["charts"]["order_depth"]["records"][0]["band"] == "0_to_1"
    assert result["tables"]["orderbook_snapshot"]["summary"] == current["bands"]["full_visible_book"]
    assert result["charts"]["order_depth"]["metadata"]["not_full_market_book"] is True
