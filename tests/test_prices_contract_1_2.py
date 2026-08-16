from __future__ import annotations

import json
from collections import Counter
from datetime    import UTC, datetime
from typing      import Any

import pytest

pytestmark = pytest.mark.skip(reason="legacy synthetic Prices vertical retired; current Main uses runtime_orchestrator")

from processing_signals.main.main_pipeline import SYNTHETIC_REFERENCE_TIMESTAMP, SyntheticPricesFetcher, _run_synthetic_vertical
from processing_signals.main.prices_ohlcv  import build_prices_view


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3_600, "4h": 14_400, "1d": 86_400}
TIMEFRAME_SOURCES = {"1m": ("1m", 1), "5m": ("1m", 5), "15m": ("15m", 1), "1h": ("15m", 4), "4h": ("15m", 16), "1d": ("15m", 96)}


@pytest.fixture(scope="module")
def vertical() -> dict[str, Any]:
    return _run_synthetic_vertical("bootstrap")


def test_synthetic_timeframes_are_anchored_backward_to_one_reference_timestamp(vertical: dict[str, Any]) -> None:
    screen     = vertical["screen"]
    processing = vertical["processing"]
    assert screen["context"]["reference_timestamp"] == SYNTHETIC_REFERENCE_TIMESTAMP
    starts = set()
    for market in ("spot", "futures", "general"):
        for timeframe, interval in TIMEFRAME_SECONDS.items():
            records = processing["markets"][market]["timeframes"][timeframe]["records"]
            stamps  = [record["timestamp"] for record in records]
            assert stamps == sorted(set(stamps))
            assert all(second - first == interval for first, second in zip(stamps, stamps[1:]))
            assert stamps[0] == stamps[-1] - (len(stamps) - 1) * interval
            metadata = screen["charts"]["ohlcv"]["markets"][market]["timeframes"][timeframe]["metadata"]
            source, expected = TIMEFRAME_SOURCES[timeframe]
            assert metadata["reference_timestamp"] == SYNTHETIC_REFERENCE_TIMESTAMP
            assert metadata["data_as_of"] == datetime.fromtimestamp(stamps[-1], tz=UTC).isoformat()
            assert metadata["source_timeframe"] == source
            assert metadata["resampled"] is (source != timeframe)
            assert metadata["records_expected"] == expected
            assert metadata["is_closed"] is True and metadata["coverage_complete"] is True
            starts.add(stamps[0])
    assert len(starts) == 2  # Shared starts are a natural consequence of exact source-window divisibility, not reused generated series.
    assert datetime.fromisoformat(screen["context"]["updated_at"]) <= datetime.fromisoformat(screen["context"]["generated_at"])


def test_long_timeframes_have_enough_history_for_fifty_period_warmup(vertical: dict[str, Any]) -> None:
    markets = vertical["processing"]["markets"]
    for market in ("spot", "futures", "general"):
        assert len(markets[market]["timeframes"]["15m"]["records"]) == 5_760
        assert len(markets[market]["timeframes"]["1h"]["records"]) == 1_440
        assert len(markets[market]["timeframes"]["4h"]["records"]) == 360
        assert len(markets[market]["timeframes"]["1d"]["records"]) == 60
        daily_averages = vertical["processing"]["features"]["indicators"][market]["1d"]["moving_averages"]
        assert daily_averages["current"]["sma_50"] is not None
        assert daily_averages["current"]["wma_50"] is not None


def test_contract_publishes_aligned_tail_windows_without_recalculating(vertical: dict[str, Any]) -> None:
    processing = vertical["processing"]
    screen     = vertical["screen"]
    expected   = {"1m": (600, 120), "5m": (120, 120), "15m": (5_760, 120), "1h": (1_440, 120), "4h": (360, 120), "1d": (60, 60)}
    assert len(processing["markets"]["general"]["timeframes"]["1m"]["records"]) == 600
    assert len(processing["markets"]["general"]["timeframes"]["15m"]["records"]) == 5_760
    for market in ("spot", "futures", "general"):
        for timeframe, (available, returned) in expected.items():
            internal  = processing["markets"][market]["timeframes"][timeframe]["records"]
            published = screen["charts"]["ohlcv"]["markets"][market]["timeframes"][timeframe]
            metadata  = published["metadata"]
            assert len(internal) == available and len(published["records"]) == returned
            assert published["records"][-1] == internal[-1]
            assert metadata["records_available"] == available and metadata["records_returned"] == returned
            assert metadata["history_truncated"] is (available > returned)
            assert metadata["first_returned_timestamp"] == published["records"][0]["timestamp"]
            assert metadata["last_returned_timestamp"] == published["records"][-1]["timestamp"]
            assert metadata["last_returned_timestamp"] == int(datetime.fromisoformat(metadata["data_as_of"]).timestamp())
            assert [record["timestamp"] for record in published["records"]] == sorted(record["timestamp"] for record in published["records"])
            package = processing["features"]["indicators"][market][timeframe]
            for indicator_id in ("rsi", "macd", "stochastic", "adx", "cci", "mfi", "williams_r", "atr", "tsi"):
                visual = screen["charts"][indicator_id]["markets"][market][timeframe]
                assert len(visual["timestamps"]) == returned
                assert all(len(values) == returned for values in visual["series"].values())
                assert visual["current"] == package[indicator_id]["current"]
            overlays = published["overlays"]
            assert all(len(values) == returned for values in overlays["moving_averages"]["series"].values())
            assert all(len(values) == returned for values in overlays["bollinger_bands"]["series"].values())
    assert screen["context"]["history_policy"] == {"calculation": "full_available_history", "presentation": "tail_window", "default_display_window": 120}
    assert screen["quality"]["presentation"] == {"window_limited": True, "default_display_window": 120}


def test_only_visible_events_are_published_and_all_references_resolve(vertical: dict[str, Any]) -> None:
    screen   = vertical["screen"]
    registry = screen["events"]
    for uid, event in registry["by_id"].items():
        source  = event["source"]
        records = screen["charts"]["ohlcv"]["markets"][source["market"]]["timeframes"][source["timeframe"]]["records"]
        assert event["timestamp"] in {record["timestamp"] for record in records}
        assert uid == event["event_uid"]
    references = registry["technical_cross_ids"] + registry["candlestick_pattern_ids"]
    references += [uid for market in screen["charts"]["ohlcv"]["annotations"].values() for timeframe in market.values()
                   for ids in timeframe["by_timestamp"].values() for uid in ids]
    references += screen["widgets"]["candlestick_patterns_analysis"]["row_ids"]
    assert references and all(uid in registry["by_id"] for uid in references)


def test_real_resampling_and_general_after_resampling(vertical: dict[str, Any]) -> None:
    markets = vertical["processing"]["markets"]
    for target, (source, expected) in TIMEFRAME_SOURCES.items():
        if target in {"1m", "15m"}:
            continue
        for market in ("spot", "futures"):
            derived = markets[market]["timeframes"][target]["records"][-1]
            members = [record for record in markets[market]["timeframes"][source]["records"]
                       if derived["timestamp"] <= record["timestamp"] < derived["timestamp"] + TIMEFRAME_SECONDS[target]]
            assert len(members) == expected == derived["source_records"]
            assert derived["open"] == members[0]["open"] and derived["close"] == members[-1]["close"]
            assert derived["high"] == max(record["high"] for record in members)
            assert derived["low"] == min(record["low"] for record in members)
            assert derived["volume_usd"] == pytest.approx(sum(record["volume_usd"] for record in members))
        spot    = markets["spot"]["timeframes"][target]["records"][-1]
        futures = markets["futures"]["timeframes"][target]["records"][-1]
        general = markets["general"]["timeframes"][target]["records"][-1]
        assert general["timestamp"] == spot["timestamp"] == futures["timestamp"]
        for field in ("open", "high", "low", "close"):
            assert general[field] == pytest.approx((spot[field] + futures[field]) / 2.0)
        assert general["combined_volume_usd"] == pytest.approx(spot["volume_usd"] + futures["volume_usd"])


def test_24h_metrics_share_exactly_twenty_four_closed_hourly_bars(vertical: dict[str, Any]) -> None:
    screen  = vertical["screen"]
    items   = {item["metric_id"]: item for item in screen["kpis"]["items"]}
    records = vertical["processing"]["markets"]["general"]["timeframes"]["1h"]["records"]
    window, prior = records[-24:], records[-25]
    expected_change = (window[-1]["close"] / prior["close"] - 1.0) * 100.0
    expected_volume = sum(record["combined_volume_usd"] for record in window)
    assert screen["kpis"]["records_used_24h"] == 24
    assert items["change_24h"]["value"] == pytest.approx(expected_change)
    assert screen["widgets"]["price_change"]["windows"]["24h"] == pytest.approx(expected_change)
    assert items["high_24h"]["value"] == max(record["high"] for record in window)
    assert items["low_24h"]["value"] == min(record["low"] for record in window)
    assert items["volume_24h"]["value"] == pytest.approx(expected_volume)
    assert screen["widgets"]["volume_analysis"]["average_24h"] == pytest.approx(expected_volume / 24.0)


def test_indicator_rows_preserve_values_parameters_units_and_visual_semantics(vertical: dict[str, Any]) -> None:
    tables = vertical["screen"]["tables"]["indicators_metrics"]
    rows   = {row["metric_id"]: row for row in tables["indicator_package"]["rows"]}
    assert len({rows[name]["value"] for name in ("macd", "macd_signal", "macd_histogram")}) == 3
    assert rows["rsi"]["parameters"] == {"period": 14}
    assert rows["macd"]["parameters"] == rows["macd_signal"]["parameters"] == rows["macd_histogram"]["parameters"] == {"fast_period": 12, "slow_period": 26, "signal_period": 9}
    assert rows["stochastic"]["parameters"] == {"k_period": 14, "k_smoothing": 3, "d_period": 3}
    assert rows["tsi"]["parameters"] == {"long_period": 25, "short_period": 13}
    assert all(row["display_color_token"] == row["state"] if row["metric_id"] in {"rsi", "stochastic", "adx", "mfi", "williams_r", "atr"}
               else row["display_color_token"] == row["signal"] for row in rows.values())
    assert all("-0.00" not in row["display_value"] for row in rows.values())
    statistics = {row["metric_id"]: row for row in tables["statistical_performance"]["rows"]}
    for metric_id in ("standard_deviation", "var_95", "cvar_95"):
        assert statistics[metric_id]["value"] is not None and statistics[metric_id]["return_value"] is not None
        assert statistics[metric_id]["unit"] == "quote_currency" and statistics[metric_id]["classification_unit"] == "decimal_return"
        assert statistics[metric_id]["display_basis"] and statistics[metric_id]["classification_basis"]


def test_event_registry_is_deterministic_lossless_and_reference_only(vertical: dict[str, Any]) -> None:
    screen   = vertical["screen"]
    registry = screen["events"]
    all_ids  = registry["technical_cross_ids"] + registry["candlestick_pattern_ids"]
    assert len(all_ids) == len(set(all_ids)) == len(registry["by_id"])
    assert all(uid == event["event_uid"] and event.get("timestamp") and event.get("calculation") is not None for uid, event in registry["by_id"].items())
    references = [uid for market in screen["charts"]["ohlcv"]["annotations"].values() for timeframe in market.values()
                  for ids in timeframe["by_timestamp"].values() for uid in ids]
    assert set(references) == set(all_ids)
    widget = screen["widgets"]["candlestick_patterns_analysis"]
    assert set(widget["row_ids"]) <= set(registry["candlestick_pattern_ids"])
    assert "rows" not in widget and all(uid in registry["by_id"] for uid in widget["row_ids"])
    assert json.dumps(screen, allow_nan=False)


def test_synthetic_fetcher_is_deterministic_variable_and_pattern_distribution_is_reasonable(vertical: dict[str, Any]) -> None:
    fetcher_a = SyntheticPricesFetcher(seed=17)
    fetcher_b = SyntheticPricesFetcher(seed=17)
    request   = {"endpoint_id": "spot_ohlcv", "params": {"interval": "15m", "limit": 120}}
    assert fetcher_a(**request) == fetcher_b(**request)
    markets = vertical["processing"]["markets"]
    spot, futures = markets["spot"]["timeframes"]["15m"]["records"], markets["futures"]["timeframes"]["15m"]["records"]
    spreads = {round(future["close"] - cash["close"], 8) for cash, future in zip(spot, futures)}
    assert len(spreads) > 20
    assert any(cash["volume_usd"] > future["volume_usd"] for cash, future in zip(spot, futures))
    assert any(future["volume_usd"] > cash["volume_usd"] for cash, future in zip(spot, futures))
    spot_returns    = [spot[index]["close"] / spot[index - 1]["close"] - 1 for index in range(1, len(spot))]
    futures_returns = [futures[index]["close"] / futures[index - 1]["close"] - 1 for index in range(1, len(futures))]
    assert any(left * right > 0 for left, right in zip(spot_returns, futures_returns))
    assert any(left * right < 0 for left, right in zip(spot_returns, futures_returns))
    indicators = vertical["processing"]["features"]["indicators"]["general"]
    assert len({indicators[timeframe]["rsi"]["current"]["rsi"] for timeframe in TIMEFRAME_SECONDS}) > 1
    assert len({indicators[timeframe]["macd"]["current"]["macd"] for timeframe in TIMEFRAME_SECONDS}) > 1
    counts = Counter(event["event_id"] for event in vertical["screen"]["events"]["by_id"].values() if event["event_type"] == "candlestick_pattern")
    assert len(counts) >= 6 and counts.most_common(1)[0][1] / sum(counts.values()) < 0.60


def test_quality_separates_contract_and_data_completeness(vertical: dict[str, Any]) -> None:
    quality = vertical["screen"]["quality"]
    assert quality["contract_complete"] is True and quality["is_complete"] is True
    assert quality["data_complete"] is False
    assert quality["availability"] == {"kpis_available": 7, "kpis_total": 9, "widgets_available": 7, "widgets_total": 13,
                                       "charts_available": 10, "charts_total": 10, "tables_available": 3, "tables_total": 3}
    assert quality["compatibility_alias"] == {"is_complete": "contract_complete"}
    assert vertical["screen"]["schema_version"] == "1.2.0"


def test_selected_view_is_small_complete_and_uses_requested_selection(vertical: dict[str, Any]) -> None:
    view = build_prices_view(vertical_output=vertical, market="spot", timeframe="15m")
    assert view["family"] == "prices_ohlcv" and view["screen"] == "prices"
    assert view["contract_type"] == "selected_view" and view["schema_version"] == "1.2.0"
    assert view["selection"] == {"market": "spot", "timeframe": "15m"}
    assert view["kpis"]["selected_market"] == "spot" and view["kpis"]["selected_timeframe"] == "15m"
    assert len(view["tables"]["indicator_package"]["rows"]) == 11
    assert len(view["tables"]["technical_bias"]["rows"]) == 4
    assert len(view["tables"]["statistical_performance"]["rows"]) == 17
    assert view["comparison"]["timeframe"] == "15m"
    assert view["data_as_of"] == datetime.fromtimestamp(vertical["processing"]["markets"]["spot"]["timeframes"]["15m"]["records"][-1]["timestamp"], tz=UTC).isoformat()
    assert view["quality"]["contract_complete"] is True
    assert "charts" not in view and "events" not in view and "selectors" not in view
    assert "markets" not in view["tables"]["indicator_package"]
    assert len(json.dumps(view, allow_nan=False).encode("utf-8")) < 100_000


def test_selected_view_changes_kpis_and_rows_without_rerunning_vertical(vertical: dict[str, Any]) -> None:
    general       = build_prices_view(vertical_output=vertical, market="general", timeframe="1h")
    futures       = build_prices_view(vertical_output=vertical, market="futures", timeframe="4h")
    general_price = next(item["value"] for item in general["kpis"]["items"] if item["metric_id"] == "last_price")
    futures_price = next(item["value"] for item in futures["kpis"]["items"] if item["metric_id"] == "last_price")
    assert general_price != futures_price
    assert general["tables"]["indicator_package"]["rows"] != futures["tables"]["indicator_package"]["rows"]
    assert futures["comparison"]["classification"] == {"status": "unavailable", "reason": "classification_not_available_for_selected_timeframe"}
    with pytest.raises(ValueError, match="market"):
        build_prices_view(vertical_output=vertical, market="invalid", timeframe="1h")
    with pytest.raises(ValueError, match="timeframe"):
        build_prices_view(vertical_output=vertical, market="spot", timeframe="2h")
