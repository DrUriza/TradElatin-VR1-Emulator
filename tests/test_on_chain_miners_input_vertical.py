import copy
import json
import math
from collections.abc import Mapping, Sequence

import pytest

from processing_signals.input.on_chain_miners.on_chain_miners_data_raw_extract import (
    COLLECTION_EXTENSION_IDS,
    CORE_METRIC_IDS,
    ENRICHMENT_METRIC_IDS,
    SCREEN_EXTENSION_METRIC_IDS,
    TIME_SERIES_EXTENSION_IDS,
    UTXO_AGE_BANDS,
    build_on_chain_miners_fetch_plan,
    extract_on_chain_miners_raw,
    is_validated_miner_flag,
    resolve_existing_input_state,
    validated_miner_symbols,
)
from processing_signals.input.on_chain_miners.on_chain_miners_data_raw_preprocessing import (
    normalize_optional_finite_number,
    preprocess_miner_entities,
    preprocess_miner_outflow_by_pool,
    preprocess_on_chain_metric,
    run_on_chain_miners_input,
    upsert_on_chain_records,
)


DAY = 86_400
NOW = 1_785_110_400


def responses(null_sopr=False):
    dates = ["2026-07-26", "2026-07-25"]
    utxo_record = {"date": dates[0]}
    for index, band in enumerate(UTXO_AGE_BANDS, 1):
        utxo_record.update({f"range_{band}": float(index * 100), f"range_{band}_usd": float(index * 100_000),
                            f"range_{band}_percent": index / 100})
    payloads = {
        "balance_miners_sum": [{"t": NOW - DAY, "v": 1872611.91}, {"t": NOW, "v": 1874853.91}],
        "hash_rate_mean": [{"t": NOW - DAY, "v": 682900000000000000000}, {"t": NOW, "v": 684200000000000000000}],
        "sopr": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "sopr": None if null_sopr else 1.036, "a_sopr": 1.029, "sth_sopr": 1.018, "lth_sopr": 1.091},
            {"date": dates[1], "sopr": 1.031, "a_sopr": 1.027, "sth_sopr": 1.012, "lth_sopr": 1.087}]}},
        "difficulty": {"status": {"code": "200", "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "difficulty": 94600000000000.0}, {"date": dates[1], "difficulty": 94450000000000.0}]}},
        "mpi": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "mpi": 1.42}, {"date": dates[1], "mpi": -0.18}]}},
        "puell_multiple": {"code": "0", "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "puell_multiple": 1.35}]},
        "bitcoin_sth_sopr": {"code": 0, "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "sth_sopr": 1.02}]},
        "bitcoin_lth_sopr": {"code": 200, "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "lth_sopr": 1.08}]},
        "bitcoin_nupl": {"code": "0", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "net_unpnl": 0.61}]},
        "miner_entity_list": {"status": {"code": 200, "message": "success"}, "result": {"type": "miner", "data": [
            {"name": "F2Pool", "symbol": "f2pool", "is_validated": 1, "market_type": 0},
            {"name": "AntPool", "symbol": "antpool", "is_validated": 1, "market_type": 0},
            {"name": "Unknown", "symbol": "unknown", "is_validated": 0, "market_type": 0}]}},
        "miner_outflow": {symbol: {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "outflow_total": 1245.4, "outflow_top10": 981.2, "outflow_mean": 3.48},
            {"date": dates[1], "outflow_total": 1102.8, "outflow_top10": 810.3, "outflow_mean": 3.11}]}}
                          for symbol in ("antpool", "f2pool")},
        "miners_unspent_supply": [{"t": NOW - DAY, "v": 1_872_611.91}, {"t": NOW, "v": 1_874_853.91}],
        "utxo_age_distribution": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [utxo_record]}},
        "revenue_sum": [{"t": NOW - DAY, "v": 40_000_000.0}, {"t": NOW, "v": 41_000_000.0}],
        "volume_mined_sum": [{"t": NOW - DAY, "v": 38_000_000.0}, {"t": NOW, "v": 39_000_000.0}],
        "revenue_from_fees": [{"t": NOW - DAY, "v": 0.05}, {"t": NOW, "v": 0.048}],
    }
    return payloads


class FakeFetcher:
    def __init__(self, payloads=None, failing=()):
        self.payloads = payloads or responses()
        self.failing = set(failing)
        self.calls = []

    def __call__(self, **request):
        self.calls.append(request)
        if request["endpoint_id"] in self.failing:
            raise RuntimeError("provider unavailable")
        if request["endpoint_id"] == "miner_outflow":
            return self.payloads["miner_outflow"][request["params"]["miner"]]
        return self.payloads[request["endpoint_id"]]


def plan(**kwargs):
    return build_on_chain_miners_fetch_plan(mode="bootstrap", reference_timestamp=NOW, **kwargs)


def test_bootstrap_plan_has_ordered_core_and_screen_extensions():
    assert tuple(item["metric_id"] for item in plan()) == CORE_METRIC_IDS + SCREEN_EXTENSION_METRIC_IDS


def test_enrichment_disabled_by_default():
    assert not (set(ENRICHMENT_METRIC_IDS) & {item["metric_id"] for item in plan()})


def test_screen_extensions_can_be_disabled_for_isolated_core_tests():
    assert tuple(item["metric_id"] for item in plan(include_screen_extensions=False)) == CORE_METRIC_IDS


def test_enrichment_adds_exactly_four_requests():
    assert tuple(item["metric_id"] for item in plan(include_enrichment=True)[-len(ENRICHMENT_METRIC_IDS):]) == ENRICHMENT_METRIC_IDS


def test_cryptoquant_daily_params():
    request = plan()[1]
    assert request["params"] == {"window": "day", "from": "20250801", "to": "20260727", "limit": 370, "format": "json"}


def test_glassnode_params_are_seconds():
    params = plan()[2]["params"]
    assert params["a"] == "BTC" and params["i"] == "24h" and params["s"] < 100_000_000_000


def test_miner_reserve_native_currency_only():
    requests = plan()
    assert requests[0]["params"]["c"] == "NATIVE" and "c" not in requests[2]["params"]


def test_requests_contain_no_secrets():
    assert not any(word in json.dumps(plan()).lower() for word in ("api_key", "token", "authorization", "bearer"))


@pytest.mark.parametrize("endpoint_id", ["sopr", "balance_miners_sum", "puell_multiple"])
def test_raw_provider_response_is_preserved(endpoint_id):
    fake = FakeFetcher()
    raw = extract_on_chain_miners_raw(fetcher=fake, mode="bootstrap", reference_timestamp=NOW, include_enrichment=True)
    metric = next(key for key, value in raw["raw"].items() if value.get("endpoint_id") == endpoint_id)
    assert raw["raw"][metric]["response"] == fake.payloads[endpoint_id]
    assert isinstance(raw["raw"][metric]["response"], type(fake.payloads[endpoint_id]))


def test_extractor_captures_endpoint_error_and_continues():
    raw = extract_on_chain_miners_raw(fetcher=FakeFetcher(failing={"sopr"}), mode="bootstrap", reference_timestamp=NOW)
    assert raw["raw"]["sopr"]["status"] == "error" and raw["raw"]["mpi"]["status"] == "ok"


def output(**kwargs):
    return run_on_chain_miners_input(fetcher=kwargs.pop("fetcher", FakeFetcher()), reference_timestamp=NOW,
                                     execution_timestamp=kwargs.pop("execution_timestamp", NOW + 3_600), **kwargs)


def test_sopr_preserves_all_provider_fields():
    record = output()["series"]["sopr"]["records"][-1]
    assert set(("sopr", "a_sopr", "sth_sopr", "lth_sopr")) <= record.keys()


def test_difficulty_stays_provider_native():
    assert output()["series"]["difficulty"]["records"][-1]["value"] == 94600000000000.0


def test_hashrate_stays_h_per_second():
    record = output()["series"]["hashrate"]["records"][-1]
    assert record["value"] == 684200000000000000000.0 and record["unit"] == "H/s"


def test_mpi_is_not_classified():
    assert set(output()["series"]["mpi"]["records"][-1]) == {"timestamp", "value", "unit", "provider", "endpoint_id", "source_field", "source_window"}


def test_descending_response_becomes_ascending():
    records = output()["series"]["sopr"]["records"]
    assert [record["timestamp"] for record in records] == sorted(record["timestamp"] for record in records)


def test_coinglass_milliseconds_become_seconds():
    assert output(include_enrichment=True)["series"]["puell_multiple"]["records"][0]["timestamp"] == NOW


def test_coinglass_history_is_filtered_to_requested_range():
    payloads = responses()
    payloads["puell_multiple"]["data"].insert(0, {"timestamp": (NOW - 200 * DAY) * 1000, "price": 1, "puell_multiple": 1})
    records = output(fetcher=FakeFetcher(payloads), include_enrichment=True)["series"]["puell_multiple"]["records"]
    assert [record["timestamp"] for record in records] == [NOW - 200 * DAY, NOW]


def test_null_is_unavailable_not_zero():
    series = output(fetcher=FakeFetcher(responses(null_sopr=True)))["series"]["sopr"]
    assert series["unavailable_records"] and all(record["value"] != 0 for record in series["records"])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_number_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_optional_finite_number(value)


def test_duplicate_incoming_last_wins():
    assert upsert_on_chain_records([], [{"timestamp": 1, "value": 1}, {"timestamp": 1, "value": 2}]) == [{"timestamp": 1, "value": 2}]


def test_incoming_replaces_existing():
    assert upsert_on_chain_records([{"timestamp": 1, "value": 1}], [{"timestamp": 1, "value": 2}])[0]["value"] == 2


def existing_contract():
    base = output()
    for series in base["series"].values():
        series["records"].insert(0, {**series["records"][0], "timestamp": series["records"][0]["timestamp"] - 10 * DAY})
    return base


def test_incremental_preserves_older_history():
    existing = existing_contract()
    current = output(existing_contract=existing)
    assert len(current["series"]["sopr"]["records"]) == 3


def test_incremental_failure_preserves_history_as_partial():
    existing = existing_contract()
    current = output(fetcher=FakeFetcher(failing={"sopr"}), existing_contract=existing)
    assert current["series"]["sopr"]["status"] == "partial" and len(current["series"]["sopr"]["records"]) == 3


def test_invalid_envelope_preserves_history_and_marks_invalid():
    existing = existing_contract()
    payloads = responses()
    payloads["sopr"] = []
    current = output(fetcher=FakeFetcher(payloads), existing_contract=existing)
    assert current["series"]["sopr"]["status"] == "invalid" and len(current["series"]["sopr"]["records"]) == 3


def test_cryptoquant_invalid_element_is_isolated():
    payloads = responses()
    payloads["sopr"]["result"]["data"].insert(1, "bad-record")
    series = output(fetcher=FakeFetcher(payloads))["series"]["sopr"]
    assert len(series["records"]) == 2 and len(series["invalid_records"]) == 1 and series["status"] == "partial"


def test_glassnode_missing_timestamp_is_isolated():
    payloads = responses()
    payloads["balance_miners_sum"].insert(1, {"v": 1.0})
    series = output(fetcher=FakeFetcher(payloads))["series"]["miner_reserve"]
    assert len(series["records"]) == 2 and len(series["invalid_records"]) == 1


def test_coinglass_invalid_element_is_isolated():
    payloads = responses()
    valid = payloads["puell_multiple"]["data"][0]
    payloads["puell_multiple"]["data"] = [{**valid, "timestamp": (NOW - DAY) * 1000}, 7, valid]
    series = output(fetcher=FakeFetcher(payloads), include_enrichment=True)["series"]["puell_multiple"]
    assert len(series["records"]) == 2 and len(series["invalid_records"]) == 1


def test_nan_record_is_invalid_without_removing_valid_records():
    payloads = responses()
    payloads["mpi"]["result"]["data"].insert(1, {"date": "2026-07-24", "mpi": math.nan})
    series = output(fetcher=FakeFetcher(payloads))["series"]["mpi"]
    assert len(series["records"]) == 2 and len(series["invalid_records"]) == 1


def test_invalid_timestamp_is_isolated():
    payloads = responses()
    payloads["hash_rate_mean"].insert(1, {"t": "yesterday", "v": 1.0})
    series = output(fetcher=FakeFetcher(payloads))["series"]["hashrate"]
    assert len(series["records"]) == 2 and len(series["invalid_records"]) == 1


def test_partial_series_does_not_stop_other_metrics():
    payloads = responses()
    payloads["sopr"]["result"]["data"].insert(1, "bad-record")
    result = output(fetcher=FakeFetcher(payloads))
    assert result["series"]["sopr"]["status"] == "partial"
    assert all(result["series"][metric_id]["records"] for metric_id in CORE_METRIC_IDS if metric_id != "sopr")


def test_recovery_requests_only_selected_metric_but_returns_complete_input():
    recovery = [{"metric_id": "sopr", "start_timestamp": NOW - DAY, "end_timestamp": NOW}]
    result = output(requested_mode="recovery", recovery_requests=recovery)
    assert set(CORE_METRIC_IDS + TIME_SERIES_EXTENSION_IDS) <= set(result["series"])
    assert set(COLLECTION_EXTENSION_IDS) == set(result["collections"])
    assert result["series"]["sopr"]["incoming_records"]


def test_recovery_adds_metric_warmup():
    request = build_on_chain_miners_fetch_plan(mode="recovery", reference_timestamp=NOW,
                                                recovery_requests=[{"metric_id": "sopr", "start_timestamp": NOW, "end_timestamp": NOW}])[0]
    assert request["from_timestamp"] == NOW - 7 * DAY


def test_gaps_are_reported_not_filled():
    payloads = responses()
    payloads["balance_miners_sum"] = [{"t": NOW - 3 * DAY, "v": 1}, {"t": NOW, "v": 2}]
    series = output(fetcher=FakeFetcher(payloads))["series"]["miner_reserve"]
    assert series["gaps"][0]["missing_days"] == 2 and len(series["records"]) == 2


def test_data_as_of_uses_minimum_core_latest():
    result = output()
    expected = min(result["series"][metric]["records"][-1]["timestamp"] for metric in CORE_METRIC_IDS)
    assert result["quality"]["data_as_of"] == expected


def test_short_bootstrap_is_partial_due_to_history_coverage():
    result = output()
    series = result["series"]["sopr"]
    assert series["status"] == "partial" and "requested_history_not_fully_covered" in series["warnings"]
    assert not series["metadata"]["history_complete"] and result["quality"]["status"] == "partial" and result["quality"]["recovery_required"]


def _daily_glassnode_raw(start, end, missing=()):
    records = [{"t": timestamp, "v": float(index + 1)} for index, timestamp in enumerate(range(start, end + DAY, DAY)) if timestamp not in missing]
    return {"status": "ok", "response": records, "from_timestamp": start, "to_timestamp": end}


def test_bootstrap_with_tolerated_daily_coverage_is_available():
    start = NOW - 130 * DAY
    series = preprocess_on_chain_metric(metric_id="hashrate", raw_payload=_daily_glassnode_raw(start, NOW, missing={start}), mode="bootstrap")
    assert series["status"] == "available" and series["metadata"]["history_complete"]
    assert series["metadata"]["requested_days"] == 131 and series["metadata"]["covered_days"] == 130


def test_short_incremental_with_sufficient_persisted_coverage_is_complete():
    start = NOW - 7 * DAY
    existing = {"records": preprocess_on_chain_metric(metric_id="hashrate", raw_payload=_daily_glassnode_raw(start, NOW),
                                                       mode="bootstrap")["records"]}
    raw = _daily_glassnode_raw(NOW, NOW + DAY)
    raw["response"] = [{"t": NOW}, {"t": NOW + DAY, "v": 99.0}]
    series = preprocess_on_chain_metric(metric_id="hashrate", raw_payload=raw, existing_series=existing, mode="incremental")
    assert series["metadata"]["history_complete"] and "requested_history_not_fully_covered" not in series["warnings"]


def test_incomplete_recovery_is_partial():
    start = NOW - 31 * DAY
    series = preprocess_on_chain_metric(metric_id="miner_reserve", raw_payload=_daily_glassnode_raw(start, NOW), mode="recovery")
    series_short = preprocess_on_chain_metric(metric_id="miner_reserve",
                                              raw_payload={**_daily_glassnode_raw(start, NOW), "response": [{"t": NOW, "v": 1.0}]}, mode="recovery")
    recovery = output(requested_mode="recovery", recovery_requests=[{"metric_id": "miner_reserve", "start_timestamp": start, "end_timestamp": NOW}])
    assert series["metadata"]["history_complete"] and series_short["status"] == "partial"
    assert recovery["series"]["miner_reserve"]["status"] == "partial" and recovery["quality"]["recovery_required"]


def test_coverage_ratio_never_exceeds_one():
    raw = _daily_glassnode_raw(NOW - DAY, NOW)
    raw["response"].extend([{"t": NOW, "v": 5.0}, {"t": NOW, "v": 6.0}])
    assert preprocess_on_chain_metric(metric_id="hashrate", raw_payload=raw)["metadata"]["coverage_ratio"] == 1.0


def test_synthetic_and_live_normalize_identically_except_context():
    live = output()
    synthetic = output(data_mode="synthetic", is_demo=True)
    assert live["series"] == synthetic["series"] and live["quality"] == synthetic["quality"]


def test_execution_timestamp_is_independent_from_reference_and_data_as_of():
    execution = NOW + 12_345
    raw = extract_on_chain_miners_raw(fetcher=FakeFetcher(), mode="bootstrap", reference_timestamp=NOW,
                                      execution_timestamp=execution, data_mode="synthetic", is_demo=True)
    result = output(execution_timestamp=execution)
    assert raw["context"]["reference_timestamp"] == NOW and raw["context"]["execution_timestamp"] == execution
    assert raw["context"]["requested_at"] == "2026-07-27T03:25:45Z"
    assert result["context"]["generated_at"] == "2026-07-27T03:25:45Z" and execution != result["quality"]["data_as_of"]


def test_synthetic_requires_demo():
    with pytest.raises(ValueError):
        output(data_mode="synthetic")


def test_output_is_strict_json_serializable():
    json.dumps(output(include_enrichment=True), allow_nan=False)


def _collect_keys(value):
    keys = set()
    if isinstance(value, Mapping):
        keys.update(value)
        for item in value.values():
            keys.update(_collect_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def test_output_contains_no_processing_or_classification_fields():
    forbidden = {"sopr_7d", "reserve_delta", "miner_net_position_change", "reserve_trend", "difficulty_t", "difficulty_trillion", "hashrate_eh_s",
                 "sopr_regime", "miner_pressure_state", "signal", "display_signal", "display_color_token"}
    assert forbidden.isdisjoint(_collect_keys(output()))


def test_resolve_existing_input_state_accepts_none_and_empty_mapping():
    assert resolve_existing_input_state(None) == {} and resolve_existing_input_state({}) == {}


def test_resolve_existing_input_state_accepts_direct_input():
    state = output()
    assert resolve_existing_input_state(state) is state


def test_resolve_existing_input_state_accepts_vertical_bundle():
    state = output()
    bundle = {"input": state, "processing": {}, "classification": {}, "screen": {}}
    resolved = resolve_existing_input_state(bundle)
    assert resolved is state and output(existing_contract=bundle)["mode"] == "incremental"


def test_resolve_existing_input_state_rejects_nonempty_unknown_shape():
    with pytest.raises(ValueError, match="vertical bundle"):
        resolve_existing_input_state({"series": {}})


def test_normal_execution_contains_all_required_extensions():
    result = output()
    assert set(TIME_SERIES_EXTENSION_IDS) <= set(result["series"])
    assert set(COLLECTION_EXTENSION_IDS) == set(result["collections"])
    assert set(SCREEN_EXTENSION_METRIC_IDS) <= set(result["quality"]["availability"])


def test_entity_catalog_request_and_validated_fanout_are_exact():
    fake = FakeFetcher()
    raw = extract_on_chain_miners_raw(fetcher=fake, mode="bootstrap", reference_timestamp=NOW)
    entity_call = next(call for call in fake.calls if call["endpoint_id"] == "miner_entity_list")
    outflow_calls = [call for call in fake.calls if call["endpoint_id"] == "miner_outflow"]
    assert entity_call["params"] == {"type": "miner", "format": "json"}
    assert [call["params"]["miner"] for call in outflow_calls] == ["antpool", "f2pool"]
    assert raw["raw"]["miner_outflow_by_pool"]["entity_symbols"] == ["antpool", "f2pool"]


def test_entity_symbols_are_deduplicated_and_last_record_wins():
    payloads = responses()
    data = payloads["miner_entity_list"]["result"]["data"]
    data.append({"name": "F2Pool Updated", "symbol": "f2pool", "is_validated": 1, "market_type": 0})
    result = output(fetcher=FakeFetcher(payloads))["collections"]["miner_entities"]
    assert result["metadata"]["symbols"] == ["antpool", "f2pool"]
    assert next(record for record in result["records"] if record["symbol"] == "f2pool")["name"] == "F2Pool Updated"


def test_each_pool_raw_envelope_is_preserved_exactly():
    fake = FakeFetcher()
    raw = extract_on_chain_miners_raw(fetcher=fake, mode="bootstrap", reference_timestamp=NOW)["raw"]["miner_outflow_by_pool"]
    for request in raw["requests"]:
        assert request["response"] == fake.payloads["miner_outflow"][request["miner_symbol"]]


class PoolFailingFetcher(FakeFetcher):
    def __call__(self, **request):
        if request["endpoint_id"] == "miner_outflow" and request["params"]["miner"] == "f2pool":
            self.calls.append(request)
            raise RuntimeError("pool unavailable")
        return super().__call__(**request)


def test_one_pool_failure_does_not_abort_others_and_is_partial():
    raw = extract_on_chain_miners_raw(fetcher=PoolFailingFetcher(), mode="bootstrap", reference_timestamp=NOW)["raw"]["miner_outflow_by_pool"]
    assert raw["status"] == "partial"
    result = output(fetcher=PoolFailingFetcher())["collections"]["miner_outflow_by_pool"]
    assert result["status"] == "partial" and result["pools"]["antpool"]["records"]
    assert result["pools"]["f2pool"]["status"] == "unavailable"


def test_pool_incremental_history_and_timestamp_upsert():
    existing = output()
    old = copy.deepcopy(existing["collections"]["miner_outflow_by_pool"]["pools"]["antpool"]["records"][0])
    old["timestamp"] -= 20 * DAY
    existing["collections"]["miner_outflow_by_pool"]["pools"]["antpool"]["records"].insert(0, old)
    current = output(existing_contract=existing)
    records = current["collections"]["miner_outflow_by_pool"]["pools"]["antpool"]["records"]
    assert old["timestamp"] in {record["timestamp"] for record in records}
    assert len({record["timestamp"] for record in records}) == len(records)


def test_removed_pool_history_is_preserved_and_marked_inactive():
    existing = output()
    payloads = responses()
    payloads["miner_entity_list"]["result"]["data"] = [payloads["miner_entity_list"]["result"]["data"][1]]
    current = output(fetcher=FakeFetcher(payloads), existing_contract=existing)
    assert current["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"]["active"] is False
    assert current["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"]["records"]


def test_recovery_can_select_explicit_pool():
    recovery = [{"metric_id": "miner_outflow_by_pool", "start_timestamp": NOW - DAY, "end_timestamp": NOW, "miner_symbols": ["f2pool"]}]
    fake = FakeFetcher()
    result = output(fetcher=fake, requested_mode="recovery", recovery_requests=recovery)
    calls = [call for call in fake.calls if call["endpoint_id"] == "miner_outflow"]
    assert [call["params"]["miner"] for call in calls] == ["f2pool"]
    assert set(result["collections"]["miner_outflow_by_pool"]["pools"]) == {"f2pool"}


def test_recovery_without_explicit_pool_uses_catalog():
    recovery = [{"metric_id": "miner_outflow_by_pool", "start_timestamp": NOW - DAY, "end_timestamp": NOW}]
    fake = FakeFetcher()
    output(fetcher=fake, requested_mode="recovery", recovery_requests=recovery)
    assert [call["params"]["miner"] for call in fake.calls if call["endpoint_id"] == "miner_outflow"] == ["antpool", "f2pool"]


@pytest.mark.parametrize(("metric_id", "expected"), [("miners_unspent_supply", "NATIVE"),
                                                        ("miner_revenue_total_usd", "USD"),
                                                        ("miner_block_reward_revenue_usd", "USD")])
def test_extension_currency_params(metric_id, expected):
    request = next(item for item in plan() if item["metric_id"] == metric_id)
    assert request["params"]["c"] == expected


def test_revenue_from_fees_has_no_currency_conversion():
    request = next(item for item in plan() if item["metric_id"] == "miner_revenue_from_fees")
    assert "c" not in request["params"]


def test_utxo_request_is_daily_and_preserves_all_dimensions():
    request = next(item for item in plan() if item["metric_id"] == "utxo_age_distribution")
    assert request["params"]["window"] == "day"
    record = output()["series"]["utxo_age_distribution"]["records"][0]
    assert tuple(record["bands"]) == UTXO_AGE_BANDS
    assert all(set(record["bands"][band]) == {"native_btc", "usd", "percent"} for band in UTXO_AGE_BANDS)
    assert record["scope"] == "bitcoin_network"


def test_utxo_metadata_is_not_miner_specific():
    metadata = output()["series"]["utxo_age_distribution"]["metadata"]
    assert metadata["scope"] == "bitcoin_network" and metadata["is_miner_specific"] is False


def test_utxo_null_band_dimensions_remain_null():
    payloads = responses()
    record = payloads["utxo_age_distribution"]["result"]["data"][0]
    record["range_0d_1d"] = record["range_0d_1d_usd"] = None
    band = output(fetcher=FakeFetcher(payloads))["series"]["utxo_age_distribution"]["records"][0]["bands"]["0d_1d"]
    assert band["native_btc"] is None and band["usd"] is None and band["percent"] is not None


def test_nupl_without_msg_uses_net_unpnl_seconds_and_price():
    request = next(item for item in plan() if item["metric_id"] == "nupl")
    assert request["params"] == {}
    record = output()["series"]["nupl"]["records"][0]
    assert record["timestamp"] == NOW and record["value"] == 0.61 and record["price_usd"] == 119100.5
    assert "nupl_phase" not in record


@pytest.mark.parametrize(("metric_id", "unit"), [("miners_unspent_supply", "BTC"), ("utxo_age_distribution", "mixed"),
                                                    ("miner_revenue_total_usd", "USD/day"),
                                                    ("miner_block_reward_revenue_usd", "USD/day"),
                                                    ("miner_revenue_from_fees", "provider_native_percentage"), ("nupl", "ratio")])
def test_extension_units_are_exact(metric_id, unit):
    assert output()["series"][metric_id]["unit"] == unit


def test_revenue_and_outflow_publish_no_derived_calculations():
    forbidden = {"pool_share", "pool_rank", "dominant_pool", "fee_revenue_usd", "fee_share", "nupl_phase", "young_supply", "old_supply",
                 "reserve_age_score", "revenue_regime", "outflow_state"}
    assert forbidden.isdisjoint(_collect_keys(output()))


def test_quality_data_as_of_includes_all_temporal_extensions():
    result = output()
    values = [result["series"][metric]["metadata"]["last_available_timestamp"] for metric in (*CORE_METRIC_IDS, *TIME_SERIES_EXTENSION_IDS)]
    values.append(result["collections"]["miner_outflow_by_pool"]["metadata"]["data_as_of"])
    assert result["quality"]["data_as_of"] == min(values)


def test_missing_required_extension_makes_data_as_of_null():
    payloads = responses()
    payloads["revenue_sum"] = []
    result = output(fetcher=FakeFetcher(payloads))
    assert result["quality"]["data_as_of"] is None and result["quality"]["status"] == "partial"


def test_invalid_extension_envelope_makes_quality_invalid():
    payloads = responses()
    payloads["utxo_age_distribution"] = []
    result = output(fetcher=FakeFetcher(payloads))
    assert result["series"]["utxo_age_distribution"]["status"] == "invalid"
    assert result["quality"]["status"] == "invalid"


def test_extensions_are_json_strict_and_live_synthetic_equal():
    live = output()
    synthetic = output(data_mode="synthetic", is_demo=True)
    assert live["series"] == synthetic["series"] and live["collections"] == synthetic["collections"]
    json.dumps(live, allow_nan=False)


def test_existing_state_is_not_mutated_by_extension_incremental():
    existing = output()
    before = copy.deepcopy(existing)
    output(existing_contract=existing)
    assert existing == before


def test_failed_catalog_reuses_active_symbols_preserves_entities_and_keeps_outflow_partial():
    existing = output()
    before = copy.deepcopy(existing)
    fake = FakeFetcher(failing={"miner_entity_list"})
    result = output(fetcher=fake, existing_contract=existing)
    entities = result["collections"]["miner_entities"]
    outflow = result["collections"]["miner_outflow_by_pool"]
    assert entities["records"] == existing["collections"]["miner_entities"]["records"]
    assert entities["status"] == "partial"
    assert entities["metadata"] == {**entities["metadata"], "catalog_source": "existing_state",
                                      "catalog_refresh_succeeded": False, "reused_symbols": ["antpool", "f2pool"]}
    assert entities["warnings"].count("miner_entity_catalog_reused_from_existing_state") == 1
    assert [call["params"]["miner"] for call in fake.calls if call["endpoint_id"] == "miner_outflow"] == ["antpool", "f2pool"]
    assert all(pool["active"] for pool in outflow["pools"].values())
    assert outflow["status"] == "partial" and "miner_outflow_catalog_reused" in outflow["warnings"]
    assert existing == before


def test_invalid_catalog_preserves_previous_catalog_and_pool_flags_but_global_is_invalid():
    existing = output()
    payloads = responses()
    payloads["miner_entity_list"] = {"unexpected": []}
    result = output(fetcher=FakeFetcher(payloads), existing_contract=existing)
    assert result["collections"]["miner_entities"]["status"] == "invalid"
    assert result["collections"]["miner_entities"]["records"] == existing["collections"]["miner_entities"]["records"]
    assert {key: pool["active"] for key, pool in result["collections"]["miner_outflow_by_pool"]["pools"].items()} == {"antpool": True, "f2pool": True}
    assert result["quality"]["status"] == "invalid"


def test_failed_catalog_without_state_skips_fanout_and_has_null_data_as_of():
    fake = FakeFetcher(failing={"miner_entity_list"})
    result = output(fetcher=fake)
    outflow = result["collections"]["miner_outflow_by_pool"]
    assert not [call for call in fake.calls if call["endpoint_id"] == "miner_outflow"]
    assert outflow["status"] == "unavailable" and outflow["metadata"]["data_as_of"] is None
    assert "miner_outflow_fanout_skipped_no_symbols" in outflow["warnings"]


def test_valid_empty_catalog_deactivates_historical_pools_but_preserves_history():
    existing = output()
    payloads = responses()
    payloads["miner_entity_list"]["result"]["data"] = []
    result = output(fetcher=FakeFetcher(payloads), existing_contract=existing)
    outflow = result["collections"]["miner_outflow_by_pool"]
    assert all(not pool["active"] and pool["records"] for pool in outflow["pools"].values())
    assert outflow["status"] == "unavailable" and outflow["metadata"]["data_as_of"] is None
    assert outflow["metadata"]["pools_active"] == 0 and outflow["metadata"]["pools_inactive"] == 2


@pytest.mark.parametrize(("flag", "accepted"), [(1, True), (True, False), (False, False), (2, False), (-1, False), ("1", False),
                                                   ("true", False), (1.0, False), (None, False)])
def test_is_validated_flag_is_exact_integer_one_in_fanout_and_preprocessing(flag, accepted):
    response = {"status": {"code": 200}, "result": {"data": [{"symbol": "antpool", "is_validated": flag}]}}
    processed = preprocess_miner_entities({"status": "ok", "response": response})
    assert is_validated_miner_flag(flag) is accepted
    assert (validated_miner_symbols(response) == ["antpool"]) is accepted
    assert (processed["metadata"]["symbols"] == ["antpool"]) is accepted
    if not accepted:
        assert processed["invalid_records"][0]["reason"] == "is_validated_must_be_integer_one"


def test_outflow_data_as_of_uses_only_active_minimum_and_requires_every_active_pool():
    existing = output()["collections"]["miner_outflow_by_pool"]
    existing["pools"]["antpool"]["metadata"]["last_timestamp"] = NOW
    existing["pools"]["f2pool"]["metadata"]["last_timestamp"] = NOW - DAY
    raw = {"status": "ok", "entity_symbols": ["antpool", "f2pool"], "requests": [], "catalog_state": "valid",
           "catalog_source": "live", "catalog_refresh_succeeded": True}
    result = preprocess_miner_outflow_by_pool(raw, existing)
    assert result["metadata"]["data_as_of"] == NOW - DAY
    result["pools"]["f2pool"]["active"] = False
    inactive = preprocess_miner_outflow_by_pool({**raw, "entity_symbols": ["antpool"]}, result)
    assert inactive["metadata"]["data_as_of"] == NOW
    inactive["pools"]["f2pool"]["metadata"]["last_timestamp"] = 1_577_836_800
    assert preprocess_miner_outflow_by_pool({**raw, "entity_symbols": ["antpool"]}, inactive)["metadata"]["data_as_of"] == NOW


def test_active_pool_without_timestamp_forces_collection_and_global_data_as_of_null():
    existing = output()
    pool = existing["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"]
    pool["records"] = []
    pool["metadata"]["last_timestamp"] = None
    result = output(fetcher=PoolFailingFetcher(), existing_contract=existing)
    outflow = result["collections"]["miner_outflow_by_pool"]
    assert outflow["status"] == "partial" and outflow["metadata"]["data_as_of"] is None
    assert "active_pool_data_as_of_unavailable:f2pool" in outflow["warnings"]
    assert result["quality"]["data_as_of"] is None


def test_failed_pool_with_history_uses_timestamp_but_remains_partial():
    existing = output()
    result = output(fetcher=PoolFailingFetcher(), existing_contract=existing)
    outflow = result["collections"]["miner_outflow_by_pool"]
    assert outflow["metadata"]["data_as_of"] is not None and outflow["status"] == "partial"
    assert "pool_update_failed_using_preserved_history:f2pool" in outflow["warnings"]


def test_recovery_nupl_preserves_complete_existing_contract_and_is_immutable():
    existing = output(include_enrichment=True)
    before = copy.deepcopy(existing)
    recovery = [{"metric_id": "nupl", "start_timestamp": NOW - DAY, "end_timestamp": NOW}]
    result = output(existing_contract=existing, requested_mode="recovery", recovery_requests=recovery, include_enrichment=True)
    assert set(result["series"]) == set(existing["series"])
    assert result["collections"] == existing["collections"]
    assert all(result["series"][metric] == existing["series"][metric] for metric in existing["series"] if metric != "nupl")
    assert existing == before and set(SCREEN_EXTENSION_METRIC_IDS) <= set(result["quality"]["availability"])


def test_selective_pool_recovery_preserves_other_pool_and_active_flags():
    existing = output()
    before_f2pool = copy.deepcopy(existing["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"])
    recovery = [{"metric_id": "miner_outflow_by_pool", "start_timestamp": NOW - DAY, "end_timestamp": NOW,
                 "miner_symbols": ["antpool"]}]
    result = output(existing_contract={"input": existing}, requested_mode="recovery", recovery_requests=recovery)
    assert result["collections"]["miner_entities"] == existing["collections"]["miner_entities"]
    assert result["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"] == before_f2pool
    assert result["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"]["active"] is True


@pytest.mark.parametrize(("metric_id", "endpoint_id", "payload"), [
    ("nupl", "bitcoin_nupl", {"code": 0, "data": [{"timestamp": NOW * 1000, "net_unpnl": math.nan}]}),
    ("miners_unspent_supply", "miners_unspent_supply", [{"t": NOW, "v": math.inf}]),
    ("miner_revenue_total_usd", "revenue_sum", [{"t": NOW, "v": -math.inf}]),
])
def test_only_non_finite_extension_record_is_invalid_and_not_copied(metric_id, endpoint_id, payload):
    payloads = responses()
    payloads[endpoint_id] = payload
    series = output(fetcher=FakeFetcher(payloads))["series"][metric_id]
    assert series["status"] == "invalid" and not series["records"]
    assert series["invalid_records"] and "nan" not in json.dumps(series).lower() and "infinity" not in json.dumps(series).lower()


def test_valid_plus_nonfinite_is_partial_while_null_only_is_unavailable():
    payloads = responses()
    payloads["bitcoin_nupl"]["data"] = [{"timestamp": NOW * 1000, "net_unpnl": 0.5},
                                           {"timestamp": (NOW - DAY) * 1000, "net_unpnl": math.nan}]
    assert output(fetcher=FakeFetcher(payloads))["series"]["nupl"]["status"] == "partial"
    payloads["bitcoin_nupl"]["data"] = [{"timestamp": NOW * 1000, "net_unpnl": None}]
    series = output(fetcher=FakeFetcher(payloads))["series"]["nupl"]
    assert series["status"] == "unavailable" and not series["invalid_records"]


def test_utxo_only_infinity_and_outflow_only_nan_are_invalid_without_nonfinite_output():
    payloads = responses()
    record = {"date": "2026-07-26", **{f"range_{band}": None for band in UTXO_AGE_BANDS},
              **{f"range_{band}_usd": None for band in UTXO_AGE_BANDS},
              **{f"range_{band}_percent": None for band in UTXO_AGE_BANDS}}
    record["range_0d_1d"] = math.inf
    payloads["utxo_age_distribution"]["result"]["data"] = [record]
    for symbol in payloads["miner_outflow"]:
        payloads["miner_outflow"][symbol]["result"]["data"] = [{"date": "2026-07-26", "outflow_total": math.nan}]
    result = output(fetcher=FakeFetcher(payloads))
    assert result["series"]["utxo_age_distribution"]["status"] == "invalid"
    assert all(pool["status"] == "invalid" for pool in result["collections"]["miner_outflow_by_pool"]["pools"].values())
    json.dumps(result, allow_nan=False)


def test_negative_zero_is_normalized_recursively_in_extensions_and_recovery_copy():
    existing = output()
    existing["series"]["nupl"]["records"][-1]["value"] = -0.0
    existing["collections"]["miner_outflow_by_pool"]["pools"]["f2pool"]["records"][-1]["outflow_total"] = -0.0
    recovery = [{"metric_id": "sopr", "start_timestamp": NOW - DAY, "end_timestamp": NOW}]
    result = output(existing_contract=existing, requested_mode="recovery", recovery_requests=recovery)

    def negative_zeros(value):
        if isinstance(value, float):
            return int(value == 0.0 and math.copysign(1.0, value) < 0)
        if isinstance(value, Mapping):
            return sum(negative_zeros(item) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return sum(negative_zeros(item) for item in value)
        return 0

    assert negative_zeros(result) == 0
    json.dumps(result, allow_nan=False)
