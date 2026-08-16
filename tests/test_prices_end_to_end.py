from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.skip(reason="legacy multi-family main_pipeline retired; covered by current eight-family runtime tests")

from processing_signals.classification.classification_pipeline import CLASSIFICATION_FAMILY_HANDLERS
from processing_signals.input.input_pipeline                   import INPUT_FAMILY_HANDLERS
from processing_signals.main.main_pipeline                     import VERTICAL_FAMILY_HANDLERS, run_main_pipeline
from processing_signals.main.prices_ohlcv                      import run_prices_vertical
from processing_signals.processing.processing_pipeline         import PROCESSING_FAMILY_HANDLERS


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
BASE_TIMESTAMP    = 1_699_920_000


def test_prices_and_long_short_are_registered_in_all_pipelines():
    expected = ("prices_ohlcv", "long_short_liquidations", "on_chain_miners", "etf_exchange_flows")
    assert tuple(INPUT_FAMILY_HANDLERS) == expected
    assert tuple(PROCESSING_FAMILY_HANDLERS) == expected
    assert tuple(CLASSIFICATION_FAMILY_HANDLERS) == expected
    assert tuple(VERTICAL_FAMILY_HANDLERS) == expected


class SyntheticPricesFetcher:
    def __init__(self, *, incremental: bool = False) -> None:
        self.incremental = incremental
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        params    = kwargs["params"]
        timeframe = params["interval"]
        step      = TIMEFRAME_SECONDS[timeframe]
        futures   = kwargs["endpoint_id"] == "futures_ohlcv"
        start     = 117 if self.incremental else 0
        count     = int(params["limit"]) if self.incremental else 120
        offset    = 20.0 if futures else 0.0
        records   = []
        for index in range(start, start + count):
            trend = index * 0.25 + (15.0 if self.incremental and index >= 120 else 0.0)
            close = 100.0 + offset + trend + (index % 7 - 3) * 0.20
            records.append({"time": (BASE_TIMESTAMP + index * step) * 1000, "open": close - 0.50,
                            "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1_000.0 + index})
        return {"code": "0", "msg": "success", "data": records}


class GapRecoveryFetcher(SyntheticPricesFetcher):
    def __init__(self, *, recovery: bool = False) -> None:
        super().__init__()
        self.recovery = recovery

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        if self.recovery:
            self.calls.append(kwargs)
            timestamp = BASE_TIMESTAMP + 50 * TIMEFRAME_SECONDS["15m"]
            return {"code": "0", "msg": "success", "data": [{"time": timestamp * 1000, "open": 132.0, "high": 134.0,
                                                                   "low": 131.0, "close": 133.0, "volume": 1_050.0}]}
        response = super().__call__(**kwargs)
        if kwargs["endpoint_id"] == "futures_ohlcv" and kwargs["params"]["interval"] == "15m":
            missing_timestamp = (BASE_TIMESTAMP + 50 * TIMEFRAME_SECONDS["15m"]) * 1000
            response["data"] = [row for row in response["data"] if row["time"] != missing_timestamp]
        return response


def test_prices_bootstrap_end_to_end_builds_complete_screen():
    fetcher = SyntheticPricesFetcher()
    outputs = run_main_pipeline(family_arguments={"prices_ohlcv": {"fetcher": fetcher, "input_arguments": {"requested_mode": "bootstrap"},
                                                                     "now_timestamp": 1_800_000_000}})
    state  = outputs["prices_ohlcv"]
    screen = state["screen"]
    assert len(fetcher.calls) == 12
    assert set(state["input"]["markets"]) == {"spot", "futures", "general"}
    assert all(len(state["processing"]["markets"][market]["timeframes"]) == 6 for market in ("spot", "futures", "general"))
    assert len(screen["charts"]) == 10
    tables = screen["tables"]["indicators_metrics"]
    assert len(tables["indicator_package"]["rows"]) == 11
    assert len(tables["technical_bias"]["rows"]) == 4
    assert len(tables["statistical_performance"]["rows"]) == 17
    assert screen["context"]["performance_basis"] == "market_returns"
    tsi = next(row for row in tables["indicator_package"]["rows"] if row["metric_id"] == "tsi")
    assert tsi["parameters"] == {"long_period": 25, "short_period": 13}
    assert screen["quality"]["is_complete"] is True
    json.dumps(screen, allow_nan=False)


def test_prices_incremental_end_to_end_preserves_history_and_updates_affected_data():
    bootstrap_fetcher = SyntheticPricesFetcher()
    bootstrap         = run_prices_vertical(fetcher=bootstrap_fetcher, input_arguments={"requested_mode": "bootstrap"}, now_timestamp=1_800_000_000)
    original_5m       = bootstrap["processing"]["markets"]["spot"]["timeframes"]["5m"]["records"]
    original_first    = dict(original_5m[0])
    original_current  = bootstrap["processing"]["features"]["indicators"]["general"]["1m"]["rsi"]["current"]["rsi"]

    incremental_fetcher = SyntheticPricesFetcher(incremental=True)
    updated             = run_prices_vertical(fetcher=incremental_fetcher, input_arguments={"requested_mode": "incremental", "incremental_limits": {"1m": 6, "15m": 6}},
                                  previous_state=bootstrap, now_timestamp=1_800_000_060)
    updated_5m = updated["processing"]["markets"]["spot"]["timeframes"]["5m"]["records"]
    assert len(incremental_fetcher.calls) == 4
    assert updated_5m[0] == original_first
    assert len(updated_5m) >= len(original_5m)
    assert updated["input"]["markets"]["spot"]["timeframes"]["1m"]["records"][0]["timestamp"] == BASE_TIMESTAMP
    assert updated["processing"]["features"]["indicators"]["general"]["1m"]["rsi"]["current"]["rsi"] != original_current
    assert updated["screen"]["context"]["updated_at"] > bootstrap["screen"]["context"]["updated_at"]
    assert updated["screen"]["quality"]["is_complete"] is True
    for timeframe in TIMEFRAME_SECONDS:
        spot    = updated["processing"]["markets"]["spot"]["timeframes"][timeframe]["records"]
        futures = updated["processing"]["markets"]["futures"]["timeframes"][timeframe]["records"]
        general = updated["processing"]["markets"]["general"]["timeframes"][timeframe]["records"]
        assert {row["timestamp"] for row in general} <= ({row["timestamp"] for row in spot} & {row["timestamp"] for row in futures})
    json.dumps(updated["screen"], allow_nan=False)


def test_prices_recovery_repairs_a_directed_gap():
    bootstrap = run_prices_vertical(fetcher=GapRecoveryFetcher(), input_arguments={"requested_mode": "bootstrap"}, now_timestamp=1_800_000_000)
    assert bootstrap["input"]["quality"]["recovery_required"] is True
    recovery_fetcher  = GapRecoveryFetcher(recovery=True)
    missing_timestamp = BASE_TIMESTAMP + 50 * TIMEFRAME_SECONDS["15m"]
    recovered         = run_prices_vertical(fetcher=recovery_fetcher,
                                    input_arguments={"requested_mode": "recovery", "recovery_requests": [{"market": "futures", "timeframe": "15m",
                                                                                                             "limit": 1, "start_time": missing_timestamp,
                                                                                                             "end_time": missing_timestamp}]},
                                    previous_state=bootstrap, now_timestamp=1_800_000_120)
    assert len(recovery_fetcher.calls) == 1
    assert recovered["input"]["quality"]["recovery_required"] is False
    general_timestamps = {row["timestamp"] for row in recovered["processing"]["markets"]["general"]["timeframes"]["15m"]["records"]}
    assert missing_timestamp in general_timestamps
    assert recovered["screen"]["quality"]["is_complete"] is True
