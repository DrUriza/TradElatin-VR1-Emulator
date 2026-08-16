from __future__ import annotations

import copy
import json

from processing_signals.main.on_chain_miners import run_on_chain_miners_vertical
from on_chain_miners_test_helpers import DAY, NOW, FakeFetcher


def _run(**kwargs):
    return run_on_chain_miners_vertical(
        fetcher=FakeFetcher(),
        input_arguments={"requested_mode": "bootstrap", "include_screen_extensions": True},
        now_timestamp=NOW,
        **kwargs,
    )


def test_bootstrap_returns_all_four_immutable_stage_contracts():
    arguments = {"requested_mode": "bootstrap", "include_screen_extensions": True}
    before = copy.deepcopy(arguments)
    result = run_on_chain_miners_vertical(fetcher=FakeFetcher(), input_arguments=arguments, now_timestamp=NOW)
    assert tuple(result) == ("input", "processing", "classification", "screen")
    assert [result[name]["stage"] for name in result] == ["input", "processing", "classification", "screen_contract"]
    assert all(result[name].get("family", result[name].get("screen", {}).get("family")) == "on_chain_miners" for name in result)
    assert arguments == before
    json.dumps(result, allow_nan=False)


def test_incremental_uses_previous_input_without_mutating_bundle():
    bootstrap = _run()
    before = copy.deepcopy(bootstrap)
    result = run_on_chain_miners_vertical(
        fetcher=FakeFetcher(),
        input_arguments={"requested_mode": "incremental", "include_screen_extensions": True},
        previous_state=bootstrap,
        now_timestamp=NOW + DAY,
    )
    assert result["input"]["mode"] == "incremental"
    assert bootstrap == before
    assert len(result["input"]["series"]["miner_reserve"]["records"]) >= len(bootstrap["input"]["series"]["miner_reserve"]["records"])


def test_recovery_rebuilds_requested_gap_and_returns_complete_bundle():
    bootstrap = _run()
    result = run_on_chain_miners_vertical(
        fetcher=FakeFetcher(),
        input_arguments={"requested_mode": "recovery", "include_screen_extensions": True,
                         "recovery_requests": [{"metric_id": "sopr", "start_timestamp": NOW - DAY, "end_timestamp": NOW}]},
        previous_state=bootstrap,
        now_timestamp=NOW,
    )
    assert result["input"]["mode"] == "recovery"
    assert result["processing"]["mode"] == "recovery"
    assert result["classification"]["mode"] == "recovery"
    assert result["screen"]["mode"] == "recovery"


def test_screen_timestamps_and_quality_follow_real_anchors():
    screen = _run()["screen"]
    assert screen["context"]["data_as_of"] == screen["quality"]["data_as_of"]
    assert screen["operational_status"]["data_as_of"] == screen["quality"]["data_as_of"]
    data_as_of = screen["quality"]["data_as_of"]
    assert data_as_of is None or (type(data_as_of) is int and 0 < data_as_of <= NOW)
    if data_as_of is None:
        assert any(warning.endswith("processing_data_as_of_unavailable") for warning in screen["quality"]["warnings"])
