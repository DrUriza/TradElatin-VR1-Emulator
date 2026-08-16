from processing_signals.input.long_short_liquidations.long_short_liquidations_data_raw_preprocessing import _provenance
from processing_signals.processing.liquidity_microstructure.liquidity_microstructure_processor import _historical_market_join
from processing_signals.processing.long_short_liquidations.long_short_liquidations_feature_builder import build_event_window
from processing_signals.processing.long_short_liquidations.long_short_liquidations_processor import _events_coverage_complete


def test_zero_event_closed_window_is_available_not_missing():
    result = build_event_window([], window_end=86_400, window_seconds=3_600, coverage_complete=True)
    assert result["status"] == "available"
    assert result["event_count"] == 0
    assert result["event_usd_total"] == 0
    assert result["coverage"]["coverage_ratio"] == 1.0


def test_missing_feed_interval_remains_incomplete():
    result = build_event_window([], window_end=86_400, window_seconds=3_600, coverage_complete=False)
    assert result["status"] == "unavailable"
    assert result["coverage"]["source_complete"] is False


def test_incremental_event_provenance_preserves_prior_coverage():
    existing = {"provenance": {"request_ids": ["old"], "coverage_intervals": [
        {"start": 0, "end": 86_400, "status": "complete"}
    ]}}
    request = {"request_id": "new", "provider": "coinglass", "endpoint_id": "liquidation_order_events",
               "path": "/events", "status": "ok", "warnings": [],
               "params": {"start_time": 86_100_000, "end_time": 86_400_000}}
    provenance = _provenance([request], {"reference_timestamp": 86_400, "execution_timestamp": 86_405}, existing)
    assert _events_coverage_complete({"status": "available", "warnings": [], "provenance": provenance}, 0, 86_400)


def test_hourly_join_is_exact_and_zero_fills_absent_events():
    timestamps = (3_600, 7_200)
    prices = {"confirmations": {"glassnode": {"price_ohlc": {"records": [
        {"timestamp": ts, "close": 100.0 + index} for index, ts in enumerate(timestamps)]}}},
        "provider_features": {"market_cap": {"records": [
            {"timestamp": ts, "value": 1_000.0 + index} for index, ts in enumerate(timestamps)]}}}
    provider = {"orderbook": {}, "order_depth": {}, "large_trades": {},
                "whale_activity": {"records": [{"timestamp": ts, "timeframe": "1h", "whale_index_value": 0.1}
                                                  for ts in timestamps]}}
    for market in ("spot", "perpetual"):
        provider["orderbook"][market] = {"records": [
            {"timestamp": ts, "timeframe": "1h", "bid_levels": [{"price": 99.0, "quantity": 1.0}],
             "ask_levels": [{"price": 101.0, "quantity": 1.0}]} for ts in timestamps]}
        provider["order_depth"][market] = {"records": [
            {"timestamp": ts, "timeframe": "1h", "range_percent": 10,
             "bids_usd": 10.0, "asks_usd": 12.0} for ts in timestamps]}
        provider["large_trades"][market] = {"events": []}
    result = _historical_market_join(prices, provider)
    assert result["status"] == "available"
    assert len(result["records"]) == 2
    assert result["records"][0]["spread"] == 2.0
    assert result["records"][0]["large_trade_count"] == 0
    assert result["source_data_as_of"] == 7_200
