from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from processing_signals.main.on_chain_miners import run_on_chain_miners_vertical
from processing_signals.runtime.emulator.provider_router import SyntheticProviderRouter

NOW = 1786147140
SP_PATH = Path(__file__).resolve().parents[1] / "src" / "processing_signals" / "classification" / "on_chain_miners" / "on_chain_miners_screen_sp_v1_3.json"


@pytest.fixture(scope="module")
def bundle():
    fetcher = SyntheticProviderRouter().for_family("on_chain_miners")
    return run_on_chain_miners_vertical(
        fetcher=fetcher,
        input_arguments={"requested_mode": "bootstrap", "include_screen_extensions": True, "data_mode": "synthetic", "is_demo": True},
        now_timestamp=NOW,
    )


def _shape_errors(reference, candidate, path="$"):
    errors = []
    if isinstance(reference, dict):
        if not isinstance(candidate, dict):
            return [f"{path}:expected_dict"]
        if tuple(reference) != tuple(candidate):
            errors.append(f"{path}:keys")
        for key in reference:
            if key in candidate:
                errors.extend(_shape_errors(reference[key], candidate[key], f"{path}.{key}"))
        return errors
    if isinstance(reference, list):
        if not isinstance(candidate, list):
            return [f"{path}:expected_list"]
        if not reference or all(not isinstance(x, (dict, list)) for x in reference):
            return errors
        for index, item in enumerate(candidate):
            if not isinstance(item, dict):
                continue
            matches = [ref for ref in reference if isinstance(ref, dict) and tuple(ref) == tuple(item)]
            if not matches:
                errors.append(f"{path}[{index}]:no_reference_shape")
                continue
            refined = [ref for ref in matches if all(
                not isinstance(item.get(key), dict) or not isinstance(ref.get(key), dict) or tuple(item[key]) == tuple(ref[key])
                for key in item if key in ref
            )]
            errors.extend(_shape_errors((refined or matches)[0], item, f"{path}[{index}]"))
    return errors


def _walk(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    else:
        yield value


def test_screen_matches_frozen_sp_1_3_structure(bundle):
    reference = json.loads(SP_PATH.read_text(encoding="utf-8"))
    screen = bundle["screen"]
    assert screen["schema"] == {"id": "trad_elatin.on_chain_miners.screen.v1", "version": "1.3.0"}
    assert _shape_errors(reference, screen) == []
    json.dumps(screen, ensure_ascii=False, allow_nan=False)
    assert all(not isinstance(value, float) or math.isfinite(value) for value in _walk(screen))


def test_four_primary_candles_are_processing_derived_from_glassnode_intraday(bundle):
    screen = bundle["screen"]
    processing = bundle["processing"]
    mapping = {
        "miner_reserve": "miner_reserve_btc",
        "sopr_7d": "sopr_7d",
        "hashrate": "hashrate_eh_s",
        "difficulty": "difficulty_t",
    }
    for chart_id, series_id in mapping.items():
        chart = screen["charts"][chart_id]
        source_candles = processing["series"][series_id]["daily_candles"]
        assert chart["chart_type"] == "candlestick"
        assert chart["preferred_representation"] == "candlestick"
        assert chart["ohlc_contract"]["native_provider_ohlc"] is False
        assert chart["ohlc_contract"]["construction_stage"] == "processing"
        assert chart["ohlc_contract"]["hmi_must_reconstruct_ohlc"] is False
        assert chart["ohlc_contract"]["line_fallback_allowed"] is False
        assert chart["calculation_history"]["point_count"] == len(source_candles) == 730
        assert chart["calculation_history"]["candles"][-1]["close"] == source_candles[-1]["close"]
        assert source_candles[-1]["observation_count"] == 24


def test_miner_net_position_is_direct_glassnode_not_reserve_delta(bundle):
    screen = bundle["screen"]
    processing = bundle["processing"]
    classification = bundle["classification"]
    chart = screen["charts"]["miner_net_position_change"]
    assert chart["provider"] == "glassnode"
    assert chart["source_provider"] == "glassnode"
    assert chart["calculation_source"] == "processing.series.miner_net_position_change.records"
    assert chart["current"]["value"] == processing["series"]["miner_net_position_change"]["current"]["value"]
    assert screen["widgets"]["net_position"]["source"]["feature_id"] == "miner_net_position_change"
    assert classification["classifications"]["net_position"]["reason"].startswith("glassnode_miner_net_position_")


def test_glassnode_is_primary_for_core_onchain_and_mpi_remains_complementary(bundle):
    input_contract = bundle["input"]
    processing = bundle["processing"]
    expected = {
        "miner_reserve": "glassnode",
        "sopr": "glassnode",
        "hashrate": "glassnode",
        "difficulty": "glassnode",
        "miner_net_position_change": "glassnode",
        "miner_outflow_total": "glassnode",
    }
    for metric_id, provider in expected.items():
        assert input_contract["series"][metric_id]["provider"] == provider
    assert input_contract["series"]["mpi"]["provider"] == "cryptoquant"
    assert processing["features"]["mpi_context"]["source_metric_id"] == "mpi"
    assert processing["features"]["miner_pressure_basis"]["source_metric_id"] == "miner_outflow_total_btc"


def test_screen_b_is_precomputed_and_excludes_volume_and_mfi(bundle):
    screen = bundle["screen"]
    processing = bundle["processing"]
    ta = screen["technical_analysis"]
    assert processing["technical_analysis"]["status"] == "available"
    assert ta["recalculate_in_hmi"] is False
    assert ta["selector_contract"]["excluded"] == ["volume", "mfi"]
    expected_indicators = {
        "macd", "rsi", "tsi", "adx", "stochastic", "williams_r", "cci", "atr",
        "wasserstein_distance", "bollinger_band_width",
    }
    for chart_id in ("miner_reserve", "sopr_7d", "hashrate", "difficulty"):
        target = ta["targets_by_chart"][chart_id]
        for range_id in ("30D", "90D", "360D"):
            payload = target["ranges"][range_id]
            assert set(payload["indicators"]) == expected_indicators
            assert set(payload["overlays"]) == {"moving_averages", "bollinger_bands", "regression_channel"}
            assert all(item["recalculate_in_hmi"] is False for item in payload["indicators"].values())


def test_real_drilldown_sources_replace_old_synthetic_proxies(bundle):
    screen = bundle["screen"]
    processing = bundle["processing"]
    outflow = screen["drilldowns"]["miner_outflow_distribution"]
    revenue = screen["drilldowns"]["revenue_breakdown"]
    assert outflow["metadata"]["construction"] == "cryptoquant_entity_pool_outflow_distribution"
    assert revenue["metadata"]["construction"] == "glassnode_total_block_reward_and_fee_revenue_alignment"
    assert processing["series"]["miner_outflow_total_btc"]["current"]["status"] == "available"
    assert processing["features"]["miner_revenue_breakdown"]["current"]["status"] == "available"
    assert screen["history_contract"]["hmi_recalculation"] is False
    assert screen["history_contract"]["technical_indicators_precomputed"] is True
