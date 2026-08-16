from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

from processing_signals.classification.open_interest_and_funding.open_interest_and_funding_classifier import classify_open_interest_and_funding
from processing_signals.classification.open_interest_and_funding.open_interest_and_funding_contract_builder import build_open_interest_and_funding_contract
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_processor import process_open_interest_and_funding


def _contracts():
    source = runpy.run_path(str(Path(__file__).with_name("test_open_interest_and_funding_processing_vertical.py")))["_input"]()
    processing = process_open_interest_and_funding(source)
    classification = classify_open_interest_and_funding(processing)
    screen = build_open_interest_and_funding_contract({"processing": processing, "classification": classification})
    return source, processing, classification, screen


def test_runtime_contract_is_all_exchanges_and_five_hmi_timeframes_only():
    _, processing, _, screen = _contracts()
    assert tuple(processing["series"]["open_interest_ohlc"]["timeframes"]) == ("1m", "5m", "15m", "1h", "4h", "1d")
    assert screen["selectors"]["market"]["options"] == ["all_exchanges"]
    assert screen["selectors"]["timeframe"]["options"] == ["5m", "15m", "1h", "4h", "1d"]
    assert "general" not in json.dumps(screen)


def test_invalid_market_cap_contract_is_absent_and_kpis_match_sp():
    _, processing, classification, screen = _contracts()
    assert "market_cap" not in json.dumps(processing)
    assert "market_cap" not in json.dumps(classification)
    assert "market_cap" not in json.dumps(screen)
    assert [item["metric_id"] for item in screen["kpis"]["items"]] == [
        "open_interest_usd", "oi_change_24h", "oi_funding_state", "provider_availability", "funding_rate"]


def test_native_ohlc_indicators_history_events_strict_json_and_immutability():
    source, processing, classification, screen = _contracts()
    before = copy.deepcopy({"processing": processing, "classification": classification})
    bundle = copy.deepcopy(before)
    second = build_open_interest_and_funding_contract(bundle)
    assert bundle == before and second == screen
    json.dumps(screen, allow_nan=False)
    assert set(screen["charts"]) == {"open_interest_candlestick", "open_interest_ohlc", "macd", "rsi", "tsi", "adx",
        "stochastic", "williams_r", "cci", "atr", "wasserstein_distance", "bollinger_band_width", "oi_roc", "funding_rate"}
    assert all(set(processing["indicators"]["open_interest"]["timeframes"][tf]) >= {
        "rsi", "tsi", "williams_r", "wasserstein_distance", "bollinger_band_width"} for tf in ("5m", "15m", "1h", "4h", "1d"))
    assert screen["history_contract"]["calculation_records"] == 220
    assert screen["history_contract"]["hmi_recalculation"] is False
    assert isinstance(screen["events"]["by_id"], dict) and isinstance(screen["events"]["indexes"], dict)
    assert source["stage"] == "input"
