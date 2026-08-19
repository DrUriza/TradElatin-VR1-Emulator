import pytest

from liquidity_microstructure_helpers import valid_fetcher
from processing_signals.input.liquidity_microstructure.liquidity_microstructure_data_raw_preprocessing import run_liquidity_microstructure_input


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
