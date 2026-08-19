from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from processing_signals.processing.cvd_volume_orderflow.cvd_volume_orderflow_feature_builder import (
    CvdVolumeOrderflowFeatureBuilder, apply_rolling_features, build_cvd_bars, build_fixed_window_summary,
    build_price_vs_vwap, resample_records, validate_base_records,
)
from processing_signals.processing.cvd_volume_orderflow.cvd_volume_orderflow_processor import process_cvd_volume_orderflow

START = 1_728_000_000
ROOT  = Path(__file__).resolve().parents[1]


def input_row(timestamp: int, buy: float = 10.0, sell: float = 6.0, provider: float = 999_999.0) -> dict:
    return {"timestamp": timestamp, "taker_buy_volume_usd": buy, "taker_sell_volume_usd": sell, "provider_cvd_usd": provider}


def rows(count: int, interval: int, *, start: int = START, buy: float = 10.0, sell: float = 6.0) -> list[dict]:
    return [input_row(start + index * interval, buy, sell, provider=100_000 + index) for index in range(count)]


def footprint(*, scoped: bool = False, quote_multiplier: float = 2.0) -> dict:
    record = {"timestamp": START + 2_000_000, "levels": [{"taker_buy_volume_base": 2.0, "taker_sell_volume_base": 3.0,
        "taker_buy_volume_quote": 4.0 * quote_multiplier, "taker_sell_volume_quote": 6.0 * quote_multiplier}]}
    if scoped:
        record.update(exchange="Binance", source_timeframe="1m", provenance={"provider": "coinglass"})
    return {"status": "available", "records": [record]}


def input_contract(*, one_minute: list[dict] | None = None, fifteen_minute: list[dict] | None = None,
                   futures_one_minute: list[dict] | None = None, futures_fifteen_minute: list[dict] | None = None,
                   mode: str = "bootstrap", scoped_footprint: bool = False) -> dict:
    one = rows(25, 60) if one_minute is None else one_minute
    fifteen = rows(96, 900) if fifteen_minute is None else fifteen_minute
    futures_one = copy.deepcopy(one if futures_one_minute is None else futures_one_minute)
    futures_fifteen = copy.deepcopy(fifteen if futures_fifteen_minute is None else futures_fifteen_minute)

    def market(one_records, fifteen_records, multiplier):
        return {"cvd": {"timeframes": {"1m": {"status": "available", "records": one_records, "gaps": []},
            "15m": {"status": "available", "records": fifteen_records, "gaps": []}}},
            "footprint": footprint(scoped=scoped_footprint, quote_multiplier=multiplier), "confirmations": {}}

    return {"family": "cvd_volume_orderflow", "stage": "input", "mode": mode,
        "context": {"base_asset": "BTC", "pair_symbol": "BTCUSDT", "data_mode": "synthetic", "is_demo": True,
            "reference_timestamp": START + 2_000_000, "requested_at": "2024-10-24T00:00:00Z", "execution_timestamp": START + 2_000_000},
        "markets": {"spot": market(one, fifteen, 2.0), "futures": market(futures_one, futures_fifteen, 3.0)},
        "readiness": {}, "quality": {"status": "ok"}}


def process(contract=None, **kwargs):
    return process_cvd_volume_orderflow(contract or input_contract(), clock=lambda: START + 3_000_000, **kwargs)


@pytest.mark.parametrize(("field", "value", "reason"), [("family", "wrong", "family"), ("stage", "wrong", "stage")])
def test_rejects_wrong_family_and_stage(field, value, reason):
    contract = input_contract()
    contract[field] = value
    with pytest.raises(ValueError, match=reason):
        process(contract)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_rejects_nan_infinity_and_bool(value):
    contract = input_contract()
    contract["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"][0]["taker_buy_volume_usd"] = value
    with pytest.raises(ValueError, match="buy_volume"):
        process(contract)


def test_input_is_immutable_and_output_is_strict_json_deterministic():
    contract = input_contract()
    before = copy.deepcopy(contract)
    first, second = process(contract), process(contract)
    assert contract == before
    assert first == second
    assert json.loads(json.dumps(first, allow_nan=False))["context"]["processing_timestamp"] == START + 3_000_000


def test_base_market_math_ratio_shares_imbalance_and_provider_reference():
    output = process(input_contract(one_minute=[input_row(START, 9, 3)], fifteen_minute=[input_row(START, 9, 3)]))
    bar = output["markets"]["spot"]["timeframes"]["1m"]["records"][0]
    assert (bar["taker_buy_volume_usd"], bar["taker_sell_volume_usd"], bar["total_volume_usd"], bar["volume_delta_usd"]) == (9, 3, 12, 6)
    assert (bar["buy_sell_ratio"]["value"], bar["buy_share"]["value"], bar["sell_share"]["value"]) == (3, .75, .25)
    assert bar["order_flow_imbalance"]["value"] == .5
    assert bar["provider_cvd_reference_usd"] == 999_999
    assert bar["cvd_ohlc_usd"]["close"] == 6


def test_zero_sell_and_zero_total_have_frozen_reasons():
    positive = process(input_contract(one_minute=[input_row(START, 5, 0)], fifteen_minute=[input_row(START, 5, 0)]))
    bar = positive["markets"]["spot"]["timeframes"]["1m"]["current"]
    assert bar["buy_sell_ratio"] == {"value": None, "status": "unavailable", "reason": "sell_volume_zero"}
    zero = process(input_contract(one_minute=[input_row(START, 0, 0)], fifteen_minute=[input_row(START, 0, 0)]))
    bar = zero["markets"]["spot"]["timeframes"]["1m"]["current"]
    assert bar["buy_share"]["reason"] == bar["sell_share"]["reason"] == bar["order_flow_imbalance"]["reason"] == "zero_total_volume"


def test_negative_imbalance_is_bounded():
    output = process(input_contract(one_minute=[input_row(START, 1, 9)], fifteen_minute=[input_row(START, 1, 9)]))
    value = output["markets"]["spot"]["timeframes"]["1m"]["current"]["order_flow_imbalance"]["value"]
    assert value == -.8
    assert -1 <= value <= 1


@pytest.mark.parametrize(("target", "count", "interval", "expected"),
    [("5m", 5, 60, 5), ("1h", 4, 900, 4), ("4h", 16, 900, 16), ("1d", 96, 900, 96)])
def test_resampling_uses_frozen_source_factor_and_volume_sums(target, count, interval, expected):
    source = "1m" if target == "5m" else "15m"
    normalized = validate_base_records(rows(count, interval, buy=2, sell=1))
    result = resample_records(normalized, source, target)
    assert len(result) == 1
    assert result[0]["source_records_expected"] == result[0]["source_records_used"] == expected
    assert result[0]["coverage_complete"] is True
    assert (result[0]["taker_buy_volume_usd"], result[0]["taker_sell_volume_usd"], result[0]["volume_delta_usd"]) == (2 * count, count, count)
    assert result[0]["provider_cvd_reference_usd"] is None
    if target == "1d":
        assert result[0]["timestamp"] % 86400 == 0


def test_incomplete_bucket_is_retained_partial_without_filling():
    output = process(input_contract(one_minute=rows(4, 60), fifteen_minute=rows(4, 900)))
    five = output["markets"]["spot"]["timeframes"]["5m"]
    assert len(five["records"]) == 1
    assert five["current"]["source_records_used"] == 4
    assert five["current"]["coverage_complete"] is False
    assert five["status"] == "partial"


def test_cvd_bootstrap_ohlc_path_and_invariants():
    normalized = validate_base_records([input_row(START, 5, 1), input_row(START + 60, 1, 4), input_row(START + 120, 7, 2),
        input_row(START + 180, 1, 3), input_row(START + 240, 4, 3)])
    aggregated = resample_records(normalized, "1m", "5m")
    bars, breaks, anchor = build_cvd_bars(aggregated, "5m")
    bar = bars[0]
    assert breaks == []
    assert anchor["anchor_value_usd"] == 0
    assert bar["cvd_ohlc_usd"] == {"open": 0.0, "high": 6.0, "low": 0.0, "close": 5.0}
    assert bar["cvd_ohlc_usd"]["close"] == bar["cvd_ohlc_usd"]["open"] + bar["volume_delta_usd"]
    assert bar["cvd_ohlc_usd"]["low"] <= min(bar["cvd_ohlc_usd"]["open"], bar["cvd_ohlc_usd"]["close"])
    assert bar["cvd_ohlc_usd"]["high"] >= max(bar["cvd_ohlc_usd"]["open"], bar["cvd_ohlc_usd"]["close"])


def test_gap_breaks_continuity_and_recovery_restores_it():
    gapped = [input_row(START), input_row(START + 120)]
    broken = process(input_contract(one_minute=gapped, fifteen_minute=[input_row(START)]))
    series = broken["markets"]["spot"]["timeframes"]["1m"]
    assert series["current"]["continuity_status"] == "broken"
    assert series["continuity_breaks"] == [{"after_timestamp": START, "before_timestamp": START + 120, "missing_records": 1}]
    recovered = process(input_contract(one_minute=[input_row(START), input_row(START + 60), input_row(START + 120)],
        fifteen_minute=[input_row(START)], mode="recovery"))
    assert recovered["markets"]["spot"]["timeframes"]["1m"]["current"]["continuity_status"] == "complete"
    assert recovered["markets"]["spot"]["timeframes"]["1m"]["continuity_breaks"] == []


def test_declared_input_gap_breaks_only_affected_market():
    contract = input_contract()
    contract["markets"]["spot"]["cvd"]["timeframes"]["1m"]["gaps"] = [{
        "previous_timestamp": START, "next_timestamp": START + 120, "missing_records": 1}]
    output = process(contract)
    assert output["markets"]["spot"]["timeframes"]["1m"]["current"]["continuity_status"] == "broken"
    assert output["markets"]["futures"]["timeframes"]["1m"]["current"]["continuity_status"] == "complete"


def test_partial_bucket_breaks_continuity_immediately():
    output = process(input_contract(one_minute=rows(4, 60), fifteen_minute=rows(4, 900)))
    five = output["markets"]["spot"]["timeframes"]["5m"]
    assert five["current"]["is_partial"] is True
    assert five["current"]["continuity_status"] == "broken"
    assert five["current"]["reason"] == "cvd_continuity_broken_by_missing_intervals"
    assert five["continuity_break_count"] == 1


def test_future_records_are_rejected_before_summaries():
    contract = input_contract(fifteen_minute=rows(4, 900))
    contract["markets"]["spot"]["cvd"]["timeframes"]["15m"]["records"].append(
        input_row(contract["context"]["reference_timestamp"] + 900, 1000, 0))
    with pytest.raises(ValueError, match="timestamp_after_reference_timestamp"):
        process(contract)


def test_delta_ma_and_flow_efficiency_warmup_and_bar_21():
    output = process(input_contract(one_minute=rows(21, 60, buy=3, sell=1), fifteen_minute=rows(21, 900, buy=3, sell=1)))
    records = output["markets"]["spot"]["timeframes"]["1m"]["records"]
    assert records[19]["delta_ma_21_usd"] is None
    assert records[20]["delta_ma_21_usd"] == 2
    assert records[20]["flow_efficiency"] == {"value": 1.0, "status": "available", "reason": None}
    assert records[20]["flow_efficiency"]["value"] != abs(records[20]["order_flow_imbalance"]["value"])


def test_rolling_features_restart_after_gap():
    base = validate_base_records(rows(21, 60) + rows(21, 60, start=START + 22 * 60))
    resampled = resample_records(base, "1m", "1m")
    bars, _, _ = build_cvd_bars(resampled, "1m")
    rolled = apply_rolling_features(bars)
    assert rolled[20]["delta_ma_21_usd"] == 4
    assert rolled[21]["delta_ma_21_usd"] is None
    assert rolled[-1]["delta_ma_21_usd"] is None


def test_fixed_window_summaries_have_exact_factors_and_partial_reason():
    records = process(input_contract(fifteen_minute=rows(96, 900)))["markets"]["spot"]["timeframes"]["15m"]["records"]
    one_hour, day = build_fixed_window_summary(records, "1h"), build_fixed_window_summary(records, "24h")
    assert (one_hour["records_expected"], one_hour["records_used"], one_hour["volume_delta_usd"]) == (4, 4, 16)
    assert (day["records_expected"], day["records_used"], day["volume_delta_usd"]) == (96, 96, 384)
    partial = build_fixed_window_summary(records[:3], "1h")
    assert (partial["status"], partial["reason"], partial["records_used"]) == ("partial", "incomplete_fixed_window", 3)


def test_current_is_latest_even_during_warmup():
    output = process()
    payload = output["markets"]["spot"]["timeframes"]["1m"]
    assert payload["current_timestamp"] == payload["records"][-1]["timestamp"]
    assert payload["current"] == payload["records"][-1]
    assert payload["current"]["delta_ma_21_usd"] == 4


def test_historical_replacement_recomputes_all_later_cvd_closes():
    original = input_contract(one_minute=rows(3, 60), fifteen_minute=[input_row(START)])
    first = process(original)
    changed = copy.deepcopy(original)
    changed["mode"] = "incremental"
    changed["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"][1]["taker_buy_volume_usd"] += 10
    second = process(changed)
    before = [row["cvd_ohlc_usd"]["close"] for row in first["markets"]["spot"]["timeframes"]["1m"]["records"]]
    after = [row["cvd_ohlc_usd"]["close"] for row in second["markets"]["spot"]["timeframes"]["1m"]["records"]]
    assert after == [before[0], before[1] + 10, before[2] + 10]
    assert second["parameters"]["recalculation_policy"] == "full_history_deterministic_rebuild"


def test_footprint_vwap_scope():
    builder = CvdVolumeOrderflowFeatureBuilder()
    spot = builder.build_footprint_vwap(footprint(scoped=False, quote_multiplier=2))
    futures = builder.build_footprint_vwap(footprint(scoped=False, quote_multiplier=3))
    assert spot["vwap_usd"] == 4
    assert (spot["status"], spot["reason"]) == ("partial", "footprint_exchange_or_timeframe_scope_not_preserved")
    empty = builder.build_footprint_vwap({"records": []})
    assert (empty["status"], empty["reason"]) == ("unavailable", "footprint_data_not_available")


def test_price_vs_vwap_with_and_without_reference():
    vwap = {"vwap_usd": 100.0}
    assert build_price_vs_vwap(vwap, {"timestamp": 1, "price_usd": 110})["value"] == pytest.approx(.1)
    assert build_price_vs_vwap(vwap, None)["reason"] == "price_reference_not_provided"
    output = process(price_reference_by_market={"spot": {"timestamp": 1, "price_usd": 10}, "futures": {"timestamp": 1, "price_usd": 10}})
    assert set(output["markets"]) == {"spot", "futures"}


def test_contract_shape_absence_of_classification_and_full_history():
    contract = input_contract()
    output = process(contract)
    assert (output["family"], output["stage"], output["version"]) == ("cvd_volume_orderflow", "processing", "0.1.0")
    assert set(output["markets"]) == {"spot", "futures"}
    assert set(output["markets"]["spot"]["timeframes"]) == {"1m", "5m", "15m", "1h", "4h", "1d"}
    assert len(output["markets"]["spot"]["timeframes"]["1m"]["records"]) == len(contract["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"])
    encoded = json.dumps(output, allow_nan=False).lower()
    for forbidden in ('"classification"', '"kpis"', '"charts"', '"widgets"', '"screen"'):
        assert forbidden not in encoded


def test_availability_partial_unavailable_and_quality_partial():
    partial = process()
    assert partial["markets"]["spot"]["timeframes"]["1d"]["status"] == "partial"
    assert partial["quality"]["status"] == "partial"
    empty = process(input_contract(one_minute=[], fifteen_minute=[], futures_one_minute=[], futures_fifteen_minute=[]))
    assert empty["markets"]["spot"]["timeframes"]["1m"] == {**empty["markets"]["spot"]["timeframes"]["1m"],
        "status": "unavailable", "reason": "no_records"}
    assert empty["quality"]["core_status"] == "invalid"
    assert empty["quality"]["status"] == "invalid"


def test_quality_ok_with_complete_core_scoped_enrichment_and_prices():
    contract = input_contract(one_minute=rows(105, 60), fifteen_minute=rows(2016, 900), scoped_footprint=True)
    refs = {market: {"timestamp": START + 3_000_000, "price_usd": 10} for market in ("spot", "futures")}
    output = process(contract, price_reference_by_market=refs)
    assert all(output["markets"][market]["timeframes"][timeframe]["status"] == "available"
        for market in ("spot", "futures") for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"))
    assert output["quality"] == {"status": "ok", "core_status": "available", "enrichment_status": "available", "warnings": [], "errors": []}


def test_input_quality_invalid_produces_invalid_processing_quality():
    contract = input_contract()
    contract["quality"] = {"status": "invalid"}
    output = process(contract)
    assert output["quality"]["status"] == "invalid"
    assert output["quality"]["core_status"] == "invalid"


def test_bootstrap_incremental_recovery_modes_are_preserved():
    for mode in ("bootstrap", "incremental", "recovery"):
        assert process(input_contract(mode=mode))["mode"] == mode


def test_family_is_not_registered_in_processing_pipeline():
    pipeline = ROOT / "src/processing_signals/processing_pipeline.py"
    if pipeline.exists():
        assert "cvd_volume_orderflow" not in pipeline.read_text(encoding="utf-8")
