from __future__ import annotations

import copy
import json

from processing_signals.main.on_chain_miners import run_on_chain_miners_vertical
from processing_signals.runtime.emulator.provider_router import SyntheticProviderRouter

NOW = 1786100400


def _run(*, mode="bootstrap", previous_state=None):
    return run_on_chain_miners_vertical(
        fetcher=SyntheticProviderRouter().for_family("on_chain_miners"),
        input_arguments={"requested_mode": mode, "include_screen_extensions": True,
                         "data_mode": "synthetic", "is_demo": True},
        previous_state=previous_state,
        now_timestamp=NOW,
    )


def test_bootstrap_returns_all_four_json_safe_stages():
    result = _run()
    assert tuple(result) == ("input", "processing", "classification", "screen")
    assert [result[name]["stage"] for name in result] == ["input", "processing", "classification", "screen_contract"]
    assert result["screen"]["schema"]["version"] == "2.0.0"
    assert "miner_analysis" in result["screen"] and "technical_analysis" not in result["screen"]
    json.dumps(result, allow_nan=False)


def test_incremental_reuses_previous_input_without_mutating_bundle():
    bootstrap = _run()
    before = copy.deepcopy(bootstrap)
    incremental = _run(mode="incremental", previous_state=bootstrap)
    assert incremental["input"]["mode"] == "incremental"
    assert bootstrap == before
    assert incremental["input"]["series"]["miner_reserve"]["records"]


def test_revenue_breakdown_is_unavailable_without_retired_fee_endpoint():
    result = _run()
    feature = result["processing"]["features"]["miner_revenue_breakdown"]
    assert feature["records"] == []
    assert feature["current"]["status"] == "unavailable"
    row = rows[-1]
    assert abs(row["total_revenue_usd"] - row["block_reward_revenue_usd"] - row["fee_revenue_usd"]) < 1e-6
    assert abs(row["derived_fee_share_ratio"] - row["provider_fee_ratio"]) < 1e-12
