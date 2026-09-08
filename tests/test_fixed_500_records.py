from app.settings import EMULATOR_RECORD_COUNT

CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}
CQ_HEADERS = {"Authorization": "Bearer emulator-cryptoquant-key"}
GN_HEADERS = {"X-Api-Key": "emulator-glassnode-key"}


def test_fixed_record_policy_is_500(client):
    assert EMULATOR_RECORD_COUNT == 500
    assert client.get("/health").json()["endpoint_records"] == 500
    registry = client.get("/admin/endpoints").json()
    assert registry["endpoint_records"] == 500


def test_historical_endpoints_ignore_smaller_limit_and_return_500(client):
    samples = (
        ("/coinglass/api/spot/price/history", CG_HEADERS, {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}, lambda body: body["data"]),
        ("/coinglass/api/spot/aggregated-cvd/history", CG_HEADERS, {"exchange_list": "Binance", "symbol": "BTC", "interval": "5m", "limit": 5, "unit": "usd"}, lambda body: body["data"]),
        ("/cryptoquant/btc/exchange-flows/inflow", CQ_HEADERS, {"exchange": "all_exchange", "window": "day", "limit": 5, "format": "json"}, lambda body: body["result"]["data"]),
        ("/glassnode/v1/metrics/indicators/sopr", GN_HEADERS, {"a": "BTC", "i": "10m", "f": "json", "timestamp_format": "unix"}, lambda body: body),
    )
    for path, headers, params, extract in samples:
        response = client.get(path, headers=headers, params=params)
        assert response.status_code == 200, (path, response.text)
        assert len(extract(response.json())) == EMULATOR_RECORD_COUNT


def test_non_timeseries_heavy_endpoints_also_have_500_records(client):
    orders = client.get(
        "/coinglass/api/spot/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"]
    assert len(orders) == 500

    levels = client.get(
        "/coinglass/api/futures/liquidation/aggregated-map",
        headers=CG_HEADERS,
        params={"symbol": "BTC", "range": "7d"},
    ).json()["data"]["data"]
    assert len(levels) == 500

    funds = client.get("/coinglass/api/etf/bitcoin/list", headers=CG_HEADERS).json()["data"]
    assert len(funds) == 500
    assert {item["ticker"] for item in funds} == {"IBIT", "FBTC", "GBTC", "ARKB", "BITB", "HODL", "BRRR", "EZBC"}


def test_oi_weighted_funding_is_neutral_near_zero_on_public_timeframes(client):
    for interval in ("5m", "15m", "4h"):
        response = client.get(
            "/coinglass/api/futures/funding-rate/oi-weight-history",
            headers=CG_HEADERS,
            params={"symbol": "BTC", "interval": interval, "limit": 500},
        )
        assert response.status_code == 200, response.text
        candles = response.json()["data"]
        assert len(candles) == 500
        closes = [float(item["close"]) for item in candles]
        assert abs(sum(closes) / len(closes)) < 5e-5
        assert max(abs(value) for value in closes) < 4.5e-4


def test_all_final_33_endpoints_return_exactly_500_records(client):
    """Exhaustive frozen-surface regression: every logical endpoint is 500 records."""
    registry = client.get("/admin/endpoints").json()["endpoints"]
    assert len(registry) == 33

    for item in registry:
        provider = item["provider"]
        endpoint_id = item["endpoint_id"]
        path = item["path"]

        if provider == "coinglass":
            headers = CG_HEADERS
            params = {}
            if endpoint_id == "spot_ohlcv":
                params = {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 500}
            elif endpoint_id in {"spot_aggregated_cvd", "futures_aggregated_cvd"}:
                params = {"exchange_list": "Binance", "symbol": "BTC", "interval": "5m", "limit": 500, "unit": "usd"}
            elif endpoint_id in {"spot_footprint", "futures_footprint", "spot_orderbook_heatmap", "perpetual_orderbook_heatmap"}:
                params = {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 500}
            elif endpoint_id in {"aggregated_open_interest_ohlc", "oi_weighted_funding_rate_ohlc"}:
                params = {"symbol": "BTC", "interval": "5m", "limit": 500}
            elif endpoint_id == "aggregated_liquidation_history":
                params = {"exchange_list": "Binance", "symbol": "BTC", "interval": "1m", "limit": 500}
            elif endpoint_id == "liquidation_order_events":
                params = {"exchange": "Binance", "symbol": "BTC", "min_liquidation_amount": 0}
            elif endpoint_id == "aggregated_liquidation_map":
                params = {"symbol": "BTC", "range": "7d"}
            elif endpoint_id == "pair_liquidation_map":
                params = {"exchange": "Binance", "symbol": "BTCUSDT", "range": "7d"}
            elif endpoint_id in {"top_position_long_short_ratio", "top_account_long_short_ratio", "global_account_long_short_ratio"}:
                params = {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 500}
            elif endpoint_id in {"spot_large_limit_orders", "perpetual_large_limit_orders"}:
                params = {"exchange": "Binance", "symbol": "BTCUSDT"}
            response = client.get(f"/coinglass{path}", headers=headers, params=params)
            assert response.status_code == 200, (endpoint_id, response.text)
            body = response.json()
            if endpoint_id in {"aggregated_liquidation_map", "pair_liquidation_map"}:
                count = len(body["data"]["data"])
            else:
                count = len(body["data"])

        elif provider == "cryptoquant":
            params = {"window": "day", "limit": 500, "format": "json"}
            if endpoint_id in {"exchange_inflow", "exchange_outflow", "exchange_reserve"}:
                params["exchange"] = "all_exchange"
            response = client.get(f"/cryptoquant{path}", headers=CQ_HEADERS, params=params)
            assert response.status_code == 200, (endpoint_id, response.text)
            count = len(response.json()["result"]["data"])

        else:
            params = {"a": "BTC", "i": "10m", "f": "json", "timestamp_format": "unix"}
            if endpoint_id in {"balance_miners_sum", "transfers_volume_from_miners_sum", "revenue_sum"}:
                params["c"] = "NATIVE"
            response = client.get(f"/glassnode{path}", headers=GN_HEADERS, params=params)
            assert response.status_code == 200, (endpoint_id, response.text)
            count = len(response.json())

        assert count == EMULATOR_RECORD_COUNT, (provider, endpoint_id, count)
