from __future__ import annotations

import copy
import json

import pytest

from processing_signals.input.cvd_volume_orderflow.cvd_volume_orderflow_data_raw_extract import (
    COINGLASS_CVD_MAX_LIMIT, CvdVolumeOrderflowRawExtractor, build_coinglass_aggregated_cvd_params,
    build_coinglass_footprint_params, build_cryptoquant_taker_params, build_cvd_volume_orderflow_fetch_plan,
    build_glassnode_metric_params, required_base_records,
)
from processing_signals.input.cvd_volume_orderflow.cvd_volume_orderflow_data_raw_preprocessing import (
    _primary_payload, detect_internal_gaps, merge_paginated_records, normalize_coinglass_cvd_record, normalize_footprint_snapshot,
    run_cvd_volume_orderflow_input, upsert_records_by_timestamp,
)

REFERENCE = 2_000_000_000


def cvd_row(timestamp: int, *, buy: float = 10.0, sell: float = 8.0, cvd: float = 2.0) -> dict[str, float | int]:
    return {"time": timestamp * 1000, "agg_taker_buy_vol": buy, "agg_taker_sell_vol": sell, "cum_vol_delta": cvd}


def response_for(provider: str, endpoint_id: str, params: dict) -> object:
    if provider == "coinglass" and "aggregated_cvd" in endpoint_id:
        interval = 60 if params["interval"] == "1m" else 900
        end = params.get("end_time", REFERENCE * 1000) // 1000
        count = params["limit"]
        return {"code": "0", "data": [cvd_row(end - offset * interval) for offset in range(count)]}
    if provider == "coinglass":
        return {"code": "0", "data": [[REFERENCE, [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]]]}
    if provider == "cryptoquant":
        return {"status": {"code": 200, "message": "success"}, "result": {"window": "hour", "data": [{
            "date": "2033-05-18T03:33:20Z", "taker_buy_volume": 10, "taker_sell_volume": 9,
            "taker_buy_ratio": .52, "taker_sell_ratio": .48, "taker_buy_sell_ratio": 1.1}]}}
    return [{"t": REFERENCE, "v": 7}]


def fetcher(*, provider: str, endpoint_id: str, path: str, params: dict) -> object:
    del path
    return response_for(provider, endpoint_id, params)


def small_input(**overrides):
    options = {"fetcher": fetcher, "reference_timestamp": REFERENCE, "clock": lambda: REFERENCE,
        "target_display_records": 1, "warmup_records": 0, "include_footprint": False,
        "include_cryptoquant_confirmation": False, "include_glassnode_confirmation": False}
    options.update(overrides)
    return run_cvd_volume_orderflow_input(**options)


def test_fetch_plan_bootstrap_keeps_1m_15m_but_incremental_pays_only_1m():
    bootstrap = build_cvd_volume_orderflow_fetch_plan(mode="bootstrap", reference_timestamp=REFERENCE, include_footprint=False,
        include_cryptoquant_confirmation=False, include_glassnode_confirmation=False)
    assert [(item["market"], item["timeframe"]) for item in bootstrap] == [
        ("spot", "1m"), ("spot", "15m"), ("futures", "1m"), ("futures", "15m")]
    incremental = build_cvd_volume_orderflow_fetch_plan(mode="incremental", reference_timestamp=REFERENCE, include_footprint=False,
        include_cryptoquant_confirmation=False, include_glassnode_confirmation=False)
    assert [(item["market"], item["timeframe"]) for item in incremental] == [("spot", "1m"), ("futures", "1m")]


def test_incremental_skips_secondary_sources_unless_explicitly_refreshed():
    regular = build_cvd_volume_orderflow_fetch_plan(mode="incremental", reference_timestamp=REFERENCE)
    assert len(regular) == 2
    assert {item["dataset"] for item in regular} == {"aggregated_cvd"}

    refreshed = build_cvd_volume_orderflow_fetch_plan(
        mode="incremental", reference_timestamp=REFERENCE, refresh_secondary=True
    )
    assert len(refreshed) == 13
    assert sum(item["dataset"] == "footprint" for item in refreshed) == 6
    assert sum(item["provider"] == "cryptoquant" for item in refreshed) == 1
    assert sum(item["provider"] == "glassnode" for item in refreshed) == 4


def test_required_history_and_provider_params_are_frozen():
    assert required_base_records("1m") == 1260
    assert required_base_records("15m") == 24192
    assert build_coinglass_aggregated_cvd_params(exchanges=("Binance", "OKX", "Bybit"), symbol="BTC", timeframe="1m", limit=4500) == {
        "exchange_list": "Binance,OKX,Bybit", "symbol": "BTC", "interval": "1m", "limit": 4500, "unit": "usd"}
    assert build_coinglass_footprint_params(exchange="Binance", symbol="BTCUSDT")["symbol"] == "BTCUSDT"
    assert build_cryptoquant_taker_params(window="hour", limit=5, start_timestamp=0, end_timestamp=1)["from"] == "19700101T000000"
    assert build_glassnode_metric_params(start_timestamp=1, end_timestamp=2) == {
        "a": "BTC", "i": "1h", "c": "USD", "f": "json", "timestamp_format": "unix", "s": 1, "u": 2}


def test_raw_deep_copies_response_and_uses_one_deterministic_clock_value():
    source = {"code": "0", "data": [cvd_row(REFERENCE)]}

    def mutable_fetcher(**kwargs):
        del kwargs
        return source

    raw = CvdVolumeOrderflowRawExtractor(mutable_fetcher, clock=lambda: REFERENCE).run(mode="bootstrap", reference_timestamp=REFERENCE,
        target_display_records=1, warmup_records=0, include_footprint=False, include_cryptoquant_confirmation=False,
        include_glassnode_confirmation=False)
    source["data"][0]["cum_vol_delta"] = 999
    assert raw["requests"][0]["response"]["data"][0]["cum_vol_delta"] == 2
    assert raw["context"]["execution_timestamp"] == REFERENCE
    assert {item["requested_at"] for item in raw["requests"]} == {raw["context"]["requested_at"]}


def test_bootstrap_paginates_15m_beyond_4500_and_collects_24192():
    raw = CvdVolumeOrderflowRawExtractor(fetcher, clock=lambda: REFERENCE).run(mode="bootstrap", reference_timestamp=REFERENCE,
        include_footprint=False, include_cryptoquant_confirmation=False, include_glassnode_confirmation=False)
    assert len(raw["requests"]) == 14
    pages = [item for item in raw["requests"] if item["logical_request_id"] == "coinglass:spot:aggregated_cvd:15m"]
    merged, metadata, invalid = merge_paginated_records(pages)
    assert len(pages) == 6
    assert len(merged) >= 24192
    assert metadata["pages_succeeded"] == 6
    assert invalid == []
    assert pages[0]["request_id"] == "coinglass:spot:aggregated_cvd:15m:page:0001"
    assert all(page["params"]["limit"] <= COINGLASS_CVD_MAX_LIMIT for page in pages)


@pytest.mark.parametrize(("mode", "expected"), [("empty", "empty_page"), ("repeat", "repeated_page_signature")])
def test_pagination_stops_safely(mode, expected):
    calls = 0

    def special_fetcher(**kwargs):
        nonlocal calls
        calls += 1
        if mode == "empty" and calls == 2:
            return {"code": "0", "data": []}
        end = REFERENCE if mode == "repeat" else kwargs["params"]["end_time"] // 1000
        return {"code": "0", "data": [cvd_row(end - index * 900) for index in range(4500)]}

    request = build_cvd_volume_orderflow_fetch_plan(mode="bootstrap", reference_timestamp=REFERENCE, include_footprint=False,
        include_cryptoquant_confirmation=False, include_glassnode_confirmation=False)[1]
    pages, stop = CvdVolumeOrderflowRawExtractor(special_fetcher, clock=lambda: REFERENCE).execute_paginated_request(request)
    assert stop == expected
    assert len(pages) >= 2


def test_page_error_is_isolated_and_valid_pages_are_preserved():
    pages = [{"page_index": 1, "status": "ok", "response": {"code": "0", "data": [cvd_row(1_700_000_000)]}},
        {"page_index": 2, "status": "error", "response": None, "error": "boom"}]
    records, metadata, invalid = merge_paginated_records(pages)
    assert records == [{"timestamp": 1_700_000_000, "taker_buy_volume_usd": 10.0, "taker_sell_volume_usd": 8.0, "provider_cvd_usd": 2.0}]
    assert metadata["pages_failed"] == 1
    assert invalid[0]["reason"] == "boom"


def test_normalization_timestamps_numeric_validation_and_negative_provider_cvd():
    assert normalize_coinglass_cvd_record(cvd_row(1, cvd=-2))["provider_cvd_usd"] == -2.0
    assert normalize_coinglass_cvd_record({**cvd_row(1), "time": 1_700_000_000})["timestamp"] == 1_700_000_000
    assert normalize_coinglass_cvd_record({**cvd_row(1), "time": 1_700_000_000_000})["timestamp"] == 1_700_000_000
    for value in (float("nan"), float("inf"), True, -1):
        with pytest.raises(ValueError):
            normalize_coinglass_cvd_record({**cvd_row(1), "agg_taker_buy_vol": value})


def test_footprint_requires_ten_positions_and_does_not_calculate_math():
    snapshot = normalize_footprint_snapshot([1, [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [1, 2]]])
    assert len(snapshot["levels"]) == 1
    assert snapshot["invalid_levels"][0]["reason"] == "footprint_level_must_have_ten_positions"
    assert not ({"delta", "ratio", "vwap", "imbalance"} & snapshot["levels"][0].keys())


def test_upsert_replaces_timestamp_preserves_history_and_does_not_mutate():
    existing, incoming = [{"timestamp": 1, "value": 1}, {"timestamp": 2, "value": 2}], [{"timestamp": 2, "value": 9}, {"timestamp": 3, "value": 3}]
    before = copy.deepcopy((existing, incoming))
    records, metadata = upsert_records_by_timestamp(existing, incoming)
    assert records == [{"timestamp": 1, "value": 1}, {"timestamp": 2, "value": 9}, {"timestamp": 3, "value": 3}]
    assert metadata["timestamps_replaced"] == [2]
    assert (existing, incoming) == before


def test_gap_detection_does_not_fill_records():
    records = [{"timestamp": 60}, {"timestamp": 180}]
    gap = detect_internal_gaps(records, 60)[0]
    assert (gap["missing_records"], gap["first_missing_timestamp"], gap["last_missing_timestamp"]) == (1, 120, 120)
    irregular = detect_internal_gaps([{"timestamp": 0}, {"timestamp": 121}], 60)[0]
    assert (irregular["missing_records"], irregular["first_missing_timestamp"], irregular["last_missing_timestamp"]) == (2, 60, 120)
    assert len(records) == 2


def test_incomplete_pagination_and_invalid_envelope_have_exact_statuses():
    short = [{"page_index": 1, "status": "ok", "response": {"code": "0", "data": [cvd_row(1_700_000_000)]},
        "pagination_stop_reason": "short_page"}]
    assert merge_paginated_records(short, records_required=2)[1]["pagination_complete"] is False
    invalid = [{"page_index": 1, "status": "ok", "response": {"code": "9", "data": []},
        "pagination_stop_reason": "empty_page"}]
    payload = _primary_payload(invalid, None, "1m", 1)
    assert (payload["status"], payload["reason"]) == ("invalid", "invalid_structure")


def test_incoming_duplicate_does_not_claim_historical_replacement():
    records, metadata = upsert_records_by_timestamp([], [{"timestamp": 1, "value": 1}, {"timestamp": 1, "value": 2}])
    assert records == [{"timestamp": 1, "value": 2}]
    assert metadata == {"records_before": 0, "records_incoming": 2, "records_after": 1,
        "duplicates_incoming": 1, "timestamps_replaced": []}


def test_bootstrap_shape_readiness_quality_and_no_downstream_fields():
    result = small_input()
    assert result["mode"] == "bootstrap"
    assert set(result["markets"]) == {"spot", "futures"}
    assert "general" not in result["markets"]
    assert set(result["readiness"]["target_timeframes"]) == {"1m", "5m", "15m", "1h", "4h", "1d"}
    assert result["readiness"]["target_timeframes"]["5m"]["source_timeframe"] == "1m"
    assert result["readiness"]["target_timeframes"]["1d"]["source_timeframe"] == "15m"
    encoded = json.dumps(result, allow_nan=False)
    for forbidden in ("delta_usd", '"cvd_usd"', "buy_sell_ratio", "vwap", "imbalance", "flow_efficiency", "candlestick"):
        assert forbidden not in encoded


def test_incremental_derives_complete_15m_bucket_from_persisted_1m_without_15m_request():
    existing = small_input()
    last_15m = existing["markets"]["spot"]["cvd"]["timeframes"]["15m"]["records"][-1]["timestamp"]
    bucket = last_15m + 900

    def incremental_fetcher(*, provider, endpoint_id, path, params):
        if provider == "coinglass" and endpoint_id in {"spot_aggregated_cvd", "futures_aggregated_cvd"}:
            return {"code": "0", "data": [cvd_row(bucket + i * 60, buy=10+i, sell=5+i) for i in range(15)]}
        return response_for(provider, endpoint_id, params)

    result = run_cvd_volume_orderflow_input(fetcher=incremental_fetcher, reference_timestamp=bucket + 14 * 60,
        requested_mode="incremental", existing_input=existing, target_display_records=1, warmup_records=0,
        include_footprint=False, include_cryptoquant_confirmation=False, include_glassnode_confirmation=False)
    frame = result["markets"]["spot"]["cvd"]["timeframes"]["15m"]
    assert frame["records"][-1]["timestamp"] == bucket
    assert frame["provenance"]["construction"] == "local_resample_from_1m"
    assert frame["provenance"]["paid_request"] is False


def test_incremental_replaces_timestamp_and_preserves_older_history():
    existing = small_input()
    stamp = existing["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"][-1]["timestamp"]

    def replacement_fetcher(*, provider, endpoint_id, path, params):
        response = response_for(provider, endpoint_id, params)
        if provider == "coinglass" and endpoint_id == "spot_aggregated_cvd":
            response["data"].append(cvd_row(stamp, buy=99))
        return response

    result = small_input(fetcher=replacement_fetcher, existing_input=existing)
    records = result["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"]
    assert result["mode"] == "incremental"
    assert next(row for row in records if row["timestamp"] == stamp)["taker_buy_volume_usd"] == 99
    old_first = existing["markets"]["spot"]["cvd"]["timeframes"]["1m"]["records"][0]
    assert next(row for row in records if row["timestamp"] == old_first["timestamp"]) == old_first


def test_recovery_runs_only_explicit_request():
    calls = []

    def recording_fetcher(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return response_for(kwargs["provider"], kwargs["endpoint_id"], kwargs["params"])

    result = small_input(fetcher=recording_fetcher, requested_mode="recovery", recovery_requests=[{
        "market": "spot", "timeframe": "1m", "start_timestamp": REFERENCE - 120, "end_timestamp": REFERENCE, "records_required": 3}])
    assert result["mode"] == "recovery"
    assert len(calls) == 1
    assert calls[0]["endpoint_id"] == "spot_aggregated_cvd"


def test_optional_sources_remain_separate_and_disabled_has_reason():
    result = small_input(include_footprint=True, include_cryptoquant_confirmation=True, include_glassnode_confirmation=True)
    assert result["markets"]["futures"]["confirmations"]["cryptoquant"]["records"][0]["provider_window"] == "hour"
    assert set(result["markets"]["spot"]["confirmations"]["glassnode"]) == {
        "spot_cvd_sum", "spot_vd_sum", "spot_buying_volume_sum", "spot_selling_volume_sum"}
    disabled = small_input()
    assert disabled["markets"]["spot"]["footprint"]["reason"] == "endpoint_disabled"
    assert disabled["quality"]["status"] == "ok"


def test_same_inputs_and_clock_are_deterministic_and_arguments_immutable():
    exchanges = ["Binance", "OKX", "Bybit"]
    recovery = [{"market": "spot", "timeframe": "1m", "start_timestamp": REFERENCE - 60, "end_timestamp": REFERENCE}]
    before = copy.deepcopy((exchanges, recovery))
    first = small_input(exchanges=exchanges, requested_mode="recovery", recovery_requests=recovery)
    second = small_input(exchanges=exchanges, requested_mode="recovery", recovery_requests=recovery)
    assert first == second
    assert (exchanges, recovery) == before


def test_synthetic_requires_demo_mode():
    with pytest.raises(ValueError, match="data_mode"):
        small_input(is_demo=False)
