import pytest

from liquidity_microstructure_helpers import valid_fetcher
from processing_signals.input.liquidity_microstructure.liquidity_microstructure_data_raw_preprocessing import run_liquidity_microstructure_input


def test_trade_side_hash_and_below_threshold_are_preserved():
    def fetcher(**request):
        if request["endpoint_id"].endswith("large_trades"):
            return [{"time": 1_700_000_000_000, "side": 1, "price": "30000", "volume_usd": "9999"}]
        return valid_fetcher(**request)
    first = run_liquidity_microstructure_input(fetcher=fetcher, reference_timestamp=1_700_000_000)
    second = run_liquidity_microstructure_input(fetcher=fetcher, reference_timestamp=1_700_000_000)
    event = first["providers"]["coinglass"]["large_trades"]["spot"]["events"][0]
    assert event["side"] == "sell" and event["meets_configured_threshold"] is False
    assert event["event_id"] == second["providers"]["coinglass"]["large_trades"]["spot"]["events"][0]["event_id"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True])
def test_non_finite_and_boolean_numbers_make_dataset_invalid(bad):
    def fetcher(**request):
        if request["endpoint_id"] == "market_data_history":
            return {"code": 0, "data": [{"timestamp": 1_700_000_000, "price": bad,
                                           "circulating_supply": 1, "market_cap": 1}]}
        return valid_fetcher(**request)
    output = run_liquidity_microstructure_input(fetcher=fetcher, reference_timestamp=1_700_000_000)
    history = output["providers"]["coinglass"]["market_history"]
    assert history["status"] == "unavailable" and history["reason"] == "no_data" and history["records"] == []


def test_input_contains_no_financial_derivations_and_debug_is_opt_in():
    output = run_liquidity_microstructure_input(fetcher=valid_fetcher, reference_timestamp=1_700_000_000)
    forbidden = {"mid_price", "best_bid", "best_ask", "spread", "spread_bps", "total_depth", "net_depth",
                 "bid_share", "ask_share", "imbalance", "market_impact", "liquidity_score", "liquidity_state"}
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()
    assert not forbidden.intersection(keys(output))
    assert "debug_raw" not in output
    assert "debug_raw" in run_liquidity_microstructure_input(fetcher=valid_fetcher, debug_raw=True,
                                                              reference_timestamp=1_700_000_000)
