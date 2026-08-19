import copy
import json
import math

import pytest

from processing_signals.input.open_interest_and_funding.open_interest_and_funding_data_raw_extract import (
    BOOTSTRAP_LIMIT,
    ENDPOINTS,
    INCREMENTAL_LIMITS,
    SCREEN_TIMEFRAMES,
    OpenInterestAndFundingRawExtractor,
    build_coinglass_history_params,
    build_cryptoquant_params,
    build_glassnode_params,
    build_open_interest_and_funding_fetch_plan,
)
from processing_signals.input.open_interest_and_funding.open_interest_and_funding_data_raw_preprocessing import (
    determine_open_interest_and_funding_input_mode,
    normalize_coinglass_ohlc_record,
    normalize_finite_float,
    normalize_funding_exchange_response,
    normalize_timestamp_utc,
    preprocess_open_interest_and_funding_raw,
    run_open_interest_and_funding_input,
    unwrap_cryptoquant_response,
    upsert_records_by_timestamp,
)

NOW = 1_800_000_000


def _coinglass_ohlc(timestamp=NOW, value=100.0):
    return {"code": "0", "msg": "success", "data": [{"time": timestamp * 1000, "open": str(value), "high": str(value + 2),
        "low": str(value - 2), "close": str(value + 1)}]}


def _fetcher(*, provider, endpoint_id, path, params):
    del path
    if provider == "coinglass" and endpoint_id in {"aggregated_open_interest_ohlc", "oi_weighted_funding_rate_ohlc"}:
        return _coinglass_ohlc(value=0.01 if endpoint_id.startswith("oi_weighted") else 100.0)
    if endpoint_id == "open_interest_exchange_list":
        return {"code": 0, "data": [{"exchange": "All", "symbol": "BTC", "open_interest_usd": 1000, "open_interest_quantity": 10,
            "open_interest_by_stable_coin_margin": 800, "open_interest_quantity_by_coin_margin": 2,
            "open_interest_quantity_by_stable_coin_margin": 8, "open_interest_change_percent_5m": 1,
            "open_interest_change_percent_15m": 2, "open_interest_change_percent_30m": 3, "open_interest_change_percent_1h": 4,
            "open_interest_change_percent_4h": 5, "open_interest_change_percent_24h": 6}]}
    if endpoint_id == "funding_rate_exchange_list":
        return {"code": "200", "data": [{"symbol": "BTC", "stablecoin_margin_list": [{"exchange": "Binance",
            "funding_rate_interval": 8, "funding_rate": 0.01, "next_funding_time": NOW * 1000}], "token_margin_list": [{"exchange": "Binance",
            "funding_rate_interval": 8, "funding_rate": -0.02, "next_funding_time": NOW * 1000}]},
            {"symbol": "ETH", "stablecoin_margin_list": [], "token_margin_list": []}]}
    if endpoint_id == "options_info":
        return {"code": 200, "data": [{"exchange_name": "All", "open_interest": 5, "oi_market_share": 100,
            "open_interest_change_24h": 2, "open_interest_usd": 500, "volume_usd_24h": 50, "volume_change_percent_24h": -3}]}
    if provider == "cryptoquant":
        field = "open_interest" if endpoint_id == "open_interest" else "funding_rates"
        return {"status": {"code": 200}, "result": {"window": params["window"], "data": [{"date": "2027-01-15T08:00:00Z", field: 10}]}}
    if provider == "glassnode":
        return [{"t": NOW, "v": 10}]
    raise AssertionError((provider, endpoint_id))


def _raw(fetcher=_fetcher, **kwargs):
    return OpenInterestAndFundingRawExtractor(fetcher).extract(mode=kwargs.pop("mode", "bootstrap"), reference_timestamp=NOW,
        execution_timestamp=NOW + 10, data_mode="synthetic", is_demo=True, **kwargs)


def test_incremental_limits_and_overlap():
    plan = build_open_interest_and_funding_fetch_plan(mode="incremental", reference_timestamp=NOW)
    primary = [row for row in plan if row["request_kind"] == "timeframe_series"]
    assert [(row["metric_id"], row["timeframe"]) for row in primary] == [
        ("open_interest_ohlc", "1m"), ("funding_rate_ohlc", "1m")
    ]
    for row in primary:
        assert row["params"]["limit"] == INCREMENTAL_LIMITS["1m"]
        assert row["from_timestamp"] < row["to_timestamp"]
    assert [row["metric_id"] for row in plan if row["request_kind"] == "snapshot"] == [
        "open_interest_exchange_list", "funding_rate_exchange_list"
    ]


def test_recovery_is_explicit_and_validated():
    request = {"metric_id": "open_interest_ohlc", "timeframe": "1h", "start_timestamp": 10_000, "end_timestamp": 20_000}
    plan = build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW, recovery_requests=[request])
    assert len(plan) == 1 and plan[0]["request_kind"] == "timeframe_series"
    assert plan[0]["from_timestamp"] == 6_400 and plan[0]["to_timestamp"] == 23_600
    for bad in ({**request, "metric_id": "options_info"}, {**request, "timeframe": "2h"}, {**request, "start_timestamp": 30_000}):
        with pytest.raises(ValueError):
            build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW, recovery_requests=[bad])


def test_provider_parameters_are_exact():
    cg = build_coinglass_history_params(timeframe="1h", limit=5, start_timestamp=1, end_timestamp=2)
    assert cg == {"symbol": "BTC", "interval": "1h", "limit": 5, "start_time": 1000, "end_time": 2000}
    cq = build_cryptoquant_params(from_timestamp=0, to_timestamp=NOW, limit=5)
    assert cq["exchange"] == "all_exchange" and cq["window"] == "hour" and cq["format"] == "json" and "T" in cq["from"]
    assert build_glassnode_params(from_timestamp=1, to_timestamp=2) == {"a": "BTC", "i": "1h", "s": 1, "u": 2}
    plan = build_open_interest_and_funding_fetch_plan(mode="bootstrap", reference_timestamp=NOW)
    funding_snapshot = next(row for row in plan if row["metric_id"] == "funding_rate_exchange_list")
    assert funding_snapshot["params"] == {}


def test_data_mode_and_clocks():
    with pytest.raises(ValueError):
        OpenInterestAndFundingRawExtractor(_fetcher).extract(mode="bootstrap", reference_timestamp=NOW, data_mode="synthetic", is_demo=False)
    raw = _raw()
    assert raw["context"]["reference_timestamp"] == NOW
    assert raw["context"]["execution_timestamp"] == NOW + 10
    assert raw["context"]["requested_at"] == "2027-01-15T08:00:10Z"


def test_timestamp_numeric_and_iso_contract():
    assert normalize_timestamp_utc(NOW * 1000) == NOW
    assert normalize_timestamp_utc(float(NOW)) == NOW
    assert normalize_timestamp_utc("2027-01-15T08:00:00Z") == NOW
    assert normalize_timestamp_utc("2027-01-15") == 1_799_971_200
    for value in (True, -1, 1.5, "", "bad", math.nan, math.inf):
        with pytest.raises(ValueError):
            normalize_timestamp_utc(value)


def test_ohlc_contract_negative_funding_and_negative_oi():
    row = {"time": NOW * 1000, "open": -0.02, "high": 0.01, "low": -0.03, "close": -0.01}
    assert normalize_coinglass_ohlc_record(row, metric_id="funding_rate_ohlc")["open"] == -0.02
    with pytest.raises(ValueError, match="negative_value"):
        normalize_coinglass_ohlc_record(row, metric_id="open_interest_ohlc")
    with pytest.raises(ValueError, match="inconsistent_ohlc"):
        normalize_coinglass_ohlc_record({**row, "open": 1, "close": 2, "high": 1, "low": 0}, metric_id="funding_rate_ohlc")


def test_upsert_orders_deduplicates_and_does_not_mutate():
    existing = [{"timestamp": 2, "value": 1}, {"timestamp": 1, "value": 1}]
    incoming = [{"timestamp": 2, "value": 9}, {"timestamp": 3, "value": 3}]
    before = copy.deepcopy((existing, incoming))
    assert upsert_records_by_timestamp(existing, incoming) == [{"timestamp": 1, "value": 1}, {"timestamp": 2, "value": 9}, {"timestamp": 3, "value": 3}]
    assert (existing, incoming) == before


def test_funding_snapshot_flattens_filters_and_keeps_negative():
    rows, invalid = normalize_funding_exchange_response(_fetcher(provider="coinglass", endpoint_id="funding_rate_exchange_list", path="", params={})["data"])
    assert not invalid and {(row["margin_type"], row["funding_rate_percent"]) for row in rows} == {("stablecoin", 0.01), ("token", -0.02)}
    assert all(row["symbol"] == "BTC" and row["next_funding_timestamp"] == NOW for row in rows)


def test_full_input_shape_snapshots_confirmations_and_strict_json():
    output = run_open_interest_and_funding_input(fetcher=_fetcher, reference_timestamp=NOW, requested_mode="bootstrap",
        execution_timestamp=NOW + 10, data_mode="synthetic", is_demo=True)
    assert output["family"] == "open_interest_and_funding" and output["stage"] == "input"
    assert output["snapshots"]["open_interest_by_exchange"]["aggregate_record"]["exchange"] == "All"
    assert output["snapshots"]["options_open_interest"]["aggregate_record"]["exchange"] == "All"
    assert output["confirmations"]["open_interest"]["cryptoquant"]["provider_window"] == "hour"
    assert output["confirmations"]["open_interest"]["glassnode"]["provider_interval"] == "1h"
    assert "open" not in output["confirmations"]["open_interest"]["glassnode"]["records"][0]
    assert output["availability"]["open_interest_market_cap_ratio"]["reason"] == "market_cap_source_not_configured"
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)


def test_invalid_record_is_partial_and_does_not_remove_history():
    existing = run_open_interest_and_funding_input(fetcher=_fetcher, reference_timestamp=NOW, requested_mode="bootstrap",
        execution_timestamp=NOW, data_mode="synthetic", is_demo=True)
    def bad_fetcher(**kwargs):
        if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" and kwargs["params"].get("interval") == "1m":
            return _coinglass_ohlc(value=-1)
        return _fetcher(**kwargs)
    output = run_open_interest_and_funding_input(fetcher=bad_fetcher, reference_timestamp=NOW + 3600, requested_mode="incremental",
        existing_state=existing, execution_timestamp=NOW + 3600, data_mode="synthetic", is_demo=True)
    payload = output["series"]["open_interest_ohlc"]["timeframes"]["1m"]
    assert payload["status"] == "partial" and payload["records"][0]["open"] == 100.0 and payload["incoming_invalid_count"] == 1


def test_snapshot_failure_preserves_history_and_optional_absence_is_not_invalid():
    existing = preprocess_open_interest_and_funding_raw(_raw())
    def failing(**kwargs):
        if kwargs["endpoint_id"] in {"open_interest_exchange_list", "options_info"}:
            raise RuntimeError("offline")
        return _fetcher(**kwargs)
    output = run_open_interest_and_funding_input(fetcher=failing, reference_timestamp=NOW + 1, requested_mode="incremental",
        existing_state=existing, execution_timestamp=NOW + 1, data_mode="synthetic", is_demo=True)
    assert output["snapshots"]["open_interest_by_exchange"]["status"] == "partial"
    assert output["snapshots"]["open_interest_by_exchange"]["stale"] is True
    # Options are intentionally not requested on a normal incremental cycle;
    # the persisted snapshot is reused rather than reported as a provider failure.
    assert output["snapshots"]["options_open_interest"]["status"] == "available"
    assert output["snapshots"]["options_open_interest"]["stale"] is True
    assert "not_refreshed_this_cycle" in output["snapshots"]["options_open_interest"]["warnings"]
    assert output["quality"]["status"] == "partial"


def test_empty_and_invalid_envelopes_are_not_available():
    def empty(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc":
            return {"code": 0, "data": []}
        return response
    unavailable = preprocess_open_interest_and_funding_raw(_raw(empty))["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    assert unavailable["status"] == "unavailable"
    def invalid(**kwargs):
        response = _fetcher(**kwargs)
        return {"code": 0, "data": {}} if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" else response
    output = preprocess_open_interest_and_funding_raw(_raw(invalid))
    assert output["series"]["open_interest_ohlc"]["timeframes"]["1h"]["status"] == "invalid"
    assert output["quality"]["status"] == "invalid"


def test_gaps_create_recovery_requests():
    def gap_fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" and kwargs["params"].get("interval") == "1h":
            response = {"code": 0, "data": _coinglass_ohlc(NOW - 7200)["data"] + _coinglass_ohlc(NOW)["data"]}
        return response
    output = preprocess_open_interest_and_funding_raw(_raw(gap_fetcher))
    payload = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    assert payload["status"] == "partial" and payload["gaps"][0]["missing_records"] == 1
    assert {"metric_id": "open_interest_ohlc", "timeframe": "1h", "start_timestamp": NOW - 3600, "end_timestamp": NOW - 3600} in output["quality"]["recovery_requests"]


def test_mode_determination_and_foreign_state_rejection():
    assert determine_open_interest_and_funding_input_mode() == "bootstrap"
    assert determine_open_interest_and_funding_input_mode(requested_mode="recovery") == "recovery"
    assert determine_open_interest_and_funding_input_mode(recovery_requests=[{}]) == "recovery"
    output = preprocess_open_interest_and_funding_raw(_raw())
    # A deliberately tiny fixture cannot complete every derived timeframe, so
    # automatic mode detection correctly stays in bootstrap until all bases exist.
    assert determine_open_interest_and_funding_input_mode(existing_state={"input": output}) == "bootstrap"
    complete = copy.deepcopy(output)
    for metric in ("open_interest_ohlc", "funding_rate_ohlc"):
        frames = complete["series"][metric]["timeframes"]
        seed = copy.deepcopy(frames["1m"]["records"][0])
        for frame in frames.values():
            if not frame["records"]:
                frame["records"] = [copy.deepcopy(seed)]
    assert determine_open_interest_and_funding_input_mode(existing_state={"input": complete}) == "incremental"
    with pytest.raises(ValueError):
        determine_open_interest_and_funding_input_mode(existing_state={"family": "prices_ohlcv", "stage": "input"})


def test_numeric_safety_and_negative_zero():
    assert normalize_finite_float(-0.0) == 0.0 and math.copysign(1, normalize_finite_float(-0.0)) == 1
    for value in (True, None, {}, math.nan, math.inf, -math.inf, "no"):
        with pytest.raises(ValueError):
            normalize_finite_float(value)


@pytest.mark.parametrize("message,secrets", [
    ("api_key=KEY", ("KEY",)),
    ("apikey=KEY token=TOK", ("KEY", "TOK")),
    ("access_token=ACCESS Bearer SECRET", ("ACCESS", "SECRET")),
    ("Authorization: Bearer SECRET api-key=KEY token=TOK", ("SECRET", "KEY", "TOK")),
    ("authorization=Bearer SECRET secret=KEY", ("SECRET", "KEY")),
])
def test_secret_redaction_removes_values_from_all_public_forms(message, secrets):
    def failing(**kwargs):
        del kwargs
        raise RuntimeError(message)
    output = _raw(failing)
    rendered = (repr(output), json.dumps(output, ensure_ascii=False, allow_nan=False))
    assert all(secret not in text for secret in secrets for text in rendered)
    error_message = output["raw"]["series"]["open_interest_ohlc"]["timeframes"]["1m"]["error"]["message"]
    assert all(secret not in error_message for secret in secrets)


def test_future_coinglass_records_are_rejected_before_upsert():
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" and kwargs["params"].get("interval") == "1h":
            response = {"code": 0, "data": _coinglass_ohlc(NOW)["data"] + _coinglass_ohlc(NOW + 3600, 200)["data"]}
        return response
    payload = preprocess_open_interest_and_funding_raw(_raw(fetcher))["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    assert payload["status"] == "partial" and payload["last_timestamp"] == NOW
    assert payload["invalid_records"][-1]["reason"] == "timestamp_after_reference_timestamp"
    assert all(record["timestamp"] <= NOW for record in payload["records"])


def test_only_future_coinglass_record_is_invalid():
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        return _coinglass_ohlc(NOW + 3600) if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" else response
    payload = preprocess_open_interest_and_funding_raw(_raw(fetcher))["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    assert payload["status"] == "invalid" and payload["records"] == [] and payload["last_timestamp"] is None


def test_future_incoming_preserves_existing_history():
    existing = preprocess_open_interest_and_funding_raw(_raw())
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        return _coinglass_ohlc(NOW + 3600, 999) if kwargs["endpoint_id"] == "aggregated_open_interest_ohlc" else response
    output = run_open_interest_and_funding_input(fetcher=fetcher, reference_timestamp=NOW, requested_mode="incremental",
        existing_state=existing, execution_timestamp=NOW, data_mode="synthetic", is_demo=True)
    payload = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    assert payload["status"] == "partial" and payload["last_timestamp"] == NOW and payload["records"][0]["open"] == 100.0


@pytest.mark.parametrize("provider,endpoint_id,container", [
    ("cryptoquant", "open_interest", ("open_interest", "cryptoquant")),
    ("cryptoquant", "funding_rates", ("funding_rate", "cryptoquant")),
    ("glassnode", "futures_open_interest_sum", ("open_interest", "glassnode")),
    ("glassnode", "futures_funding_rate_perpetual", ("funding_rate", "glassnode")),
])
def test_future_confirmation_records_are_invalid(provider, endpoint_id, container):
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["provider"] == provider and kwargs["endpoint_id"] == endpoint_id:
            if provider == "cryptoquant":
                field = "open_interest" if endpoint_id == "open_interest" else "funding_rates"
                return {"status": {"code": 200}, "result": {"window": "hour", "data": [{"date": NOW + 1, field: 1}]}}
            return [{"t": NOW + 1, "v": 1}]
        return response
    payload = preprocess_open_interest_and_funding_raw(_raw(fetcher))["confirmations"][container[0]][container[1]]
    assert payload["status"] == "invalid" and payload["records"] == []
    assert payload["invalid_records"][0]["reason"] == "timestamp_after_reference_timestamp"


def test_future_next_funding_timestamp_remains_valid_snapshot_metadata():
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "funding_rate_exchange_list":
            for row in response["data"][0]["stablecoin_margin_list"] + response["data"][0]["token_margin_list"]:
                row["next_funding_time"] = (NOW + 3600) * 1000
        return response
    output = preprocess_open_interest_and_funding_raw(_raw(fetcher))
    records = output["snapshots"]["funding_rate_by_exchange"]["records"]
    assert records and all(record["next_funding_timestamp"] == NOW + 3600 for record in records)


@pytest.mark.parametrize("symbols,expected,warned", [
    (("BTC", "ETH"), {"BTC"}, True),
    (("BTC", "btc"), {"BTC"}, False),
    (("ETH",), set(), True),
    ((None, "BTCUSDT"), set(), True),
])
def test_open_interest_snapshot_filters_to_btc(symbols, expected, warned):
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "open_interest_exchange_list":
            template = copy.deepcopy(response["data"][0])
            response["data"] = [{**template, "exchange": "All" if index == 0 else "Binance", "symbol": symbol}
                for index, symbol in enumerate(symbols)]
        return response
    payload = preprocess_open_interest_and_funding_raw(_raw(fetcher))["snapshots"]["open_interest_by_exchange"]
    assert {record["symbol"] for record in payload["records"]} == expected
    assert ("non_btc_records_filtered" in payload["warnings"]) is warned
    assert payload["aggregate_record"] is None or payload["aggregate_record"]["symbol"] == "BTC"


def test_eth_all_never_becomes_btc_aggregate():
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["endpoint_id"] == "open_interest_exchange_list":
            eth = copy.deepcopy(response["data"][0])
            eth["symbol"] = "ETH"
            btc = copy.deepcopy(response["data"][0])
            btc.update(exchange="Binance", symbol="BTC")
            response["data"] = [eth, btc]
        return response
    payload = preprocess_open_interest_and_funding_raw(_raw(fetcher))["snapshots"]["open_interest_by_exchange"]
    assert payload["aggregate_record"] is None and [record["symbol"] for record in payload["records"]] == ["BTC"]


def test_recovery_rejects_future_ranges_and_clamps_overlap():
    base = {"metric_id": "open_interest_ohlc", "timeframe": "1h"}
    for request in ({**base, "start_timestamp": NOW + 1, "end_timestamp": NOW + 2},
                    {**base, "start_timestamp": NOW - 1, "end_timestamp": NOW + 1}):
        with pytest.raises(ValueError, match="recovery_range_after_reference_timestamp"):
            build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW, recovery_requests=[request])
    plan = build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW,
        recovery_requests=[{**base, "start_timestamp": NOW, "end_timestamp": NOW}])
    assert plan[0]["to_timestamp"] == NOW
    zero = build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW,
        recovery_requests=[{**base, "start_timestamp": 0, "end_timestamp": 0}])
    assert zero[0]["from_timestamp"] == 0


def test_recovery_deduplicates_stably_without_merging_distinct_requests():
    first = {"metric_id": "open_interest_ohlc", "timeframe": "1h", "start_timestamp": 10_000, "end_timestamp": 20_000}
    different_metric = {**first, "metric_id": "funding_rate_ohlc"}
    different_range = {**first, "start_timestamp": 30_000, "end_timestamp": 40_000}
    requests = [first, different_metric, first, different_range, first]
    left = build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW, recovery_requests=requests)
    right = build_open_interest_and_funding_fetch_plan(mode="recovery", reference_timestamp=NOW, recovery_requests=requests)
    assert left == right and len(left) == 3
    assert len({request["request_id"] for request in left}) == len(left)


@pytest.mark.parametrize("window", [pytest.param("missing", id="missing"), None, "", " ", "day", "min", 1])
def test_cryptoquant_requires_exact_hour_window(window):
    result = {"data": []}
    if window != "missing":
        result["window"] = window
    with pytest.raises(ValueError, match="invalid_cryptoquant_window"):
        unwrap_cryptoquant_response({"status": {"code": 200}, "result": result})
    assert unwrap_cryptoquant_response({"status": {"code": 200}, "result": {"window": "hour", "data": []}}) == ("hour", [])


def test_invalid_cryptoquant_window_produces_invalid_confirmation():
    def fetcher(**kwargs):
        response = _fetcher(**kwargs)
        if kwargs["provider"] == "cryptoquant":
            response["result"]["window"] = "day"
        return response
    output = preprocess_open_interest_and_funding_raw(_raw(fetcher))
    assert output["confirmations"]["open_interest"]["cryptoquant"]["status"] == "invalid"


def test_all_confirmations_always_expose_contractual_provenance():
    expected = {
        ("open_interest", "cryptoquant"): ("cryptoquant", "open_interest", "USD", "hour"),
        ("funding_rate", "cryptoquant"): ("cryptoquant", "funding_rates", "percent", "hour"),
        ("open_interest", "glassnode"): ("glassnode", "futures_open_interest_sum", "USD", "1h"),
        ("funding_rate", "glassnode"): ("glassnode", "futures_funding_rate_perpetual", "percent", "1h"),
    }
    for source in (_raw(), _raw(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))):
        output = preprocess_open_interest_and_funding_raw(source)
        for (metric, provider), values in expected.items():
            payload = output["confirmations"][metric][provider]
            interval = payload["provider_window"] if provider == "cryptoquant" else payload["provider_interval"]
            assert (payload["provider"], payload["endpoint_id"], payload["unit"], interval) == values
            assert isinstance(payload["records"], list) and payload["status"] in {"available", "partial", "unavailable", "invalid"}


def test_all_fetch_plans_have_unique_request_ids_and_output_is_causal_strict_json():
    for mode in ("bootstrap", "incremental"):
        plan = build_open_interest_and_funding_fetch_plan(mode=mode, reference_timestamp=NOW)
        assert len(plan) == len({request["request_id"] for request in plan})
    output = preprocess_open_interest_and_funding_raw(_raw())
    for series in output["series"].values():
        for payload in series["timeframes"].values():
            assert all(record["timestamp"] <= NOW for record in payload["records"])
    assert set(record["symbol"] for record in output["snapshots"]["open_interest_by_exchange"]["records"]) <= {"BTC"}
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)
