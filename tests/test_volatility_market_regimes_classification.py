from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from processing_signals.classification.volatility_market_regimes.volatility_market_regimes_classifier import (
    CLASSIFICATION_VERSION,
    DAY_SECONDS,
    PROCESSING_VERSION,
    build_regime_transition_events,
    calculate_regime_confidence,
    calculate_regime_distribution,
    calculate_regime_persistence,
    calculate_regime_statistics,
    classify_daily_regime_history,
    classify_daily_regime_record,
    classify_percentile_regime,
    classify_positioning_record,
    classify_volatility_market_regimes,
    validate_volatility_market_regimes_processing_contract,
)

ROOT = Path(__file__).parents[1]
START = 1_700_000_000 // DAY_SECONDS * DAY_SECONDS


def _daily(index: int, rank: float | None, *, status: str = "available") -> dict:
    ts = START + index * DAY_SECONDS
    return {
        "timestamp": ts,
        "data_as_of": ts + 3600,
        "status": status,
        "reason": None,
        "realized_volatility_percent": 30.0 + index * 0.1,
        "realized_rolling_mean_30d": 35.0,
        "realized_rolling_std_30d": 5.0,
        "realized_z_score_30d": -1.0 + index / 100,
        "realized_percentile_rank_90d": rank,
        "long_percent": 53.0,
        "short_percent": 47.0,
        "long_short_ratio": 1.12,
        "net_long_percentage_points": 6.0,
    }


def _processing(days: int | str = 120, mode: str = "bootstrap") -> dict:
    # Backward-compatible test helper: older downstream tests passed mode positionally.
    if isinstance(days, str):
        mode, days = days, 120
    daily = [_daily(i, None if i < 89 else min(1.0, (i - 89) / 30)) for i in range(days)]
    positioning = [{"timestamp": START + i * DAY_SECONDS, "long_percent": 53.0, "short_percent": 47.0,
                    "long_short_ratio": 1.12, "net_long_percentage_points": 6.0} for i in range(days)]
    realized = [{"timestamp": START + i * DAY_SECONDS, "realized_volatility_percent": 30 + i * 0.1} for i in range(days)]
    return {
        "family": "volatility_market_regimes", "stage": "processing", "version": PROCESSING_VERSION, "mode": mode,
        "context": {"reference_timestamp": daily[-1]["data_as_of"], "input_execution_timestamp": daily[-1]["data_as_of"] + 5,
                    "asset": "BTC", "symbol": "BTCUSDT", "exchange": "Binance", "base_interval": "1h"},
        "source_availability": {},
        "features": {
            "positioning": {"status": "available", "reason": None, "records": positioning, "records_available": len(positioning),
                            "source_data_as_of": positioning[-1]["timestamp"]},
            "realized_volatility": {"status": "available", "reason": None, "records": realized, "records_available": len(realized),
                                    "source_data_as_of": realized[-1]["timestamp"]},
            "daily_regime_basis": {"status": "available", "reason": None, "records": daily, "current": daily[-1],
                                   "records_available": len(daily), "source_data_as_of": daily[-1]["data_as_of"], "warnings": []},
        },
        "quality": {"status": "ok", "warnings": [], "errors": []},
    }


def test_processing_contract_version_is_frozen_to_new_realized_only_contract():
    processing = _processing()
    validate_volatility_market_regimes_processing_contract(processing)
    old = copy.deepcopy(processing)
    old["version"] = "0.1.0"
    with pytest.raises(ValueError):
        validate_volatility_market_regimes_processing_contract(old)


@pytest.mark.parametrize("mode", ["bootstrap", "incremental", "recovery"])
def test_mode_is_preserved(mode):
    assert classify_volatility_market_regimes(_processing(mode=mode))["mode"] == mode


@pytest.mark.parametrize("rank,state", [(0.0, "low_vol"), (1/3, "normal"), (0.5, "normal"), (2/3, "normal"), (1.0, "high_vol")])
def test_percentile_boundaries(rank, state):
    assert classify_percentile_regime(rank) == state


@pytest.mark.parametrize("rank", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_invalid_percentiles_are_rejected(rank):
    with pytest.raises(ValueError):
        classify_percentile_regime(rank)


def test_confidence_uses_realized_percentile_only():
    low = calculate_regime_confidence(0.0, "low_vol")
    center = calculate_regime_confidence(0.5, "normal")
    high = calculate_regime_confidence(1.0, "high_vol")
    assert low["confidence_score"] == center["confidence_score"] == high["confidence_score"] == 1.0
    assert low["confidence_basis"] == "realized_percentile_boundary_distance"


def test_daily_warmup_is_unavailable_but_does_not_degrade_current_history():
    feature = _processing()["features"]["daily_regime_basis"]
    result = classify_daily_regime_history(feature)
    assert result["records_warmup"] == 89
    assert result["status"] == "available"
    assert result["current"]["regime"] in {"low_vol", "normal", "high_vol"}


def test_positioning_is_context_and_classified_independently():
    row = classify_positioning_record({"timestamp": START, "long_percent": 60.0, "short_percent": 40.0,
                                       "long_short_ratio": 1.6, "net_long_percentage_points": 20.0})
    assert row["positioning_state"] == "long_bias"
    assert row["crowding_state"] == "extreme_long"


def test_persistence_distribution_statistics_and_transitions():
    rows = [classify_daily_regime_record(_daily(0, 0.1)), classify_daily_regime_record(_daily(1, 0.1)),
            classify_daily_regime_record(_daily(2, 0.5)), classify_daily_regime_record(_daily(3, 0.9))]
    calculate_regime_persistence(rows)
    assert [r["persistence_days"] for r in rows] == [1, 2, 1, 1]
    distribution = calculate_regime_distribution(rows)
    assert distribution["full_history"]["classified_days"] == 4
    stats = calculate_regime_statistics(rows, rows[-1])
    assert {r["regime"] for r in stats} == {"low_vol", "normal", "high_vol"}
    events = build_regime_transition_events(rows)
    assert len(events["regime_transition_ids"]) == 2


def test_public_classifier_is_realized_only_and_quality_ok():
    output = classify_volatility_market_regimes(_processing())
    assert output["version"] == CLASSIFICATION_VERSION
    assert output["quality"]["status"] == "ok"
    assert set(output["classifications"]) == {"daily_regimes", "positioning"}
    assert set(output["source_availability"]) == {"processing.positioning", "processing.realized_volatility", "processing.daily_regime_basis"}
    text = json.dumps(output).lower()
    assert "deribit" not in text
    assert "implied" not in text
    assert "spread_volatility" not in text
    json.dumps(output, allow_nan=False)


def test_invalid_contract_returns_invalid_classification_contract():
    bad = _processing()
    bad["features"].pop("daily_regime_basis")
    output = classify_volatility_market_regimes(bad)
    assert output["quality"]["status"] == "invalid"
    assert output["stage"] == "classification"


def test_classifier_source_contains_no_removed_provider_or_removed_series_vocabulary():
    text = (ROOT / "src/processing_signals/classification/volatility_market_regimes/volatility_market_regimes_classifier.py").read_text().lower()
    assert "deribit" not in text
    assert "implied" not in text
    assert "spread_volatility" not in text
