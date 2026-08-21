from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from processing_signals.main.cvd_volume_orderflow import run_cvd_volume_orderflow_vertical
from processing_signals.input.cvd_volume_orderflow.cvd_volume_orderflow_data_raw_preprocessing import run_cvd_volume_orderflow_input
from processing_signals.processing.cvd_volume_orderflow.cvd_volume_orderflow_processor import process_cvd_volume_orderflow
from processing_signals.runtime.emulator import SyntheticProviderRouter


TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
IDENTITY_KEYS = (
    "metric_id", "kpi_id", "widget_id", "chart_id", "table_id", "badge_id",
    "id", "role", "family", "group", "indicator_id", "event_type", "event_group",
    "market", "timeframe", "window",
)


@pytest.fixture(scope="module")
def vertical() -> dict[str, Any]:
    return run_cvd_volume_orderflow_vertical(
        fetcher=SyntheticProviderRouter("runtime/contracts/input_raw").for_family("cvd_volume_orderflow"),
        reference_timestamp=1786147140,
        mode="bootstrap",
        selected_market="spot",
        selected_timeframe="15m",
        display_point_limit=220,
        data_mode="synthetic",
        is_demo=True,
        clock=lambda: 1786147140,
        include_debug_bundle=False,
    )


def _reference_item(reference: list[Any], item: Any, index: int) -> Any:
    if isinstance(item, Mapping):
        for key in IDENTITY_KEYS:
            if key not in item:
                continue
            for ref_item in reference:
                if isinstance(ref_item, Mapping) and ref_item.get(key, object()) == item[key]:
                    return ref_item
    return reference[index] if index < len(reference) else reference[0]


def _assert_same_structure(reference: Any, candidate: Any, path: str = "$") -> None:
    if isinstance(reference, Mapping):
        assert isinstance(candidate, Mapping), path
        # events.by_id is a runtime registry: IDs are intentionally dynamic.
        if path.endswith(".events.by_id"):
            prototypes = list(reference.values())
            for uid, event in candidate.items():
                prototype = next((row for row in prototypes
                    if row.get("event_type") == event.get("event_type")
                    and row.get("event_group") == event.get("event_group")), None)
                assert prototype is not None, f"{path}[{uid}]"
                _assert_same_structure(prototype, event, f"{path}[{uid}]")
            return
        assert set(reference) == set(candidate), path
        for key in reference:
            _assert_same_structure(reference[key], candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), path
        if not reference or all(not isinstance(item, (dict, list)) for item in reference):
            return
        for index, item in enumerate(candidate):
            _assert_same_structure(_reference_item(reference, item, index), item, f"{path}[{index}]")
        return
    # Runtime scalars are allowed to be null when a provider/rolling value is
    # unavailable.  Structural parity is key/nesting/type-family parity, not
    # equality to calibrated SP fixture values.
    if reference is None or candidate is None:
        return
    if isinstance(reference, (int, float)) and not isinstance(reference, bool):
        assert isinstance(candidate, (int, float)) and not isinstance(candidate, bool), path
        return
    assert type(reference) is type(candidate), path


def test_spot_and_futures_remain_native_cvd_visual_markets(vertical: dict[str, Any]) -> None:
    screen = vertical
    assert screen["context"]["markets"] == ["spot", "futures"]
    assert "general" not in screen["context"]["markets"]
    assert screen["screen"]["layout_contract"]["primary_market_columns"][0]["market"] == "spot"
    assert screen["screen"]["layout_contract"]["primary_market_columns"][1]["market"] == "futures"
    for market in ("spot", "futures"):
        chart = screen["charts"][f"cvd_{market}"]
        assert chart["chart_type"] == "candlestick"
        assert chart["native_ohlc"] is False
        assert chart["construction"] == "derived_from_interval_volume_delta_path"
        for timeframe in TIMEFRAMES:
            assert chart["series_by_timeframe"][timeframe]["representation"] == "candlestick"


def test_cross_market_kpis_and_provider_roles(vertical: dict[str, Any]) -> None:
    screen = vertical
    assert all(kpi["market"] == "cross_market" for kpi in screen["kpis"].values())
    input_contract = run_cvd_volume_orderflow_input(
        fetcher=SyntheticProviderRouter("runtime/contracts/input_raw").for_family("cvd_volume_orderflow"),
        reference_timestamp=1786147140,
        clock=lambda: 1786147140,
        target_display_records=1,
        warmup_records=0,
        include_footprint=False,
        include_cryptoquant_confirmation=False,
        include_glassnode_confirmation=True,
        max_pages=1,
    )
    processing = process_cvd_volume_orderflow(input_contract, clock=lambda: 1786147140)
    policy = processing["provider_reconciliation"]["policy"]
    assert policy == {
        "spot_cvd_semantic_primary": "glassnode",
        "granular_candle_source": "coinglass",
        "futures_flow_primary": "coinglass",
        "futures_glassnode_mapping": "pending_schema_verification",
        "hmi_ohlc_owner": "Processing",
    }
    glassnode = input_contract["markets"]["spot"]["confirmations"]["glassnode"]
    assert all(glassnode[metric]["status"] == "available" for metric in (
        "spot_cvd_sum", "spot_vd_sum", "spot_buying_volume_sum", "spot_selling_volume_sum"))


def test_screen_b_policy_is_native_orderflow_and_screen_a_has_only_six_mas(vertical: dict[str, Any]) -> None:
    ta = vertical["technical_analysis"]
    assert ta["recalculate_in_hmi"] is False
    assert ta["selector_contract"] == {
        "trend": ["ema_9", "ema_21", "sma_20", "sma_50", "wma_20", "wma_50"],
        "derived_analysis": ["cvd_slope_acceleration", "delta_zscore", "buy_sell_imbalance"],
        "momentum": ["price_cvd_divergence", "spot_futures_divergence"],
        "volatility": ["wasserstein_distance"],
    }
    expected = {
        "cvd_slope_acceleration", "delta_zscore", "buy_sell_imbalance",
        "price_cvd_divergence", "spot_futures_divergence", "wasserstein_distance",
    }
    for market in ("spot", "futures"):
        for timeframe in TIMEFRAMES:
            payload = ta["markets"][market]["timeframes"][timeframe]
            assert set(payload["indicators"]) == expected
            assert set(payload["overlays"]) == {"moving_averages"}
            assert payload["overlays"]["moving_averages"]["series_ids"] == [
                "ema_9", "ema_21", "sma_20", "sma_50", "wma_20", "wma_50"
            ]
            assert payload["calculation_history_records"] <= 730
            assert all(item["recalculate_in_hmi"] is False for item in payload["indicators"].values())


def test_screen_contract_matches_final_cvd_sp_structure(vertical: dict[str, Any]) -> None:
    reference_path = Path(__file__).parents[1] / "src" / "processing_signals" / "classification" / "cvd_volume_orderflow" / "cvd_screen_sp_v1_5.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    candidate = vertical
    assert candidate["schema"]["version"] == "1.5.0"
    assert tuple(candidate) == tuple(reference)
    _assert_same_structure(reference, candidate)
    json.dumps(candidate, ensure_ascii=False, allow_nan=False)


def test_events_are_only_approved_ma_crosses_and_are_anchored_to_visible_window(vertical: dict[str, Any]) -> None:
    allowed_pairs = {
        frozenset(("ema_9", "ema_21")),
        frozenset(("sma_20", "sma_50")),
        frozenset(("wma_20", "wma_50")),
    }
    for market in ("spot", "futures"):
        for timeframe in TIMEFRAMES:
            payload = vertical["technical_analysis"]["markets"][market]["timeframes"][timeframe]
            start = payload["timestamps"][0] if payload["timestamps"] else None
            for event in payload["events"]:
                calc = event["calculation"]
                assert frozenset((calc["first_series"], calc["second_series"])) in allowed_pairs
                assert start is None or event["timestamp"] >= start
                assert event["display"]["marker_anchor"] == "exact_interpolated_cross"
                assert event["event_timestamp_exact"] == calc["event_timestamp_exact"]
                assert event["event_value_exact"] == calc["event_value_exact"]


def test_delta_chart_is_owned_by_processing_not_hmi(vertical: dict[str, Any]) -> None:
    for market in ("spot", "futures"):
        chart = vertical["charts"][f"delta_buy_sell_{market}"]
        assert chart["chart_type"] == "buy_sell_flow_percent"
        for timeframe in TIMEFRAMES:
            payload = chart["series_by_timeframe"][timeframe]
            assert payload["calculation"]["formula"] == "cvd_close - cvd_open"
            assert payload["calculation"]["recalculate_in_hmi"] is False
            history = payload["calculation_history"]
            assert history["recalculate_in_hmi"] is False
            assert history["record_count"] <= 730
