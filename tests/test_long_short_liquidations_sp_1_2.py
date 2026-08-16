from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from processing_signals.main.runtime_orchestrator import run_all

SP = Path(__file__).resolve().parents[1] / "src/processing_signals/classification/long_short_liquidations/long_short_liquidations_screen_sp_v1_2.json"


def _screen() -> dict[str, Any]:
    return run_all(source="emulator", enabled_families=("prices_ohlcv", "long_short_liquidations"))["hmi_contract"]["long_short_liquidations"]


def _assert_sp_shape(reference: Any, candidate: Any, path: str = "root") -> None:
    if isinstance(reference, dict):
        assert isinstance(candidate, dict), f"{path}: expected mapping"
        assert list(candidate) == list(reference), f"{path}: key/order mismatch"
        for key, ref_value in reference.items():
            _assert_sp_shape(ref_value, candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), f"{path}: expected list"
        if not reference or not candidate:
            return
        if all(not isinstance(item, (dict, list)) for item in reference):
            return
        identity_keys = ("id", "provider", "series_id", "exchange", "badge_id")
        for index, item in enumerate(candidate):
            ref_item = None
            if isinstance(item, dict):
                for identity_key in identity_keys:
                    if identity_key not in item:
                        continue
                    ref_item = next(
                        (entry for entry in reference if isinstance(entry, dict)
                         and entry.get(identity_key) == item.get(identity_key)),
                        None,
                    )
                    if ref_item is not None:
                        break
            if ref_item is None:
                ref_item = reference[index] if index < len(reference) else reference[0]
            _assert_sp_shape(ref_item, item, f"{path}[{index}]")
        return
    # Scalar values are dynamic runtime data. The frozen SP controls their key position,
    # not whether a particular run is null/int/float/string.


def test_sp_v1_2_identity_and_root_shape():
    screen = _screen()
    reference = json.loads(SP.read_text(encoding="utf-8"))
    assert screen["contract_version"] == "1.2.0"
    assert screen["stage"] == "screen_contract_final"
    assert screen["family"] == screen["screen_id"] == "long_short_liquidations"
    assert list(screen) == list(reference)
    _assert_sp_shape(reference, screen)


def test_sp_v1_2_maps_are_real_coinglass_contracts():
    screen = _screen()
    charts = screen["charts"]
    assert charts["aggregate_map"]["status"] == "available"
    assert charts["aggregate_map"]["provider"] == "coinglass"
    assert charts["aggregate_map"]["provenance"]["endpoint_id"] == "aggregated_liquidation_map"
    assert [row["series_id"] for row in charts["aggregate_map"]["bar_series"]] == ["binance", "okx", "bybit"]
    assert charts["hyperliquid_map"]["status"] == "available"
    assert charts["hyperliquid_map"]["proxy"] is False
    assert charts["hyperliquid_map"]["provenance"]["provider"] == "coinglass"
    assert charts["binance_leverage_map"]["status"] == "available"
    assert charts["binance_leverage_map"]["provenance"]["provider"] == "coinglass"
    assert [row["series_id"] for row in charts["binance_leverage_map"]["bar_series"]] == ["10x", "25x", "50x", "100x"]
    # CoinGlass documents these map values as liquidation levels, not realized liquidation USD.
    assert all(chart["unit"] == "provider_level" for chart in charts.values())
    assert all(chart["visual_contract"]["hmi_calculation"] is False for chart in charts.values())


def test_sp_v1_2_source_selection_and_confirmations():
    screen = _screen()
    selection = screen["source_selection"]
    assert selection["realized_aggregate"]["provider"] == "coinglass" and selection["realized_aggregate"]["selected"] is True
    assert selection["aggregated_map"]["provider"] == "coinglass" and selection["aggregated_map"]["selected"] is True
    assert selection["exchange_maps"]["provider"] == "coinglass" and selection["exchange_maps"]["selected"] is True
    assert selection["exchange_maps"]["role"] == "canonical"
    assert selection["glassnode_long_confirmation"]["selected"] is True
    assert selection["glassnode_short_confirmation"]["selected"] is True
    assert selection["glassnode_total_confirmation"]["selected"] is True
    rows = {row["provider"]: row for row in screen["tables"]["provider_confirmations"]["rows"]}
    assert rows["glassnode"]["status"] == "available"
    assert rows["cryptoquant"]["status"] == "available"


def test_sp_v1_2_hmi_contract_has_no_temporal_selector_or_recalculation():
    screen = _screen()
    assert set(screen["selectors"]) == {"exchange", "map"}
    assert screen["selectors"]["map"]["options"][1]["id"] == "hyperliquid"
    assert screen["selectors"]["map"]["options"][1]["enabled"] is True
    assert screen["history_contract"]["hmi_recalculation"] is False
    assert screen["quality"]["visual_fixture"]["hmi_calculation"] is False
    assert screen["calculation_history"]["record_count"] == len(screen["calculation_history"]["records"])
    assert screen["calculation_history"]["resolution"] == "1h"


def test_sp_v1_2_kpis_side_panel_and_json_are_runtime_ready():
    screen = _screen()
    assert [item["id"] for item in screen["kpis"]] == [
        "current_price", "total_liquidations_24h", "long_liquidations_24h",
        "short_liquidations_24h", "realized_imbalance_24h", "realized_side_24h", "pressure_score",
    ]
    assert [item["id"] for item in screen["side_panel"]["items"]] == [
        "pressure_score", "pressure_label", "dominant_side", "side_imbalance",
        "top_exchange_concentration", "max_event_spike", "max_single_liquidation",
        "nearest_long_cluster", "nearest_short_cluster",
    ]
    assert all(item["unit"] == "M USD" for item in screen["kpis"] if item["id"] in {
        "total_liquidations_24h", "long_liquidations_24h", "short_liquidations_24h"
    })
    assert all(row["exchange_key"] != "all" for row in screen["tables"]["exchange_distribution"]["rows"])
    json.dumps(screen, ensure_ascii=False, allow_nan=False)
