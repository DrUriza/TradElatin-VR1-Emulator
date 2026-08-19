from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy

import pytest

from processing_signals.main.open_interest_and_funding import build_open_interest_and_funding_screen
from processing_signals.runtime.contract_validator import _semantic_checks, _structure_checks

PROCESSING_TEST = Path(__file__).with_name("test_open_interest_and_funding_processing_vertical.py")
TEMPLATE = Path(__file__).parents[1] / "src/processing_signals/classification/open_interest_and_funding/open_interest_and_funding_screen_sp_v1_12.json"
NATIVE_CHARTS = {
    "wasserstein_distance", "oi_dynamics", "oi_zscore_percentile", "price_oi_regime",
    "price_oi_divergence", "funding_oi_crowding",
}


def _input() -> dict:
    return runpy.run_path(str(PROCESSING_TEST))["_input"]()


def test_vertical_emits_current_oi_1_12_surface_only():
    out = build_open_interest_and_funding_screen(_input())
    assert out["schema_version"] == "1.12.0-oi-native-screen-b-demo"
    assert set(out["charts"]) == {"open_interest_candlestick", "open_interest_ohlc", *NATIVE_CHARTS}
    assert out["events"]["screen_b_display_policy"]["native_metrics_only"] is True
    assert out["events"]["screen_b_display_policy"]["technical_cross_arrows"] is False
    assert [row["metric_id"] for row in out["tables"]["indicators_metrics"]["indicator_package"]["markets"]["all_exchanges"]["1h"]] == [
        "oi_dynamics", "oi_zscore_percentile", "price_oi_regime", "price_oi_divergence",
        "funding_oi_crowding", "wasserstein_distance",
    ]


def test_vertical_matches_canonical_template_recursively():
    out = build_open_interest_and_funding_screen(_input())
    reference = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    errors: list[str] = []
    _structure_checks(reference, out, errors)
    _semantic_checks("open_interest_and_funding", out, errors)
    assert errors == []
    json.dumps(out, ensure_ascii=False, allow_nan=False)


def test_screen_a_events_are_only_ma_or_channel_and_screen_b_arrows_disabled():
    out = build_open_interest_and_funding_screen(_input())
    groups = {event["event_group"] for event in out["events"]["by_id"].values()}
    assert groups <= {"moving_average_cross", "channel_cross"}
    assert all(event["display"]["screen_a"] is True for event in out["events"]["by_id"].values())
    assert all(event["display"]["screen_b"] is False for event in out["events"]["by_id"].values())


def test_vertical_is_deterministic_and_does_not_mutate_input():
    source = _input()
    before = copy.deepcopy(source)
    first = build_open_interest_and_funding_screen(source)
    second = build_open_interest_and_funding_screen(copy.deepcopy(source))
    assert first == second
    assert source == before


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h", "4h", "1d"])
def test_timeframe_selection(timeframe):
    out = build_open_interest_and_funding_screen(_input(), selected_timeframe=timeframe)
    assert out["selectors"]["timeframe"]["selected"] == timeframe
    assert out["kpis"]["selected_timeframe"] == timeframe
