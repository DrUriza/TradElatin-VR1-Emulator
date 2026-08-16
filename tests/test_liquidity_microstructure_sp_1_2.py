from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from processing_signals.main.liquidity_microstructure.liquidity_microstructure_vertical import (
    run_liquidity_microstructure_vertical,
)
from processing_signals.runtime.source_router import build_provider_router


REFERENCE_TIMESTAMP = 1_786_150_800
SP_PATH = (
    Path(__file__).parents[1]
    / "src/processing_signals/classification/liquidity_microstructure/liquidity_microstructure_screen_sp_v1_2.json"
)


@lru_cache(maxsize=1)
def _vertical() -> dict[str, Any]:
    router = build_provider_router("emulator")
    return run_liquidity_microstructure_vertical(
        fetcher=router.for_family("liquidity_microstructure"),
        runtime_context={
            "data_mode": "synthetic",
            "is_demo": True,
            "generated_at": "2026-08-08T01:00:00Z",
            "updated_at": "2026-08-08T01:00:00Z",
            "connection_status": "not_reported",
            "cache_status": "not_reported",
            "latency_ms": None,
            "refresh_interval_seconds": None,
            "cache_ttl_seconds": None,
        },
        input_arguments={
            "reference_timestamp": REFERENCE_TIMESTAMP,
            "execution_timestamp": REFERENCE_TIMESTAMP,
            "history_limit": 1000,
        },
        processing_arguments={"now_timestamp": REFERENCE_TIMESTAMP},
        classification_arguments={"now_timestamp": REFERENCE_TIMESTAMP},
        selected_market="perpetual",
        selected_timeframe="1m",
    )


def _item_shapes(items: list[Any]) -> set[tuple[str, ...]]:
    return {tuple(sorted(item)) for item in items if isinstance(item, dict)}


def _assert_topology(reference: Any, candidate: Any, path: str = "$") -> None:
    if isinstance(reference, dict):
        assert isinstance(candidate, dict), f"{path}: expected object"
        assert set(candidate) == set(reference), f"{path}: key mismatch"
        for key in reference:
            _assert_topology(reference[key], candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), f"{path}: expected list"
        # Runtime may honestly have no observations for an optional provider
        # series (notably market_history).  When records exist, their shape must
        # still be one of the frozen SP shapes.
        if reference and candidate and any(isinstance(item, dict) for item in reference):
            assert _item_shapes(candidate) <= _item_shapes(reference), f"{path}: undocumented item shape"
        return


def test_liquidity_sp_1_2_root_selector_layout_and_kpis() -> None:
    screen = _vertical()["screen_contract"]
    assert screen["schema"] == {
        "id": "trad_elatin.liquidity_microstructure.screen.v1",
        "version": "1.2.0",
    }
    assert screen["screen"]["id"] == "liquidity_microstructure"
    assert set(screen["selectors"]) == {"market"}
    assert [item["metric_id"] for item in screen["kpis"]["items"]] == [
        "bid_depth",
        "ask_depth",
        "spread",
        "liquidity_imbalance",
        "mid_price",
        "impact_1_btc",
    ]
    assert screen["layout"]["type"] == "three_column_chart_table_grid"


def test_liquidity_real_provider_semantics_for_three_main_panels() -> None:
    result = _vertical()
    processing = result["processing"]
    screen = result["screen_contract"]
    source = processing["source_selection"]

    assert source["orderbook_perpetual"]["provider"] == "coinglass"
    assert source["whale_orders_perpetual"]["provider"] == "coinglass"
    assert source["whale_orders_perpetual"]["role"] == "large_limit_orders"
    assert source["large_trades_perpetual"]["provider"] == "coinglass"
    assert source["large_trades_perpetual"]["role"] == "executed_footprint"

    # The HMI keeps its frozen contract chart/table ids, but their data is not a
    # fabricated trade tape: whale liquidity comes from Large Limit Order and
    # executed liquidity comes from Footprint price bins.
    assert screen["charts"]["whale_liquidity_profile"]["metadata"]["note"] == "large_limit_order_liquidity"
    assert screen["charts"]["executed_liquidity_profile"]["metadata"]["note"] == "executed_footprint_price_bins"
    assert screen["charts"]["large_trades_flow"]["metadata"]["window_semantics"] == "footprint_price_bins"
    assert screen["tables"]["large_trades"]["metadata"]["side_semantics"] == "taker_buy_sell_footprint"


def test_liquidity_dense_runtime_data_matches_final_screen_geometry() -> None:
    screen = _vertical()["screen_contract"]
    order = screen["charts"]["order_depth"]
    whales = screen["charts"]["whale_liquidity_profile"]
    executed = screen["charts"]["executed_liquidity_profile"]

    assert len(order["records"]) == 100
    assert order["metadata"]["bid_level_count"] == 50
    assert order["metadata"]["ask_level_count"] == 50
    assert len(screen["tables"]["orderbook_snapshot"]["bids"]) == 50
    assert len(screen["tables"]["orderbook_snapshot"]["asks"]) == 50

    assert len(whales["records"]) == 60
    assert len(screen["tables"]["whale_orders"]["rows"]) == 60
    assert len(executed["records"]) == 120
    assert len(screen["charts"]["large_trades_flow"]["items"]) == 120
    assert len(screen["tables"]["large_trades"]["rows"]) == 120


def test_liquidity_history_is_processing_owned_and_quality_is_honest() -> None:
    screen = _vertical()["screen_contract"]
    history = screen["history_contract"]
    assert history["calculation_records"] == 730
    assert history["minimum_warmup_records"] == 200
    assert history["technical_indicators_precomputed"] is True
    assert history["hmi_recalculation"] is False
    assert len(screen["charts"]["whale_activity"]["calculation_history"]["records"]) == 730

    # The emulator intentionally has no independent market-history source.
    # Do not synthesize it just to make global quality green.
    assert screen["charts"]["market_history"]["status"] == "unavailable"
    assert screen["quality"]["status"] == "partial"
    assert screen["quality"]["contract_complete"] is True


def test_liquidity_runtime_contract_matches_final_sp_topology_and_strict_json() -> None:
    reference = json.loads(SP_PATH.read_text(encoding="utf-8"))
    screen = _vertical()["screen_contract"]
    _assert_topology(reference, screen)
    json.dumps(screen, ensure_ascii=False, allow_nan=False)
