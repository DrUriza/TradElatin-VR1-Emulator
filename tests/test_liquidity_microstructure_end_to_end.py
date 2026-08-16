from copy import deepcopy

from liquidity_microstructure_vertical_helpers import arguments
from processing_signals.main.liquidity_microstructure import run_liquidity_microstructure_vertical


def test_end_to_end_deterministic_immutable_and_complete():
    args = arguments()
    original = deepcopy(args["runtime_context"])
    one = run_liquidity_microstructure_vertical(**args)
    two = run_liquidity_microstructure_vertical(**args)
    screen = one["screen_contract"]
    assert screen == two["screen_contract"] and args["runtime_context"] == original
    assert (len(screen["charts"]), len(screen["tables"]), len(screen["widgets"]), len(screen["drilldowns"])) == (6, 3, 6, 6)
    assert screen["context"]["provider"]["label"] == "CoinGlass" and screen["context"]["exchange"] == "Binance"
