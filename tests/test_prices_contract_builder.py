from __future__ import annotations

import json
from copy import deepcopy

import pytest

from processing_signals.classification.prices_ohlcv.prices_ohlcv_classifier       import run_prices_ohlcv_classification
from processing_signals.classification.prices_ohlcv.prices_ohlcv_contract_builder import build_prices_screen_contract
from test_prices_classification_vertical                                          import MARKETS, TIMEFRAMES, make_processing_output


def test_contract_has_complete_prices_screen_structure():
    processing     = make_processing_output()
    classification = run_prices_ohlcv_classification(processing)
    contract       = build_prices_screen_contract(processing, classification)
    assert len(contract["charts"]) == 10
    assert contract["selectors"]["market"]["options"] == ["spot"]
    assert contract["selectors"]["timeframe"]["options"] == list(TIMEFRAMES)
    tables = contract["tables"]["indicators_metrics"]
    assert len(tables["indicator_package"]["rows"]) == 11
    assert len(tables["technical_bias"]["rows"]) == 4
    assert len(tables["statistical_performance"]["rows"]) == 17
    assert contract["quality"]["is_complete"] is True


def test_tsi_and_performance_basis_are_truthful():
    processing = make_processing_output()
    contract   = build_prices_screen_contract(processing, run_prices_ohlcv_classification(processing))
    serialized = json.dumps(contract, allow_nan=False)
    labels     = [row["label"] for row in contract["tables"]["indicators_metrics"]["indicator_package"]["rows"]]
    assert "TSI (25,13)" in labels and "TSI (14)" not in labels
    assert contract["context"]["performance_basis"] == "market_returns"
    assert contract["tables"]["indicators_metrics"]["statistical_performance"]["metadata"]["performance_basis"] == "market_returns"
    assert "NaN" not in serialized and "Infinity" not in serialized


def test_main_chart_switches_market_and_timeframe_together():
    processing = make_processing_output()
    contract   = build_prices_screen_contract(processing, run_prices_ohlcv_classification(processing))
    chart      = contract["charts"]["ohlcv"]
    assert chart["selected_market"] == "spot" and chart["selected_timeframe"] == "1h"
    assert set(chart["markets"]) == {"spot"}
    assert chart["optional_overlays"] == {"spot_close": True, "regression_channel": True, "moving_average_cross_markers": True, "regression_bollinger_cross_markers": True, "buy_sell_volume_split": True}


def test_events_keep_market_and_timeframe_sources():
    processing = make_processing_output()
    contract   = build_prices_screen_contract(processing, run_prices_ohlcv_classification(processing))
    events     = list(contract["events"]["by_id"].values())
    assert events and all(event["source"]["market"] and event["source"]["timeframe"] for event in events)


def test_single_selection_is_shared_by_every_consumer():
    processing = make_processing_output()
    processing["features"]["market_selector"].update({"selected_market": "spot", "selected_timeframe": "4h"})
    contract = build_prices_screen_contract(processing, run_prices_ohlcv_classification(processing))
    assert contract["selectors"]["market"]["selected"] == "spot"
    assert contract["selectors"]["market"]["visible"] is False
    assert contract["selectors"]["timeframe"]["selected"] == "4h"
    assert all(chart["selected_market"] == "spot" and chart["selected_timeframe"] == "4h" for chart in contract["charts"].values())
    tables = contract["tables"]["indicators_metrics"]
    assert tables["indicator_package"]["selected_market"] == "spot" and tables["indicator_package"]["selected_timeframe"] == "4h"
    assert "comparison" not in contract


def test_unavailable_is_valid_but_missing_classification_is_reported():
    processing     = make_processing_output()
    classification = run_prices_ohlcv_classification(processing)
    classification["indicator_signals"]["spot"]["1h"]["rsi"]["state"] = "unavailable"
    contract = build_prices_screen_contract(processing, classification)
    assert not any(field.endswith(".rsi") for field in contract["quality"]["missing_fields"])
    del classification["indicator_signals"]["spot"]["1h"]["rsi"]
    contract = build_prices_screen_contract(processing, classification)
    assert "classification.indicator_signals.spot.1h.rsi" in contract["quality"]["missing_fields"]
    assert contract["quality"]["is_complete"] is False


def test_display_semantics_tsi_parameters_quality_and_updated_at():
    processing = make_processing_output()
    processing["metadata"] = {"updated_at": "2026-07-24T12:00:00Z"}
    classification = run_prices_ohlcv_classification(processing)
    classification["indicator_signals"]["spot"]["1h"]["rsi"].update({"signal": "neutral", "state": "overbought"})
    classification["indicator_signals"]["spot"]["1h"]["adx"].update({"signal": "neutral", "state": "strong", "direction": "bullish"})
    contract = build_prices_screen_contract(processing, classification)
    rows     = {row["metric_id"]: row for row in contract["tables"]["indicators_metrics"]["indicator_package"]["rows"]}
    assert rows["rsi"]["display_signal"] == "Overbought"
    assert rows["adx"]["display_signal"] == "Strong"
    assert rows["tsi"]["parameters"] == {"long_period": 25, "short_period": 13}
    assert contract["context"]["updated_at"] == "2026-07-24T12:00:00Z"
    assert set(contract["quality"]["sources"]) == {"processing", "classification", "screen_coverage", "serialization"}


def test_performance_basis_comes_from_classification_and_stages_are_validated():
    processing     = make_processing_output()
    classification = run_prices_ohlcv_classification(processing)
    classification["statistical_signals"]["spot"]["1h"]["metadata"]["performance_basis"] = None
    contract = build_prices_screen_contract(processing, classification)
    assert contract["context"]["performance_basis"] is None
    assert contract["quality"]["is_complete"] is False
    with pytest.raises(ValueError, match="stage=processing"):
        build_prices_screen_contract({**processing, "stage": "input"}, classification)
    with pytest.raises(ValueError, match="stage=classification"):
        build_prices_screen_contract(processing, {**classification, "stage": "processing"})
