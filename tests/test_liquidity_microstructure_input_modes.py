from liquidity_microstructure_helpers import valid_fetcher
from processing_signals.input.liquidity_microstructure.liquidity_microstructure_data_raw_preprocessing import run_liquidity_microstructure_input


def test_incremental_upserts_and_preserves_previous_state_on_failure():
    bootstrap = run_liquidity_microstructure_input(fetcher=valid_fetcher, reference_timestamp=1_700_000_000)
    failed = run_liquidity_microstructure_input(fetcher=lambda **_: (_ for _ in ()).throw(RuntimeError("down")),
                                                requested_mode="incremental", existing_contract=bootstrap,
                                                reference_timestamp=1_700_000_100)
    before = bootstrap["providers"]["coinglass"]["market_history"]["records"]
    after = failed["providers"]["coinglass"]["market_history"]["records"]
    assert after == before
    assert failed["providers"]["coinglass"]["market_history"]["reason"] == "no_data"


def test_recovery_only_rebuilds_requested_dataset():
    bootstrap = run_liquidity_microstructure_input(fetcher=valid_fetcher, reference_timestamp=1_700_000_000)
    recovered = run_liquidity_microstructure_input(fetcher=valid_fetcher, requested_mode="recovery", existing_contract=bootstrap,
                                                   recovery_requests=["whale_index"], reference_timestamp=1_700_000_100)
    assert recovered["providers"]["coinglass"]["orderbook"] == bootstrap["providers"]["coinglass"]["orderbook"]
