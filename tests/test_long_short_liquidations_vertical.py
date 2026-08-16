from copy import deepcopy

import pytest

from long_short_liquidations_integration_helpers import REFERENCE, SIDE_IDS, SyntheticLiquidationsFetcher, run_vertical


def test_bootstrap_runs_four_real_layers_without_mutation():
    fetcher = SyntheticLiquidationsFetcher()
    output = run_vertical(fetcher=fetcher)
    assert list(output) == ["input", "processing", "classification", "screen"]
    assert [output[key]["stage"] for key in ("input", "processing", "classification", "screen")] == [
        "input", "processing", "classification", "screen_contract_final"]
    assert len(output["screen"]["kpis"]) == 7
    assert [item["id"] for item in output["screen"]["side_panel"]["items"]] == SIDE_IDS


def test_incremental_preserves_previous_state_and_builds_new_screen():
    bootstrap = run_vertical()
    before = deepcopy(bootstrap)
    incremental = run_vertical(REFERENCE + 3600, mode="incremental",
        fetcher=SyntheticLiquidationsFetcher(REFERENCE + 3600, updated_value=9), previous_state=bootstrap)
    records = incremental["input"]["providers"]["coinglass"]["aggregated_history"]["records"]
    assert bootstrap == before and incremental["screen"] is not bootstrap["screen"]
    assert len({item["timestamp"] for item in records}) == len(records)
    assert any(item["timestamp"] == REFERENCE + 3600 for item in records)


def test_recovery_and_invalid_recovery_before_fetch():
    bootstrap = run_vertical()
    request = [{"provider": "coinglass", "endpoint_id": "aggregated_liquidation_map",
                "params": {"symbol": "BTC", "range": "1d"}}]
    recovered = run_vertical(mode="recovery", previous_state=bootstrap, recovery_requests=request)
    assert recovered["screen"]["family"] == "long_short_liquidations"
    fetcher = SyntheticLiquidationsFetcher()
    with pytest.raises(ValueError):
        run_vertical(mode="recovery", fetcher=fetcher, previous_state=bootstrap,
            recovery_requests=[{"provider": "coinglass", "endpoint_id": "pair_liquidation_history",
                                "params": {"symbol": "BTCUSDT"}}])
    assert fetcher.calls == []


@pytest.mark.parametrize("timestamp", [True, 0, -1, 1.5])
def test_invalid_vertical_clock_is_rejected(timestamp):
    with pytest.raises(ValueError, match="now_timestamp"):
        run_vertical(timestamp)
