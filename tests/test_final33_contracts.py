from app.core.simulation_engine import engine

CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}
CQ_HEADERS = {"Authorization": "Bearer emulator-cryptoquant-key"}
GN_HEADERS = {"X-Api-Key": "emulator-glassnode-key"}


def test_registry_is_exact_final_33(client):
    payload = client.get("/admin/endpoints").json()
    assert payload["count"] == 33
    endpoints = payload["endpoints"]
    assert len(endpoints) == 33
    assert len({(item["provider"], item["endpoint_id"]) for item in endpoints}) == 33
    assert sum(item["provider"] == "coinglass" for item in endpoints) == 20
    assert sum(item["provider"] == "cryptoquant" for item in endpoints) == 4
    assert sum(item["provider"] == "glassnode" for item in endpoints) == 9


def test_coinglass_20_endpoints(client):
    calls = [
        ("/api/spot/price/history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/spot/aggregated-cvd/history", {"exchange_list": "Binance,OKX", "symbol": "BTC", "interval": "5m", "limit": 5, "unit": "usd"}),
        ("/api/futures/aggregated-cvd/history", {"exchange_list": "Binance,OKX", "symbol": "BTC", "interval": "5m", "limit": 5, "unit": "usd"}),
        ("/api/spot/volume/footprint-history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/futures/volume/footprint-history", {"exchange": "Bybit", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/futures/open-interest/aggregated-history", {"symbol": "BTC", "interval": "5m", "limit": 5}),
        ("/api/futures/funding-rate/oi-weight-history", {"symbol": "BTC", "interval": "5m", "limit": 5}),
        ("/api/etf/bitcoin/flow-history", {}),
        ("/api/etf/bitcoin/list", {}),
        ("/api/futures/liquidation/aggregated-history", {"exchange_list": "Binance,OKX", "symbol": "BTC", "interval": "1m", "limit": 5}),
        ("/api/futures/liquidation/order", {"exchange": "Binance", "symbol": "BTC", "min_liquidation_amount": 0}),
        ("/api/futures/liquidation/aggregated-map", {"symbol": "BTC", "range": "7d"}),
        ("/api/futures/liquidation/map", {"exchange": "Binance", "symbol": "BTCUSDT", "range": "7d"}),
        ("/api/futures/top-long-short-position-ratio/history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/futures/top-long-short-account-ratio/history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/futures/global-long-short-account-ratio/history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/spot/orderbook/history", {"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/futures/orderbook/history", {"exchange": "OKX", "symbol": "BTCUSDT", "interval": "1m", "limit": 5}),
        ("/api/spot/orderbook/large-limit-order", {"exchange": "Binance", "symbol": "BTCUSDT"}),
        ("/api/futures/orderbook/large-limit-order", {"exchange": "Binance", "symbol": "BTCUSDT"}),
    ]
    for path, params in calls:
        response = client.get(f"/coinglass{path}", headers=CG_HEADERS, params=params)
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert body["code"] == "0" and body["msg"] == "success"
        assert body["data"] is not None


def test_coinglass_special_shapes(client):
    etf = client.get("/coinglass/api/etf/bitcoin/flow-history", headers=CG_HEADERS).json()["data"][-1]
    assert {"timestamp", "flow_usd", "price_usd", "etf_flows"} <= set(etf)

    order = client.get(
        "/coinglass/api/futures/liquidation/order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTC", "min_liquidation_amount": 0},
    ).json()["data"][0]
    assert {"exchange_name", "symbol", "price", "usd_value", "side", "time"} <= set(order)

    amap = client.get(
        "/coinglass/api/futures/liquidation/aggregated-map",
        headers=CG_HEADERS,
        params={"symbol": "BTC", "range": "7d"},
    ).json()["data"]
    assert "data" in amap and isinstance(amap["data"], dict) and amap["data"]

    heatmap = client.get(
        "/coinglass/api/spot/orderbook/history",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 2},
    ).json()
    assert heatmap["success"] is True
    assert len(heatmap["data"][-1]) == 3

    spot_large = client.get(
        "/coinglass/api/spot/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"][0]
    perp_large = client.get(
        "/coinglass/api/futures/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"][0]
    assert "price" in spot_large
    assert "limit_price" in perp_large


def test_long_short_ratio_field_names(client):
    expectations = {
        "/api/futures/top-long-short-position-ratio/history": "top_position_long_short_ratio",
        "/api/futures/top-long-short-account-ratio/history": "top_account_long_short_ratio",
        "/api/futures/global-long-short-account-ratio/history": "global_account_long_short_ratio",
    }
    for path, field in expectations.items():
        item = client.get(
            f"/coinglass{path}",
            headers=CG_HEADERS,
            params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 2},
        ).json()["data"][-1]
        assert field in item


def test_cryptoquant_4_endpoints(client):
    calls = {
        "/btc/exchange-flows/inflow": {"inflow_total", "inflow_top10", "inflow_mean"},
        "/btc/exchange-flows/outflow": {"outflow_total", "outflow_top10", "outflow_mean"},
        "/btc/exchange-flows/reserve": {"reserve", "reserve_usd"},
        "/btc/flow-indicator/mpi": {"mpi"},
    }
    for path, fields in calls.items():
        params = {"window": "day", "limit": 5, "format": "json"}
        if "exchange-flows" in path:
            params["exchange"] = "all_exchange"
        response = client.get(f"/cryptoquant{path}", headers=CQ_HEADERS, params=params)
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert body["status"]["code"] == 200
        assert body["result"]["window"] == "day"
        item = body["result"]["data"][-1]
        assert {"date"} | fields <= set(item)


def test_cryptoquant_accepts_provider_date_format(client):
    from datetime import datetime, timezone
    now = engine.get_state().timestamp
    stamp = datetime.fromtimestamp(now - 60, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    response = client.get(
        "/cryptoquant/btc/exchange-flows/inflow",
        headers=CQ_HEADERS,
        params={"exchange": "all_exchange", "window": "hour", "from": stamp, "format": "json"},
    )
    assert response.status_code == 200


def test_glassnode_9_endpoints(client):
    paths = (
        "/v1/metrics/market/marketcap_usd",
        "/v1/metrics/derivatives/futures_estimated_leverage_ratio",
        "/v1/metrics/derivatives/dvol_ohlc",
        "/v1/metrics/distribution/balance_miners_sum",
        "/v1/metrics/indicators/sopr",
        "/v1/metrics/mining/hash_rate_mean",
        "/v1/metrics/mining/difficulty_latest",
        "/v1/metrics/transactions/transfers_volume_from_miners_sum",
        "/v1/metrics/mining/revenue_sum",
    )
    for path in paths:
        params = {"a": "BTC", "i": "10m", "f": "json", "timestamp_format": "unix"}
        if path.endswith(("balance_miners_sum", "transfers_volume_from_miners_sum", "revenue_sum")):
            params["c"] = "NATIVE"
        response = client.get(f"/glassnode{path}", headers=GN_HEADERS, params=params)
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert isinstance(body, list) and body
        assert "t" in body[-1]
        if path.endswith("dvol_ohlc"):
            assert set(body[-1]["o"]) == {"o", "h", "l", "c"}
        else:
            assert "v" in body[-1]


def test_provider_authentication(client):
    assert client.get("/coinglass/api/spot/price/history").status_code == 401
    assert client.get("/cryptoquant/btc/exchange-flows/inflow").status_code == 401
    assert client.get("/glassnode/v1/metrics/indicators/sopr").status_code == 401


def test_removed_batch20_provider_paths_are_not_exposed(client):
    removed = (
        "/coinglass/api/futures/price/history",
        "/coinglass/api/futures/open-interest/exchange-list",
        "/coinglass/api/option/info",
        "/cryptoquant/btc/market-data/open-interest",
        "/cryptoquant/btc/market-data/funding-rates",
        "/glassnode/v1/metrics/market/price_usd_ohlc",
        "/glassnode/v1/metrics/market/spot_cvd_sum",
    )
    for path in removed:
        assert client.get(path).status_code == 404


def test_new_data_is_exposed_only_when_endpoint_is_requested(client):
    params = {"symbol": "BTC", "interval": "5m", "limit": 5}
    before = client.get(
        "/coinglass/api/futures/open-interest/aggregated-history",
        headers=CG_HEADERS,
        params=params,
    ).json()["data"][-1]["close"]

    # Engine changes privately; only the following GET serializes/publishes it.
    engine.advance()

    after = client.get(
        "/coinglass/api/futures/open-interest/aggregated-history",
        headers=CG_HEADERS,
        params=params,
    ).json()["data"][-1]["close"]
    assert before != after
