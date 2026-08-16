from __future__ import annotations

import json
from pathlib import Path

from processing_signals.runtime.emulator import SyntheticProviderRouter
from processing_signals.main.open_interest_and_funding.open_interest_and_funding_vertical import run_open_interest_and_funding_vertical

HMI_TFS = ["5m", "15m", "1h", "4h", "1d"]
KPI_IDS = ["open_interest_usd", "oi_change_24h", "oi_funding_state", "provider_availability", "funding_rate", "estimated_leverage_ratio"]


def _screen():
    return run_open_interest_and_funding_vertical(
        mode="bootstrap",
        fetcher=SyntheticProviderRouter().for_family("open_interest_and_funding"),
        reference_timestamp=1786150800,
        execution_timestamp=1786150805,
        selected_timeframe="1h",
        include_snapshots=True,
        include_confirmations=True,
        data_mode="synthetic",
        is_demo=True,
    )


def _shape(value):
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        if value and isinstance(value[0], (dict, list)):
            return [_shape(value[0])]
        # Scalar list cardinality/content is runtime data, not contract nesting.
        return ["scalar_list"]
    return "scalar"


def test_final_schema_market_timeframes_and_kpis():
    out = _screen()
    assert out["schema_version"] == "1.11.0-oi-adx-crosses"
    assert out["context"]["available_markets"] == ["all_exchanges"]
    assert out["context"]["available_timeframes"] == HMI_TFS
    assert out["selectors"]["timeframe"]["options"] == HMI_TFS
    assert [item["metric_id"] for item in out["kpis"]["items"]] == KPI_IDS


def test_estimated_leverage_ratio_is_glassnode_runtime_data():
    out = _screen()
    elr = out["kpis"]["items"][-1]
    assert elr["metric_id"] == "estimated_leverage_ratio"
    assert elr["status"] == "available"
    assert elr["value"] is not None
    assert elr["source"]["provider"] == "glassnode"
    assert elr["source"]["metric"] == "futures_estimated_leverage_ratio"
    assert elr["provenance"]["integration_state"] == "runtime_feed"


def test_native_ohlc_and_precomputed_analysis_contract():
    out = _screen()
    chart = out["charts"]["open_interest_candlestick"]
    assert chart["chart_type"] == "candlestick"
    assert chart["ohlc_contract"]["native_provider_ohlc"] is True
    assert chart["ohlc_contract"]["hmi_must_reconstruct_ohlc"] is False
    one_hour = chart["markets"]["all_exchanges"]["timeframes"]["1h"]
    assert one_hour["records"]
    assert set(one_hour["records"][0]) == {"timestamp", "open", "high", "low", "close", "market_type"}
    assert set(one_hour["overlays"]["moving_averages"]["series"]) == {
        "ema_9", "ema_21", "ema_50", "sma_20", "sma_50", "sma_100", "sma_200", "wma_20", "wma_50"
    }
    assert set(one_hour["overlays"]["regression_channel"]["series"]) == {"upper", "middle", "lower"}
    assert set(out["charts"]["tsi"]["markets"]["all_exchanges"]["1h"]["series"]) == {"tsi", "signal"}
    assert out["history_contract"]["hmi_recalculation"] is False


def test_runtime_events_use_final_screen_contract_only():
    out = _screen()
    groups = {event["event_group"] for event in out["events"]["by_id"].values()}
    assert groups <= {"moving_average_cross", "channel_cross", "macd_cross", "adx_cross", "stochastic_cross"}
    assert "moving_average_cross" in groups
    assert "macd_cross" in groups
    assert "adx_cross" in groups
    assert "stochastic_cross" in groups
    for uid, event in out["events"]["by_id"].items():
        assert uid == event["event_uid"]
        assert event["source"]["market"] == "all_exchanges"
        assert event["source"]["timeframe"] in HMI_TFS
        assert event["event_type"] == "technical_cross"


def test_exact_recursive_sp_shape_and_strict_json():
    out = _screen()
    sp_path = Path(__file__).parents[1] / "src" / "processing_signals" / "classification" / "open_interest_and_funding" / "open_interest_and_funding_screen_sp_v1_11.json"
    sp = json.loads(sp_path.read_text(encoding="utf-8"))
    assert _shape(out) == _shape(sp)
    json.dumps(out, ensure_ascii=False, allow_nan=False)
