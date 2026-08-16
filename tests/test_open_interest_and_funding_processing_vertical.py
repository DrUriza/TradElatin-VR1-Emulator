import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from canonical_hash_helpers import canonical_text_sha256
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_feature_builder import (
    OpenInterestAndFundingFeatureBuilder,
    build_open_interest_and_funding_features,
)
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_processor import (
    TIMEFRAME_SECONDS,
    TIMEFRAMES,
    OpenInterestAndFundingProcessor,
    process_open_interest_and_funding,
)


REFERENCE = 1_800_000_000
ROOT_KEYS = {
    "family", "stage", "version", "mode", "context", "series", "indicators",
    "events", "snapshots", "confirmations", "availability", "quality",
}
FROZEN_HASHES = {
    "src/processing_signals/input/open_interest_and_funding/open_interest_and_funding_data_raw_extract.py":
        "3C7EC9C300022029ECA75CF8B69C7EF9F24A0FA8FB025C5FDFC410B8C35718AE",
    "src/processing_signals/input/open_interest_and_funding/open_interest_and_funding_data_raw_preprocessing.py":
        "B630F835CB70E7ACEE7E6E19FEEAC108F612A826AC3E85FDD20351DBE857D148",
    "tests/test_open_interest_and_funding_input_vertical.py":
        "1F4BB93B8E5C21C7CAD4CDBC59284BB15281FCC7272932285DDC3C7000A65CB9",
}


def _records(timeframe, count=220, *, funding=False, gaps=(), zero_at=None):
    interval = TIMEFRAME_SECONDS[timeframe]
    timestamp = REFERENCE - interval * (count - 1 + len(gaps))
    rows = []
    for index in range(count):
        if index in gaps:
            timestamp += interval
        if funding:
            close = ((index % 10) - 5) / 1000
            open_value = close - 0.0001
            low, high = min(open_value, close) - 0.0002, max(open_value, close) + 0.0002
        else:
            close = 1000.0 + index * 2.0 + math.sin(index / 3) * 20.0
            if zero_at == index:
                close = 0.0
            open_value = max(0.0, close - 1.0)
            low, high = max(0.0, min(open_value, close) - 2.0), max(open_value, close) + 2.0
        rows.append({"timestamp": timestamp, "open": open_value, "high": high, "low": low, "close": close})
        timestamp += interval
    return rows


def _frame(timeframe, *, funding=False, count=220, status="available", gaps=(), zero_at=None):
    return {
        "status": status,
        "reason": None,
        "expected_interval_seconds": TIMEFRAME_SECONDS[timeframe],
        "unit": "percent_points" if funding else "USD",
        "representation": "percentage_points" if funding else None,
        "records": [] if status == "unavailable" else _records(
            timeframe, count, funding=funding, gaps=gaps, zero_at=zero_at
        ),
    }


def _input(*, mode="bootstrap", counts=None, oi_overrides=None, funding_overrides=None):
    counts = counts or {}
    oi_overrides = oi_overrides or {}
    funding_overrides = funding_overrides or {}
    oi_frames = {
        timeframe: _frame(timeframe, count=counts.get(timeframe, 220), **oi_overrides.get(timeframe, {}))
        for timeframe in TIMEFRAMES
    }
    funding_frames = {
        timeframe: _frame(
            timeframe, funding=True, count=counts.get(timeframe, 220), **funding_overrides.get(timeframe, {})
        )
        for timeframe in TIMEFRAMES
    }
    return {
        "family": "open_interest_and_funding",
        "stage": "input",
        "mode": mode,
        "context": {
            "asset": "BTC",
            "exchange_scope": "all_exchanges",
            "primary_provider": "coinglass",
            "confirmation_providers": ["cryptoquant", "glassnode"],
            "data_mode": "synthetic",
            "is_demo": True,
            "reference_timestamp": REFERENCE,
            "execution_timestamp": REFERENCE,
            "generated_at": "2027-01-15T08:00:00Z",
        },
        "series": {
            "open_interest_ohlc": {
                "provider": "coinglass", "endpoint_id": "aggregated_open_interest_ohlc", "unit": "USD",
                "timeframes": oi_frames,
            },
            "funding_rate_ohlc": {
                "provider": "coinglass", "endpoint_id": "oi_weighted_funding_rate_ohlc",
                "unit": "percent_points", "representation": "percentage_points",
                "aggregation": "open_interest_weighted", "timeframes": funding_frames,
            },
        },
        "snapshots": {
            "open_interest_by_exchange": {
                "status": "available", "reason": None,
                "records": [
                    {"exchange": "All", "open_interest_usd": 5000.0, "open_interest_change_percent_24h": 2.5},
                    {"exchange": "Binance", "open_interest_usd": 3000.0},
                    {"exchange": "OKX", "open_interest_usd": 2000.0},
                ],
                "aggregate_record": {"exchange": "All", "open_interest_usd": 5000.0,
                                     "open_interest_change_percent_24h": 2.5},
            },
            "funding_rate_by_exchange": {
                "status": "available", "reason": None,
                "records": [
                    {"exchange": "Binance", "margin_type": "stablecoin", "funding_rate_percent": 0.01,
                     "next_funding_timestamp": REFERENCE + 3600},
                    {"exchange": "Binance", "margin_type": "token", "funding_rate_percent": -0.01,
                     "next_funding_timestamp": REFERENCE + 3600},
                ],
            },
            "options_open_interest": {
                "status": "available", "reason": None,
                "records": [{"exchange": "All", "open_interest_usd": 800.0, "open_interest_contracts": 12.0}],
                "aggregate_record": {"exchange": "All", "open_interest_usd": 800.0,
                                     "open_interest_contracts": 12.0},
            },
        },
        "confirmations": {
            "open_interest": {
                "cryptoquant": {"status": "available", "provider": "cryptoquant", "records": [{"value": 1.0}]},
                "glassnode": {"status": "available", "provider": "glassnode", "records": [{"value": 2.0}]},
            },
            "funding_rate": {
                "cryptoquant": {"status": "available", "provider": "cryptoquant", "records": [{"value": 0.01}]},
                "glassnode": {"status": "available", "provider": "glassnode", "records": [{"value": 0.02}]},
            },
        },
        "availability": {},
        "quality": {"status": "ok"},
    }


def _process(**kwargs):
    return process_open_interest_and_funding(_input(**kwargs))


def test_contract_direct_bundle_api_and_exact_root():
    source = _input()
    direct = process_open_interest_and_funding(source)
    bundled = OpenInterestAndFundingProcessor().process({"input": source, "ignored": {}})
    assert set(direct) == ROOT_KEYS
    assert (direct["family"], direct["stage"], direct["version"]) == (
        "open_interest_and_funding", "processing", "0.1"
    )
    assert direct == bundled
    assert tuple(direct["series"]["open_interest_ohlc"]["timeframes"]) == TIMEFRAMES


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(family="prices_ohlcv"),
    lambda value: value.update(stage="processing"),
    lambda value: value["context"].pop("reference_timestamp"),
    lambda value: value["series"]["open_interest_ohlc"]["timeframes"].pop("1m"),
    lambda value: value.update(mode="unknown"),
])
def test_incompatible_contracts_are_rejected(mutation):
    source = _input()
    mutation(source)
    with pytest.raises(ValueError):
        process_open_interest_and_funding(source)


@pytest.mark.parametrize("value", [None, [], "input", {"input": []}, {"arbitrary": {}}])
def test_non_contract_values_are_rejected(value):
    with pytest.raises(ValueError):
        process_open_interest_and_funding(value)


def test_deep_immutability_and_output_decoupling():
    source = _input()
    before = copy.deepcopy(source)
    output = process_open_interest_and_funding(source)
    assert source == before
    output["context"]["asset"] = "MUTATED"
    output["snapshots"]["open_interest_by_exchange"]["records"][0]["exchange"] = "MUTATED"
    assert source == before


def test_feature_builder_is_structural_and_deep_copies():
    output = _process()
    sections = {key: output[key] for key in output if key not in {"family", "stage", "version"}}
    rebuilt = build_open_interest_and_funding_features(sections)
    assert rebuilt == output
    rebuilt["series"].clear()
    assert sections["series"]
    assert OpenInterestAndFundingFeatureBuilder().build(sections)["family"] == "open_interest_and_funding"
    source_text = Path(
        "src/processing_signals/processing/open_interest_and_funding/open_interest_and_funding_feature_builder.py"
    ).read_text(encoding="utf-8")
    assert "processing.math" not in source_text


def test_oi_delta_and_exact_change_24h_are_separate_and_aligned():
    output = _process(counts={"1h": 30})
    frame = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    delta = frame["derived"]["oi_delta"]
    change = frame["derived"]["oi_change_24h"]
    assert delta["series"]["delta_absolute_usd"][0] is None
    assert delta["series"]["delta_absolute_usd"][1] == pytest.approx(
        frame["records"][1]["close"] - frame["records"][0]["close"]
    )
    assert change["series"]["change_absolute_usd"][23] is None
    assert change["series"]["change_absolute_usd"][24] == pytest.approx(
        frame["records"][24]["close"] - frame["records"][0]["close"]
    )
    assert len(delta["timestamps"]) == len(change["timestamps"]) == len(frame["records"])
    assert output["snapshots"]["open_interest_by_exchange"]["reported_changes"]["value"] == 2.5


def test_zero_reference_suppresses_percent_but_keeps_absolute():
    output = _process(counts={"1h": 30}, oi_overrides={"1h": {"zero_at": 0}})
    delta = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]["derived"]["oi_delta"]["series"]
    assert delta["delta_absolute_usd"][1] is not None
    assert delta["delta_percent"][1] is None


def test_gaps_split_segments_reset_metrics_and_degrade_quality():
    output = _process(oi_overrides={"1h": {"gaps": (100,)}})
    frame = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    indicators = output["indicators"]["open_interest"]["timeframes"]["1h"]
    assert frame["coverage"]["segment_count"] == 2
    assert frame["derived"]["oi_delta"]["series"]["delta_absolute_usd"][100] is None
    assert indicators["oi_roc"]["series"]["roc"][111] is None
    assert indicators["oi_roc"]["series"]["roc"][112] is not None
    assert indicators["macd"]["series"]["macd"][132] is None
    assert indicators["macd"]["series"]["macd"][133] is not None
    assert frame["status"] == "partial" and output["quality"]["gaps_present"] is True


def test_current_is_not_taken_from_an_older_segment_when_latest_warmup_is_incomplete():
    output = _process(oi_overrides={"1h": {"gaps": (215,)}})
    package = output["indicators"]["open_interest"]["timeframes"]["1h"]
    assert package["atr"]["current"] is None
    assert package["atr"]["current_timestamp"] is None
    assert package["atr"]["status"] == "partial"


def test_all_indicator_arrays_match_source_timeline():
    output = _process()
    for timeframe in TIMEFRAMES:
        count = len(output["series"]["open_interest_ohlc"]["timeframes"][timeframe]["records"])
        for package in output["indicators"]["open_interest"]["timeframes"][timeframe].values():
            for values in package["series"].values():
                assert len(values) == count


def test_funding_is_separate_preserves_negative_values_and_has_no_indicators():
    output = _process()
    frame = output["series"]["funding_rate_ohlc"]
    assert frame["unit"] == "percent_points"
    assert frame["representation"] == "percentage_points"
    assert any(row["close"] < 0 for row in frame["timeframes"]["1h"]["records"])
    assert set(output["indicators"]) == {"open_interest"}


def test_events_are_unique_referenced_deterministic_and_non_semantic():
    first = _process()
    second = _process()
    assert first["events"] == second["events"]
    by_id = first["events"]["by_id"]
    references = [event_id for timeframe in TIMEFRAMES for event_id in first["events"]["timeframes"][timeframe]["event_ids"]]
    assert len(references) == len(set(references)) == len(by_id)
    assert set(references) == set(by_id)
    for event_id, event in by_id.items():
        assert event_id == event["event_id"]
        assert event_id.startswith(f"open_interest_and_funding:{event['timeframe']}:{event['timestamp']}:")
        assert event["current_difference"] != 0
        for forbidden in ("bullish", "bearish", "signal", "color", "marker", "display_label"):
            assert forbidden not in event
    encoded = json.dumps(first["events"]).lower()
    assert "bullish" not in encoded and "bearish" not in encoded


def test_events_never_bridge_a_gap():
    output = _process(oi_overrides={"1h": {"gaps": (100,)}}, funding_overrides={"1h": {"gaps": (100,)}})
    frame = output["series"]["open_interest_ohlc"]["timeframes"]["1h"]
    gap_timestamp = frame["records"][100]["timestamp"]
    for event_id in output["events"]["timeframes"]["1h"]["event_ids"]:
        event = output["events"]["by_id"][event_id]
        if event["timestamp"] == gap_timestamp:
            assert event["previous_difference"] is None


def test_snapshots_are_organized_without_double_summing():
    output = _process()
    oi = output["snapshots"]["open_interest_by_exchange"]
    funding = output["snapshots"]["funding_rate_by_exchange"]
    options = output["snapshots"]["options_open_interest"]
    assert oi["current_total_usd"] == 5000.0 and oi["exchange_count"] == 2
    assert oi["aggregate_record"]["exchange"] == "All"
    assert len(funding["stablecoin_margin_records"]) == len(funding["token_margin_records"]) == 1
    assert options["current_options_open_interest_usd"] == 800.0
    assert output["availability"]["funding_8h_aggregate"] == {
        "status": "unavailable", "reason": "cross_exchange_8h_weighting_not_defined"
    }


def test_confirmations_stay_separate_and_comparisons_are_unavailable():
    output = _process()
    confirmations = output["confirmations"]
    assert confirmations["open_interest"]["cryptoquant"]["records"] == [{"value": 1.0}]
    assert confirmations["open_interest"]["glassnode"]["records"] == [{"value": 2.0}]
    assert confirmations["comparisons"]["open_interest"] == {
        "status": "unavailable", "reason": "provider_scope_not_proven_comparable"
    }


def test_insufficient_history_is_partial_not_invalid():
    output = _process(counts={timeframe: 10 for timeframe in TIMEFRAMES})
    assert output["availability"]["oi_change_24h_derived"]["status"] == "partial"
    assert output["indicators"]["open_interest"]["timeframes"]["1h"]["macd"]["status"] == "partial"
    assert output["quality"]["status"] == "partial"


def test_source_unavailable_and_invalid_propagate_locally():
    unavailable = _process(oi_overrides={"1h": {"status": "unavailable"}})
    assert unavailable["series"]["open_interest_ohlc"]["timeframes"]["1h"]["status"] == "unavailable"
    assert unavailable["indicators"]["open_interest"]["timeframes"]["1h"]["macd"]["status"] == "unavailable"
    invalid_source = _input()
    invalid_source["series"]["open_interest_ohlc"]["timeframes"]["1h"]["records"][0]["close"] = math.nan
    invalid = process_open_interest_and_funding(invalid_source)
    assert invalid["series"]["open_interest_ohlc"]["timeframes"]["1h"]["status"] == "invalid"
    assert invalid["quality"]["status"] == "invalid"


def test_optional_confirmation_invalid_is_isolated():
    source = _input()
    source["confirmations"]["open_interest"]["glassnode"]["status"] = "invalid"
    output = process_open_interest_and_funding(source)
    assert output["series"]["open_interest_ohlc"]["timeframes"]["1h"]["status"] == "available"
    assert output["quality"]["status"] != "invalid"
    assert "optional_confirmation_invalid:open_interest.glassnode" in output["quality"]["warnings"]


def test_incompatible_snapshot_payloads_and_rows_are_isolated_and_json_safe():
    source = _input()
    valid_oi = copy.deepcopy(source["snapshots"]["open_interest_by_exchange"]["records"][1])
    source["snapshots"]["open_interest_by_exchange"]["records"] = [valid_oi, None]
    source["snapshots"]["open_interest_by_exchange"]["aggregate_record"] = []
    stablecoin, token = copy.deepcopy(source["snapshots"]["funding_rate_by_exchange"]["records"])
    source["snapshots"]["funding_rate_by_exchange"]["records"] = [None, stablecoin, token]
    source["snapshots"]["options_open_interest"]["records"] = [None]
    output = process_open_interest_and_funding(source)

    oi = output["snapshots"]["open_interest_by_exchange"]
    assert oi["records"] == [valid_oi]
    assert oi["invalid_records"] == [{"index": 1, "reason": "snapshot_record_not_mapping"}]
    assert (oi["status"], oi["reason"], oi["aggregate_record"]) == (
        "partial", "aggregate_record_incompatible", None
    )
    funding = output["snapshots"]["funding_rate_by_exchange"]
    assert len(funding["stablecoin_margin_records"]) == len(funding["token_margin_records"]) == 1
    assert funding["invalid_records"] == [{"index": 0, "reason": "snapshot_record_not_mapping"}]
    options = output["snapshots"]["options_open_interest"]
    assert options["status"] == "invalid" and options["reason"] == "snapshot_records_incompatible"
    assert "snapshot_open_interest_by_exchange_invalid_records" in output["quality"]["warnings"]
    assert output["quality"]["status"] == "partial" and output["quality"]["data_complete"] is False
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)

    source = _input()
    source["snapshots"]["open_interest_by_exchange"]["records"] = [None]
    isolated = process_open_interest_and_funding(source)["snapshots"]["open_interest_by_exchange"]
    assert (isolated["status"], isolated["reason"]) == ("invalid", "snapshot_records_incompatible")
    source["snapshots"]["open_interest_by_exchange"] = []
    invalid_payload = process_open_interest_and_funding(source)["snapshots"]["open_interest_by_exchange"]
    assert (invalid_payload["status"], invalid_payload["reason"]) == ("invalid", "snapshot_payload_not_mapping")


def test_incompatible_confirmations_are_normalized_without_invalidating_primary_series():
    source = _input()
    source["confirmations"]["open_interest"]["glassnode"] = []
    source["confirmations"]["funding_rate"]["cryptoquant"] = None
    source["confirmations"]["funding_rate"]["glassnode"]["records"] = "invalid"
    source["confirmations"]["open_interest"]["cryptoquant"]["records"] = [{"value": 1.0}, None]
    output = process_open_interest_and_funding(source)

    glassnode_oi = output["confirmations"]["open_interest"]["glassnode"]
    assert glassnode_oi == {
        "provider": "glassnode", "endpoint_id": "futures_open_interest_sum", "unit": "USD",
        "provider_interval": "1h", "status": "invalid", "reason": "confirmation_payload_not_mapping",
        "records": [],
    }
    cryptoquant_funding = output["confirmations"]["funding_rate"]["cryptoquant"]
    assert cryptoquant_funding["endpoint_id"] == "funding_rates"
    assert cryptoquant_funding["provider_window"] == "hour"
    assert cryptoquant_funding["status"] == "invalid"
    assert output["confirmations"]["funding_rate"]["glassnode"]["reason"] == "confirmation_records_not_list"
    mixed = output["confirmations"]["open_interest"]["cryptoquant"]
    assert mixed["status"] == "partial" and mixed["records"] == [{"value": 1.0}]
    assert output["series"]["open_interest_ohlc"]["timeframes"]["1h"]["status"] == "available"
    assert output["series"]["funding_rate_ohlc"]["timeframes"]["1h"]["status"] == "available"
    assert output["quality"]["status"] == "partial" and output["quality"]["contract_complete"] is True
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)

    source = _input()
    source["confirmations"]["open_interest"]["cryptoquant"]["records"] = [None]
    invalid = process_open_interest_and_funding(source)["confirmations"]["open_interest"]["cryptoquant"]
    assert (invalid["status"], invalid["reason"], invalid["records"]) == (
        "invalid", "confirmation_records_incompatible", []
    )


def test_modes_are_mathematically_equivalent():
    outputs = [_process(mode=mode) for mode in ("bootstrap", "incremental", "recovery")]
    for output in outputs:
        output.pop("mode")
    assert outputs[0] == outputs[1] == outputs[2]


def test_strict_json_has_no_numpy_pandas_nonfinite_or_negative_zero():
    output = _process()
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)

    def walk(value):
        assert not isinstance(value, (np.generic, pd.Series, pd.DataFrame, pd.Timestamp, bytes, set, tuple))
        if isinstance(value, float):
            assert math.isfinite(value)
            assert not (value == 0.0 and math.copysign(1.0, value) < 0)
        elif isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(output)


