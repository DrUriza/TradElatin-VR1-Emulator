def valid_fetcher(**request):
    endpoint = request["endpoint_id"]
    timestamp_ms = request.get("params", {}).get("end_time", 1_700_000_000_000)
    if endpoint.endswith("orderbook_heatmap"):
        return {"code": "0", "data": [{"time": timestamp_ms, "bids": [[100, 2]], "asks": [[101, 3]]}]}
    if endpoint.endswith("order_depth"):
        return {"code": 0, "data": [{"time": timestamp_ms, "bids_usd": "200", "bids_quantity": "2",
                                       "asks_usd": "303", "asks_quantity": "3"}]}
    if endpoint.endswith("footprint"):
        return {"code": 0, "data": [[timestamp_ms, [[99, 100, 1.5, 1.0, 0, 0, 15000, 10000]]]]}
    if endpoint.endswith("large_limit_orders"):
        return {"code": 0, "data": [{"id": f"{endpoint}-1", "current_time": timestamp_ms, "start_time": timestamp_ms - 60_000,
                                       "order_side": 1, "order_state": 1, "price": "100", "current_quantity": "2",
                                       "current_usd_value": "200", "executed_volume": "0", "executed_usd_value": "0",
                                       "trade_count": 0, "exchange_name": "Binance", "symbol": "BTCUSDT"}]}
    if endpoint == "whale_index":
        return {"code": 0, "data": [{"time": timestamp_ms, "whale_index_value": "-0.25"}]}
    return {"code": 0, "data": []}
