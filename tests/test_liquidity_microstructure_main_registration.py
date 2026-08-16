import inspect

from processing_signals.main.main_pipeline import VERTICAL_FAMILY_HANDLERS, run_main_pipeline


def test_main_registration_and_default():
    assert tuple(VERTICAL_FAMILY_HANDLERS) == (
        "prices_ohlcv", "cvd_volume_orderflow", "open_interest_and_funding", "etf_exchange_flows",
        "on_chain_miners", "volatility_market_regimes", "long_short_liquidations", "liquidity_microstructure",
    )
    assert inspect.signature(run_main_pipeline).parameters["enabled_families"].default == tuple(VERTICAL_FAMILY_HANDLERS)
