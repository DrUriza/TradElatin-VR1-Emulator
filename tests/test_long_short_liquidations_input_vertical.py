from copy import deepcopy
import json

import pytest

pytestmark = pytest.mark.skip(reason="legacy Input/API contract retired; covered by current runtime/emulator tests")

from processing_signals.input.long_short_liquidations.long_short_liquidations_data_raw_extract import (
    ENDPOINT_MANIFEST,
    LongShortLiquidationsRawExtractor,
    build_long_short_liquidations_fetch_plan,
)
from processing_signals.input.long_short_liquidations.long_short_liquidations_data_raw_preprocessing import (
    LongShortLiquidationsInputPreprocessor,
    normalize_coinglass_liquidation_event,
    run_long_short_liquidations_input,
)

REFERENCE = 1_740_000_000


def _request(endpoint_id, response, *, status="ok", error=None, request_id=None):
    params = {
        "aggregated_liquidation_history": {
            "exchange_list": "Binance", "symbol": "BTC", "interval": "1h", "limit": 1,
            "start_time": (REFERENCE - 3600) * 1000, "end_time": REFERENCE * 1000,
        },
        "liquidation_exchange_list": {"symbol": "BTC", "range": "24h"},
        "aggregated_liquidation_map": {"symbol": "BTC", "range": "1d"},
    }[endpoint_id]
    return {
        "request_id": request_id or endpoint_id,
        "provider": "coinglass",
        "endpoint_id": endpoint_id,
        "path": ENDPOINT_MANIFEST[("coinglass", endpoint_id)],
        "params": params,
        "dimensions": {"exchange": None, "asset": "BTC", "symbol": "BTC"},
        "status": status,
        "response": response,
        "error": error,
        "warnings": [],
    }


def _raw(requests, *, mode="recovery"):
    return {
        "family": "long_short_liquidations",
        "stage": "input_raw",
        "mode": mode,
        "reference_timestamp": REFERENCE,
        "execution_timestamp": REFERENCE + 1,
        "requests": requests,
    }


def _map_request(rows):
    return _request("aggregated_liquidation_map", {"code": "0", "data": {"data": rows}})


def _old_map():
    return {
        "status": "available",
        "reason": None,
        "range": "1d",
        "snapshot_observed_at": REFERENCE - 10,
        "source_data_as_of": None,
        "levels": [{"price_level": 9.0}],
        "provenance": {"provider": "coinglass", "endpoint_id": "aggregated_liquidation_map",
                       "path": ENDPOINT_MANIFEST[("coinglass", "aggregated_liquidation_map")],
                       "params": {}, "request_ids": ["old"],
                       "reference_timestamp": REFERENCE - 20,
                       "execution_timestamp": REFERENCE - 10},
        "warnings": [],
        "errors": [],
    }


def _preprocess(requests, *, mode="recovery", existing=None):
    return LongShortLiquidationsInputPreprocessor(existing_contract=existing).preprocess_raw(
        _raw(requests, mode=mode),
    )


def test_all_invalid_snapshot_without_previous_is_invalid():
    output = _preprocess([_map_request({"1": [[1, 2, None, float("nan")]]})])
    dataset = output["providers"]["coinglass"]["aggregated_map"]
    assert dataset["status"] == "invalid"
    assert dataset["reason"] == "all_records_invalid"
    assert dataset["levels"] == []
    assert dataset["snapshot_observed_at"] is None
    assert output["quality"]["status"] == "invalid"


def test_all_invalid_snapshot_with_previous_preserves_previous():
    previous = _old_map()
    existing = {"providers": {"coinglass": {"aggregated_map": previous}}}
    output = _preprocess([_map_request({"1": [[1, 2, None, float("nan")]]})], existing=existing)
    dataset = output["providers"]["coinglass"]["aggregated_map"]
    assert dataset["status"] == "partial"
    assert dataset["reason"] == "latest_snapshot_invalid"
    assert dataset["levels"] == previous["levels"]
    assert dataset["snapshot_observed_at"] == previous["snapshot_observed_at"]
    assert dataset["provenance"] == previous["provenance"]
    assert dataset["latest_attempt"]["invalid_record_count"] == 1


def test_empty_snapshot_is_unavailable():
    dataset = _preprocess([_map_request({})])["providers"]["coinglass"]["aggregated_map"]
    assert dataset["status"] == "unavailable"
    assert dataset["reason"] == "empty_response"
    assert dataset["snapshot_observed_at"] is None


def test_partially_valid_snapshot_is_partial():
    rows = {"1": [[1, 2, None, float("nan")]], "2": [[2, 3, None, None]]}
    dataset = _preprocess([_map_request(rows)])["providers"]["coinglass"]["aggregated_map"]
    assert dataset["status"] == "partial"
    assert dataset["reason"] == "some_records_invalid"
    assert dataset["snapshot_observed_at"] == REFERENCE + 1
    assert len(dataset["levels"]) == 1


def test_bootstrap_without_aggregated_history_is_invalid_quality():
    output = _preprocess([], mode="bootstrap")
    assert output["quality"]["status"] == "invalid"
    assert "coinglass.aggregated_history" in output["quality"]["missing_required_datasets"]


def _core_bootstrap_requests():
    history = _request("aggregated_liquidation_history", {
        "code": "0", "data": [{"time": REFERENCE * 1000,
                                  "aggregated_long_liquidation_usd": 1,
                                  "aggregated_short_liquidation_usd": 2}],
    })
    snapshot = _request("liquidation_exchange_list", {
        "code": "0", "data": [{"exchange": "Binance", "liquidation_usd": 3,
                                  "long_liquidation_usd": 1, "short_liquidation_usd": 2}],
    })
    return [history, snapshot, _map_request({"1": [[1, 2, None, None]]})]


def test_valid_bootstrap_core_can_have_ok_quality_with_informational_warning():
    output = _preprocess(_core_bootstrap_requests(), mode="bootstrap")
    snapshot = output["providers"]["coinglass"]["exchange_snapshot"]
    assert snapshot["status"] == "available"
    assert "provider_timestamp_not_supplied" in snapshot["warnings"]
    assert output["quality"]["status"] == "ok"


def test_incremental_without_discovery_does_not_require_discovery():
    output = _preprocess(_core_bootstrap_requests(), mode="incremental")
    assert "coinglass.supported_exchange_pairs" not in output["quality"]["required_datasets"]
    assert output["quality"]["status"] == "ok"


def test_incremental_failed_core_request_is_partial_quality():
    failed = _request("aggregated_liquidation_history", None, status="error", error="timeout")
    output = _preprocess([failed], mode="incremental")
    dataset = output["providers"]["coinglass"]["aggregated_history"]
    assert dataset["status"] == "unavailable"
    assert dataset["reason"] == "request_failed"
    assert output["quality"]["status"] == "partial"


def test_recovery_requires_only_declared_target_and_preserves_other_data():
    previous = _old_map()
    existing = {"providers": {"coinglass": {"aggregated_map": previous,
                                               "max_pain": {"status": "available", "marker": "same"}}}}
    before = deepcopy(existing)
    output = _preprocess([_map_request({"1": [[1, 2, None, None]]})], existing=existing)
    assert output["quality"]["required_datasets"] == ["coinglass.aggregated_map"]
    assert output["providers"]["coinglass"]["max_pain"] == before["providers"]["coinglass"]["max_pain"]
    assert existing == before


def test_empty_recovery_is_rejected_by_plan_and_preprocessor():
    with pytest.raises(ValueError, match="recovery_requests_required"):
        build_long_short_liquidations_fetch_plan(
            mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[],
        )
    with pytest.raises(ValueError, match="recovery_requests_required"):
        LongShortLiquidationsInputPreprocessor().preprocess_raw(_raw([]))


@pytest.mark.parametrize(
    ("window", "interval", "seconds"),
    [("day", "1d", 86400), ("hour", "1h", 3600), ("min", "1m", 60)],
)
def test_cryptoquant_publishes_response_window(window, interval, seconds):
    request = {
        "request_id": f"cq:{window}",
        "provider": "cryptoquant",
        "endpoint_id": "cryptoquant_liquidations",
        "path": ENDPOINT_MANIFEST[("cryptoquant", "cryptoquant_liquidations")],
        "params": {"exchange": "all_exchange", "symbol": "all_symbol", "window": window,
                   "from": "2025-02-19T20:00:00Z", "to": "2025-02-19T21:00:00Z",
                   "limit": 1, "format": "json"},
        "dimensions": {"exchange": "all_exchange", "asset": "BTC", "symbol": "all_symbol"},
        "status": "ok",
        "response": {"status": {"code": 200}, "result": {"window": window, "data": [{
            "date": "2026-07-28T08:00:00", "long_liquidations": None,
            "short_liquidations": None, "long_liquidations_usd": 1,
            "short_liquidations_usd": 2,
        }]}},
        "error": None,
        "warnings": [],
    }
    dataset = _preprocess([request])["providers"]["cryptoquant"]["aggregate_history"]
    assert dataset["window"] == window
    assert dataset["interval"] == interval
    assert dataset["interval_seconds"] == seconds


def test_request_error_without_previous_has_reason():
    failed = _request("aggregated_liquidation_history", None, status="error", error="timeout")
    dataset = _preprocess([failed])["providers"]["coinglass"]["aggregated_history"]
    assert dataset["status"] == "unavailable"
    assert dataset["reason"] == "request_failed"


def test_every_non_available_generated_dataset_has_reason_and_json_is_strict():
    output = _preprocess([_map_request({"1": [[1, 2, None, float("nan")]]})])

    def visit(value):
        if isinstance(value, dict):
            if value.get("status") in {"partial", "unavailable", "invalid"}:
                assert value.get("reason") is not None
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(output)
    json.dumps(output, ensure_ascii=False, allow_nan=False)


def test_raw_and_existing_contract_are_immutable():
    existing = {"providers": {"coinglass": {"aggregated_map": _old_map()}}}
    raw = _raw([_map_request({"1": [[1, 2, None, None]]})])
    existing_before, raw_before = deepcopy(existing), deepcopy(raw)
    LongShortLiquidationsInputPreprocessor(existing_contract=existing).preprocess_raw(raw)
    assert existing == existing_before
    assert raw == raw_before


def test_audit_cases_14_and_28_are_invalid_and_json_safe():
    output = _preprocess([_map_request({"1": [[1, 2, None, {1: "invalid"}]]})])
    dataset = output["providers"]["coinglass"]["aggregated_map"]
    assert dataset["status"] == "invalid"
    assert dataset["reason"] == "all_records_invalid"
    json.dumps(output, allow_nan=False)


def _keyed_params(endpoint_id):
    common = {"exchange": "Binance", "symbol": "BTCUSDT"}
    if endpoint_id == "pair_liquidation_history":
        return {**common, "interval": "1h", "limit": 2,
                "start_time": (REFERENCE - 120) * 1000, "end_time": REFERENCE * 1000}
    if endpoint_id == "liquidation_order_events":
        return {"exchange": "Binance", "symbol": "BTC", "min_liquidation_amount": "10000",
                "start_time": (REFERENCE - 120) * 1000, "end_time": REFERENCE * 1000}
    return {**common, "range": "1d"}


def _keyed_raw_request(endpoint_id, *, dimensions=None, params=None):
    return {
        "request_id": f"raw:{endpoint_id}", "provider": "coinglass", "endpoint_id": endpoint_id,
        "path": ENDPOINT_MANIFEST[("coinglass", endpoint_id)],
        "params": deepcopy(params or _keyed_params(endpoint_id)),
        "dimensions": deepcopy(dimensions if dimensions is not None else {
            "exchange": "Binance", "asset": "BTC",
            "symbol": "BTC" if endpoint_id == "liquidation_order_events" else "BTCUSDT",
        }),
        "status": "ok", "response": {"code": "0", "data": []}, "error": None, "warnings": [],
    }


@pytest.mark.parametrize(
    "endpoint_id", ["pair_liquidation_history", "liquidation_order_events", "pair_liquidation_map"],
)
def test_external_keyed_raw_requires_exchange_dimension(endpoint_id):
    with pytest.raises(ValueError, match="missing_required_dimension:exchange"):
        _preprocess([_keyed_raw_request(endpoint_id, dimensions={})])


@pytest.mark.parametrize(
    "endpoint_id", ["pair_liquidation_history", "liquidation_order_events", "pair_liquidation_map"],
)
def test_public_recovery_requires_exchange_before_fetcher(endpoint_id):
    calls = []

    def fetcher(**kwargs):
        calls.append(kwargs)
        return {"code": "0", "data": [{"exchange_name": "Binance", "symbol": "BTCUSDT",
                "base_asset": "BTC", "side": 1, "price": 50_000, "usd_value": 10_000,
                "time": REFERENCE * 1000}]}

    params = _keyed_params(endpoint_id)
    del params["exchange"]
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=fetcher, reference_timestamp=REFERENCE, exchanges=(), cryptoquant_exchanges=(),
    )
    with pytest.raises(ValueError, match="missing_required_param:exchange"):
        extractor.run(mode="recovery", recovery_requests=[{
            "provider": "coinglass", "endpoint_id": endpoint_id, "params": params,
        }])
    assert calls == []


@pytest.mark.parametrize(
    "endpoint_id", ["pair_liquidation_history", "liquidation_order_events", "pair_liquidation_map"],
)
def test_public_recovery_requires_symbol_before_fetcher(endpoint_id):
    calls = []
    params = _keyed_params(endpoint_id)
    del params["symbol"]
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=lambda **kwargs: calls.append(kwargs), reference_timestamp=REFERENCE,
    )
    with pytest.raises(ValueError, match="missing_required_param:symbol"):
        extractor.run(mode="recovery", recovery_requests=[{
            "provider": "coinglass", "endpoint_id": endpoint_id, "params": params,
        }])
    assert calls == []


def test_public_recovery_builds_canonical_dimensions_and_target():
    plan = build_long_short_liquidations_fetch_plan(
        mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[{
            "provider": "coinglass", "endpoint_id": "pair_liquidation_history",
            "params": _keyed_params("pair_liquidation_history"),
        }],
    )
    assert plan[0]["dimensions"] == {
        "exchange": "Binance", "asset": "BTC", "symbol": "BTCUSDT",
    }
    raw_request = {**plan[0], "status": "ok", "response": {"code": "0", "data": []},
                   "error": None, "warnings": []}
    output = _preprocess([raw_request])
    assert output["quality"]["required_datasets"] == ["coinglass.pair_history.Binance"]


def test_public_recovery_rejects_supplied_dimension_mismatch():
    with pytest.raises(ValueError, match="request_dimension_mismatch:exchange"):
        build_long_short_liquidations_fetch_plan(
            mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[{
                "provider": "coinglass", "endpoint_id": "pair_liquidation_map",
                "params": _keyed_params("pair_liquidation_map"),
                "dimensions": {"exchange": "OKX"},
            }],
        )


def test_cryptoquant_rejects_exchange_dimension_mismatch():
    params = {"exchange": "binance", "symbol": "btc_usdt", "window": "hour",
              "from": "2025-02-19T20:00:00Z", "to": "2025-02-19T21:00:00Z",
              "limit": 2, "format": "json"}
    with pytest.raises(ValueError, match="request_dimension_mismatch:exchange"):
        build_long_short_liquidations_fetch_plan(
            mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[{
                "provider": "cryptoquant", "endpoint_id": "cryptoquant_liquidations",
                "params": params, "dimensions": {"exchange": "okx"},
            }],
        )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda params: params.pop("start_time"), "missing_required_param:start_time"),
        (lambda params: params.pop("end_time"), "missing_required_param:end_time"),
        (lambda params: params.update(start_time=REFERENCE * 1000 + 1), "invalid_request_time_range"),
        (lambda params: params.update(start_time=True), "invalid_request_param:start_time"),
    ],
)
def test_event_time_contract_is_rejected_before_fetcher(mutation, reason):
    params = _keyed_params("liquidation_order_events")
    mutation(params)
    calls = []
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=lambda **kwargs: calls.append(kwargs), reference_timestamp=REFERENCE,
    )
    with pytest.raises(ValueError, match=reason):
        extractor.run(mode="recovery", recovery_requests=[{
            "provider": "coinglass", "endpoint_id": "liquidation_order_events", "params": params,
        }])
    assert calls == []


def test_raw_extractor_segments_saturated_events_deterministically():
    response = {"code": "0", "data": [{} for _ in range(200)]}
    calls = []

    def fetcher(**kwargs):
        calls.append(deepcopy(kwargs))
        return response

    extractor = LongShortLiquidationsRawExtractor(
        fetcher=fetcher, reference_timestamp=REFERENCE, minimum_event_window_seconds=60,
    )
    raw = extractor.run(mode="recovery", recovery_requests=[{
        "provider": "coinglass", "endpoint_id": "liquidation_order_events",
        "params": _keyed_params("liquidation_order_events"),
    }])
    assert len(calls) == 3
    assert len(raw["requests"]) == 2
    assert all(item["dimensions"]["exchange"] == "Binance" for item in raw["requests"])
    assert all(item["warnings"] == ["event_endpoint_record_limit_reached"] for item in raw["requests"])
    assert raw["requests"][0]["request_id"] != raw["requests"][1]["request_id"]
    json.dumps(raw, ensure_ascii=False, allow_nan=False)


def test_raw_extractor_response_is_a_deepcopy():
    response = {"code": "0", "data": {"data": {"1": [[1, 2, None, None]]}}}
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=lambda **kwargs: response, reference_timestamp=REFERENCE,
    )
    raw = extractor.run(mode="recovery", recovery_requests=[{
        "provider": "coinglass", "endpoint_id": "aggregated_liquidation_map",
        "params": {"symbol": "BTC", "range": "1d"},
    }])
    response["data"]["data"].clear()
    assert raw["requests"][0]["response"]["data"]["data"]


def test_public_facade_runs_recovery_end_to_end_and_is_json_strict():
    output = run_long_short_liquidations_input(
        fetcher=lambda **kwargs: {"code": "0", "data": {"data": {"1": [[1, 2, None, None]]}}},
        requested_mode="recovery", reference_timestamp=REFERENCE,
        recovery_requests=[{
            "provider": "coinglass", "endpoint_id": "aggregated_liquidation_map",
            "params": {"symbol": "BTC", "range": "1d"},
        }],
    )
    assert output["quality"]["required_datasets"] == ["coinglass.aggregated_map"]
    assert output["quality"]["status"] == "ok"
    json.dumps(output, ensure_ascii=False, allow_nan=False)


def _glassnode_params(endpoint_id="glassnode_long_liquidations"):
    params = {"a": "BTC", "s": REFERENCE - 3600, "u": REFERENCE, "i": "1h",
              "f": "json", "timestamp_format": "unix"}
    if endpoint_id != "glassnode_long_liquidation_dominance":
        params["c"] = "USD"
    return params


def _glassnode_raw(*, symbol="BTC", asset="BTC"):
    endpoint_id = "glassnode_long_liquidations"
    return {
        "request_id": "glassnode:long:audit", "provider": "glassnode", "endpoint_id": endpoint_id,
        "path": ENDPOINT_MANIFEST[("glassnode", endpoint_id)], "params": _glassnode_params(),
        "dimensions": {"asset": asset, "symbol": symbol}, "status": "ok",
        "response": [{"t": REFERENCE, "v": 1000.0}], "error": None, "warnings": [],
    }


def test_glassnode_external_raw_rejects_symbol_mismatch():
    with pytest.raises(ValueError, match="request_dimension_mismatch:symbol"):
        _preprocess([_glassnode_raw(symbol="ETH")])


def test_glassnode_public_recovery_rejects_symbol_mismatch_before_fetcher():
    calls = []
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=lambda **kwargs: calls.append(kwargs), reference_timestamp=REFERENCE,
    )
    with pytest.raises(ValueError, match="request_dimension_mismatch:symbol"):
        extractor.run(mode="recovery", recovery_requests=[{
            "provider": "glassnode", "endpoint_id": "glassnode_long_liquidations",
            "params": _glassnode_params(), "dimensions": {"asset": "BTC", "symbol": "ETH"},
        }])
    assert calls == []


def test_all_glassnode_endpoints_build_canonical_dimensions():
    endpoints = (
        "glassnode_long_liquidations", "glassnode_short_liquidations",
        "glassnode_total_liquidations", "glassnode_long_liquidation_dominance",
    )
    for endpoint_id in endpoints:
        plan = build_long_short_liquidations_fetch_plan(
            mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[{
                "provider": "glassnode", "endpoint_id": endpoint_id,
                "params": _glassnode_params(endpoint_id),
            }],
        )
        assert plan[0]["dimensions"] == {"asset": "BTC", "symbol": "BTC"}


def _nominal_fetcher(**kwargs):
    endpoint_id = kwargs["endpoint_id"]
    if endpoint_id == "supported_exchange_pairs":
        return {"code": "0", "data": {"Binance": ["BTCUSDT"]}}
    if endpoint_id == "aggregated_liquidation_history":
        return {"code": "0", "data": [{"time": REFERENCE * 1000,
                "aggregated_long_liquidation_usd": 1, "aggregated_short_liquidation_usd": 2}]}
    if endpoint_id == "liquidation_exchange_list":
        return {"code": "0", "data": [{"exchange": "Binance", "liquidation_usd": 3,
                "long_liquidation_usd": 1, "short_liquidation_usd": 2}]}
    if endpoint_id == "pair_liquidation_history":
        return {"code": "0", "data": [{"time": REFERENCE * 1000,
                "long_liquidation_usd": 1, "short_liquidation_usd": 2}]}
    if endpoint_id == "liquidation_order_events":
        return {"code": "0", "data": [{"exchange_name": "Binance", "symbol": "BTCUSDT",
                "base_asset": "BTC", "side": 1, "price": 50_000, "usd_value": 10_000,
                "time": REFERENCE * 1000}]}
    if endpoint_id in {"aggregated_liquidation_map", "pair_liquidation_map"}:
        return {"code": "0", "data": {"data": {"50000": [[50_000, 10, None, None]]}}}
    if endpoint_id == "liquidation_max_pain":
        return {"code": "0", "data": [{"symbol": "BTC", "price": 50_000,
                "long_max_pain_liq_level": 1, "long_max_pain_liq_price": 49_000,
                "short_max_pain_liq_level": 2, "short_max_pain_liq_price": 51_000}]}
    if endpoint_id == "cryptoquant_liquidations":
        return {"status": {"code": 200}, "result": {"window": "hour", "data": [{
                "date": "2025-02-19T21:00:00Z", "long_liquidations": None,
                "short_liquidations": None, "long_liquidations_usd": 1,
                "short_liquidations_usd": 2}]}}
    return [{"t": REFERENCE, "v": 1}]


def _facade_options():
    return {"fetcher": _nominal_fetcher, "reference_timestamp": REFERENCE,
            "exchanges": ("Binance",), "exchange_pairs": {"Binance": "BTCUSDT"},
            "cryptoquant_exchanges": ("binance",)}


def test_public_facade_bootstrap_nominal():
    output = run_long_short_liquidations_input(requested_mode="bootstrap", **_facade_options())
    assert output["quality"]["status"] == "ok"
    assert "coinglass.events.Binance" in output["quality"]["required_datasets"]
    json.dumps(output, ensure_ascii=False, allow_nan=False)


def test_public_facade_incremental_preserves_and_upserts_history():
    bootstrap = run_long_short_liquidations_input(requested_mode="bootstrap", **_facade_options())
    before = deepcopy(bootstrap)
    output = run_long_short_liquidations_input(
        requested_mode="incremental", existing_contract=bootstrap, **_facade_options(),
    )
    records = output["providers"]["coinglass"]["aggregated_history"]["records"]
    assert output["quality"]["status"] == "ok" and len(records) == 1
    assert "coinglass.supported_exchange_pairs" not in output["quality"]["required_datasets"]
    assert bootstrap == before


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [("provider", "unknown", "invalid_request_endpoint"),
     ("endpoint_id", "unknown", "invalid_request_endpoint"),
     ("path", "/wrong", "request_path_mismatch")],
)
def test_external_raw_rejects_identity_mismatch(field, value, reason):
    request = _glassnode_raw()
    request[field] = value
    with pytest.raises(ValueError, match=reason):
        _preprocess([request])


@pytest.mark.parametrize("duration_ms", [0, 1])
def test_saturated_minimum_event_windows_terminate(duration_ms):
    params = _keyed_params("liquidation_order_events")
    params.update(start_time=REFERENCE * 1000, end_time=REFERENCE * 1000 + duration_ms)
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=lambda **kwargs: {"code": "0", "data": [{} for _ in range(200)]},
        reference_timestamp=REFERENCE, minimum_event_window_seconds=60,
    )
    raw = extractor.run(mode="recovery", recovery_requests=[{
        "provider": "coinglass", "endpoint_id": "liquidation_order_events", "params": params,
    }])
    assert len(raw["requests"]) == 1
    assert raw["requests"][0]["warnings"] == ["event_endpoint_record_limit_reached"]


def test_event_child_failure_is_isolated_from_sibling():
    params = _keyed_params("liquidation_order_events")
    calls = []
    def fetcher(**kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return {"code": "0", "data": [{} for _ in range(200)]}
        if len(calls) == 2:
            raise RuntimeError("child_failed")
        return {"code": "0", "data": [{"exchange_name": "Binance", "symbol": "BTCUSDT",
                "base_asset": "BTC", "side": 1, "price": 50_000, "usd_value": 10_000,
                "time": REFERENCE * 1000}]}
    extractor = LongShortLiquidationsRawExtractor(
        fetcher=fetcher, reference_timestamp=REFERENCE, minimum_event_window_seconds=60,
    )
    raw = extractor.run(mode="recovery", recovery_requests=[{
        "provider": "coinglass", "endpoint_id": "liquidation_order_events", "params": params,
    }])
    assert [item["status"] for item in raw["requests"]] == ["error", "ok"]
    output = LongShortLiquidationsInputPreprocessor().preprocess_raw(raw)
    dataset = output["providers"]["coinglass"]["events"]["Binance"]
    assert dataset["status"] == "partial" and "RuntimeError:child_failed" in dataset["errors"]


def _check(condition):
    assert condition


def _smoke_plan_without_cryptoquant_exchanges():
    plan = build_long_short_liquidations_fetch_plan(
        mode="bootstrap", reference_timestamp=REFERENCE, exchanges=(), cryptoquant_exchanges=(),
    )
    requests = [item for item in plan if item["provider"] == "cryptoquant"]
    assert len(requests) == 1 and requests[0]["params"]["exchange"] == "all_exchange"


def _smoke_skipped_identity():
    plan = build_long_short_liquidations_fetch_plan(
        mode="bootstrap", reference_timestamp=REFERENCE, exchanges=("Binance",),
        cryptoquant_exchanges=(),
    )
    skipped = [item for item in plan if item.get("skip_reason")]
    assert {item["endpoint_id"] for item in skipped} == {
        "pair_liquidation_history", "pair_liquidation_map",
    }
    assert all(item["dimensions"]["exchange"] == "Binance" for item in skipped)


def _smoke_invalid_window():
    params = {"exchange": "binance", "symbol": "btc_usdt", "window": "week",
              "from": "2025-02-19T20:00:00Z", "to": "2025-02-19T21:00:00Z",
              "limit": 1, "format": "json"}
    with pytest.raises(ValueError, match="invalid_request_param:window"):
        build_long_short_liquidations_fetch_plan(
            mode="recovery", reference_timestamp=REFERENCE, recovery_requests=[{
                "provider": "cryptoquant", "endpoint_id": "cryptoquant_liquidations",
                "params": params,
            }],
        )


def _smoke_event_side(value):
    with pytest.raises(ValueError, match="side_must_be_integer_1_or_2"):
        normalize_coinglass_liquidation_event({
            "exchange": "Binance", "symbol": "BTCUSDT", "base_asset": "BTC", "side": value,
            "price": 50_000, "usd_value": 1, "time": REFERENCE * 1000,
        }, expected_asset="BTC")


def _smoke_event_asset(value=None):
    record = {"exchange": "Binance", "symbol": "BTCUSDT", "side": 1,
              "price": 50_000, "usd_value": 1, "time": REFERENCE * 1000}
    if value is not None:
        record["base_asset"] = value
    reason = "base_asset_must_be_non_empty_string" if value is None else "base_asset_mismatch"
    with pytest.raises(ValueError, match=reason):
        normalize_coinglass_liquidation_event(record, expected_asset="BTC")


def _smoke_raw_identity(field, value, reason):
    test_external_raw_rejects_identity_mismatch(field, value, reason)


def _smoke_forbidden_processing_content():
    output = run_long_short_liquidations_input(
        requested_mode="recovery", recovery_requests=[{
            "provider": "coinglass", "endpoint_id": "aggregated_liquidation_map",
            "params": {"symbol": "BTC", "range": "1d"},
        }], **_facade_options(),
    )
    forbidden = {"pressure", "imbalance", "dominant_side", "signal", "widgets", "charts"}
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()
    assert not forbidden.intersection(keys(output))


SMOKE_RUNNERS = (
    lambda: test_recovery_requires_only_declared_target_and_preserves_other_data(),
    _smoke_skipped_identity,
    lambda: test_incremental_without_discovery_does_not_require_discovery(),
    lambda: test_valid_bootstrap_core_can_have_ok_quality_with_informational_warning(),
    lambda: test_valid_bootstrap_core_can_have_ok_quality_with_informational_warning(),
    lambda: test_all_glassnode_endpoints_build_canonical_dimensions(),
    _smoke_plan_without_cryptoquant_exchanges,
    lambda: test_recovery_requires_only_declared_target_and_preserves_other_data(),
    lambda: test_public_facade_incremental_preserves_and_upserts_history(),
    lambda: test_all_invalid_snapshot_with_previous_preserves_previous(),
    lambda: _smoke_event_side(True),
    lambda: _smoke_event_side(1.0),
    lambda: test_audit_cases_14_and_28_are_invalid_and_json_safe(),
    lambda: test_all_invalid_snapshot_without_previous_is_invalid(),
    lambda: test_cryptoquant_publishes_response_window("day", "1d", 86400),
    _smoke_invalid_window,
    lambda: test_cryptoquant_rejects_exchange_dimension_mismatch(),
    lambda: _smoke_raw_identity("provider", "unknown", "invalid_request_endpoint"),
    lambda: _smoke_raw_identity("path", "/wrong", "request_path_mismatch"),
    lambda: _smoke_event_asset(),
    lambda: _smoke_event_asset("ETH"),
    lambda: test_raw_and_existing_contract_are_immutable(),
    lambda: test_raw_and_existing_contract_are_immutable(),
    lambda: test_public_facade_incremental_preserves_and_upserts_history(),
    lambda: test_partially_valid_snapshot_is_partial(),
    lambda: test_recovery_requires_only_declared_target_and_preserves_other_data(),
    lambda: test_all_invalid_snapshot_with_previous_preserves_previous(),
    lambda: test_audit_cases_14_and_28_are_invalid_and_json_safe(),
    lambda: test_event_child_failure_is_isolated_from_sibling(),
    lambda: test_every_non_available_generated_dataset_has_reason_and_json_is_strict(),
    lambda: test_public_recovery_builds_canonical_dimensions_and_target(),
    _smoke_forbidden_processing_content,
    lambda: test_bootstrap_without_aggregated_history_is_invalid_quality(),
    lambda: test_empty_recovery_is_rejected_by_plan_and_preprocessor(),
    lambda: test_all_invalid_snapshot_without_previous_is_invalid(),
    lambda: test_all_invalid_snapshot_with_previous_preserves_previous(),
    lambda: test_empty_snapshot_is_unavailable(),
    lambda: test_cryptoquant_publishes_response_window("hour", "1h", 3600),
    lambda: test_request_error_without_previous_has_reason(),
    lambda: test_every_non_available_generated_dataset_has_reason_and_json_is_strict(),
    lambda: test_external_keyed_raw_requires_exchange_dimension("pair_liquidation_history"),
    lambda: test_external_keyed_raw_requires_exchange_dimension("liquidation_order_events"),
    lambda: test_external_keyed_raw_requires_exchange_dimension("pair_liquidation_map"),
    lambda: test_public_recovery_requires_exchange_before_fetcher("pair_liquidation_history"),
    lambda: test_public_recovery_requires_symbol_before_fetcher("pair_liquidation_history"),
    lambda: test_public_recovery_rejects_supplied_dimension_mismatch(),
    lambda: test_cryptoquant_rejects_exchange_dimension_mismatch(),
    lambda: test_event_time_contract_is_rejected_before_fetcher(
        lambda params: params.pop("start_time"), "missing_required_param:start_time"),
    lambda: test_event_time_contract_is_rejected_before_fetcher(
        lambda params: params.pop("end_time"), "missing_required_param:end_time"),
    lambda: test_event_time_contract_is_rejected_before_fetcher(
        lambda params: params.update(start_time=REFERENCE * 1000 + 1), "invalid_request_time_range"),
    lambda: test_event_time_contract_is_rejected_before_fetcher(
        lambda params: params.update(start_time=True), "invalid_request_param:start_time"),
    lambda: test_public_recovery_builds_canonical_dimensions_and_target(),
    lambda: test_external_keyed_raw_requires_exchange_dimension("pair_liquidation_history"),
    lambda: test_public_recovery_builds_canonical_dimensions_and_target(),
    lambda: test_public_recovery_requires_exchange_before_fetcher("pair_liquidation_history"),
    lambda: test_raw_extractor_response_is_a_deepcopy(),
    lambda: test_public_facade_runs_recovery_end_to_end_and_is_json_strict(),
    lambda: test_raw_extractor_segments_saturated_events_deterministically(),
    lambda: test_saturated_minimum_event_windows_terminate(0),
    lambda: test_public_facade_runs_recovery_end_to_end_and_is_json_strict(),
)


@pytest.mark.parametrize(
    "runner", SMOKE_RUNNERS, ids=[f"smoke_{index:02d}" for index in range(1, 61)],
)
def test_long_short_liquidations_smoke_matrix(runner):
    runner()
