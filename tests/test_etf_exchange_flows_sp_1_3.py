from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from processing_signals.main.etf_exchange_flows.etf_exchange_flows_vertical import run_etf_exchange_flows_vertical
from processing_signals.runtime.source_router import build_provider_router


REFERENCE_TIMESTAMP = 1_786_147_200
SP_PATH = Path(__file__).parents[1] / "src/processing_signals/classification/etf_exchange_flows/etf_exchange_flows_screen_sp_v1_3.json"


@lru_cache(maxsize=1)
def _vertical() -> dict[str, Any]:
    router = build_provider_router("emulator")
    return run_etf_exchange_flows_vertical(
        fetcher=router.for_family("etf_exchange_flows"),
        input_arguments={
            "requested_mode": "bootstrap",
            "include_secondary": True,
            "data_mode": "synthetic",
            "is_demo": True,
            "exchange_scope": "all_exchange",
            "symbol": "BTC",
            "now": REFERENCE_TIMESTAMP,
        },
        contract_arguments={"selected_range": "30d", "generated_at": "2026-08-08T00:00:00Z"},
        now_timestamp=REFERENCE_TIMESTAMP,
    )


def _item_shapes(items: list[Any]) -> set[tuple[str, ...]]:
    return {tuple(sorted(item)) for item in items if isinstance(item, dict)}


def _assert_topology(reference: Any, candidate: Any, path: str = "$" ) -> None:
    if isinstance(reference, dict):
        assert isinstance(candidate, dict), f"{path}: expected object"
        assert set(candidate) == set(reference), f"{path}: key mismatch"
        for key in reference:
            _assert_topology(reference[key], candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), f"{path}: expected list"
        if reference and candidate and any(isinstance(item, dict) for item in reference):
            reference_shapes = _item_shapes(reference)
            candidate_shapes = _item_shapes(candidate)
            if path == "$.technical_analysis.events":
                # The final SP fixture contains both a base event shape and an
                # optional diagnostic variant with a ``calculation`` object.
                # Runtime events need not duplicate the same event merely to
                # populate both optional shapes; every emitted shape must be a
                # documented SP shape.
                assert candidate_shapes <= reference_shapes, f"{path}: undocumented event shape"
            else:
                assert candidate_shapes == reference_shapes, f"{path}: item-key shape mismatch"
        return
    # Leaf values are runtime data. Nullable reasons/statuses may legitimately
    # differ from the visual fixture, so topology intentionally stops here.


def test_etf_sp_1_3_root_layout_ranges_and_kpi_order() -> None:
    screen = _vertical()["screen"]
    assert screen["schema"] == {"id": "trad_elatin.etf_exchange_flows.screen.v1", "version": "1.3.0"}
    assert screen["screen"]["id"] == "etf_exchange_flows"
    assert screen["screen"]["layout_contract"]["main_content"] == [
        {"type": "bar", "chart_id": "etf_flow_daily", "technical_analysis_allowed": False},
        {"type": "table", "table_id": "etf_funds", "title": "ETF Flow by Provider"},
        {"type": "bar", "chart_id": "exchange_net_flow", "technical_analysis_allowed": False},
        {"type": "candlestick", "chart_id": "exchange_balance", "technical_analysis_allowed": True},
    ]
    assert [item["id"] for item in screen["range_selector"]["options"]] == ["30d", "90d", "360d"]
    assert list(screen["kpis"]) == [
        "etf_net_flow", "total_aum", "cumulative_etf_net_flow", "exchange_inflow",
        "exchange_outflow", "exchange_balance", "gbtc_premium", "exchange_flow_pressure",
    ]


def test_etf_glassnode_confirmations_and_cumulative_kpi_only() -> None:
    vertical = _vertical()
    processing = vertical["processing"]
    screen = vertical["screen"]
    confirmations = processing["features"]["secondary_confirmations"]["glassnode"]
    assert set(confirmations) == {"etf_net_flow", "exchange_inflow", "exchange_outflow", "exchange_netflow", "exchange_balance"}
    assert all(item["provider"] == "glassnode" for item in confirmations.values())
    assert confirmations["etf_net_flow"]["endpoint_id"] == "us_spot_etf_flows_net"
    assert processing["features"]["provider_reconciliation"]["etf_net_flow"]["secondary_provider"] == "glassnode"
    assert "cumulative_etf_net_flow" in screen["kpis"]
    assert "etf_cumulative_net_flow" not in screen["charts"]
    assert all(chart.get("chart_id") != "etf_cumulative_net_flow" for chart in screen["charts"].values())


def test_etf_exchange_balance_candle_is_processing_owned_and_hmi_never_rebuilds() -> None:
    screen = _vertical()["screen"]
    chart = screen["charts"]["exchange_balance"]
    assert chart["chart_type"] == "candlestick"
    assert len(chart["candles"]) == 730
    assert chart["candle_count"] == 730
    assert chart["ohlc_contract"]["construction_stage"] == "processing"
    assert chart["ohlc_contract"]["native_provider_ohlc"] is False
    assert chart["ohlc_contract"]["hmi_must_reconstruct_ohlc"] is False
    assert chart["ohlc_contract"]["line_fallback_allowed"] is False
    assert chart["calculation_history"]["recalculate_in_hmi"] is False
    assert screen["provenance"]["providers"]["primary"]["exchange_balance_chart"] == ["cryptoquant"]
    assert screen["provenance"]["providers"]["secondary"]["exchange_balance"] == ["glassnode"]
    assert chart["source_points"]
    assert all(point["provider"] == "coinglass" for point in chart["source_points"])


def test_etf_technical_analysis_is_precomputed_and_volume_indicators_are_excluded() -> None:
    screen = _vertical()["screen"]
    analysis = screen["technical_analysis"]
    assert analysis["target_chart_id"] == "exchange_balance"
    assert analysis["recalculate_in_hmi"] is False
    assert analysis["selector_contract"]["excluded"] == ["volume", "mfi"]
    assert set(analysis["overlays"]) == {"moving_averages", "bollinger_bands", "regression_channel"}
    assert set(analysis["indicators"]) == {
        "macd", "rsi", "tsi", "adx", "stochastic", "williams_r", "cci", "atr",
        "wasserstein_distance", "bollinger_band_width",
    }
    assert screen["history_contract"]["technical_indicators_precomputed"] is True
    assert screen["history_contract"]["hmi_recalculation"] is False


def test_etf_runtime_contract_matches_final_sp_key_topology_and_is_strict_json() -> None:
    reference = json.loads(SP_PATH.read_text(encoding="utf-8"))
    screen = _vertical()["screen"]
    _assert_topology(reference, screen)
    json.dumps(screen, ensure_ascii=False, allow_nan=False)
