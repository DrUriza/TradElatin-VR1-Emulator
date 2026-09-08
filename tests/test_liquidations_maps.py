CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}

def test_pair_liquidation_map_accepts_hyperliquid_and_returns_500(client):
    hyper = client.get("/coinglass/api/futures/liquidation/map", headers=CG_HEADERS, params={"exchange":"Hyperliquid","symbol":"BTCUSDT","range":"7d"})
    bina = client.get("/coinglass/api/futures/liquidation/map", headers=CG_HEADERS, params={"exchange":"Binance","symbol":"BTCUSDT","range":"7d"})
    assert hyper.status_code == 200, hyper.text
    assert bina.status_code == 200, bina.text
    h = hyper.json()["data"]["data"]
    b = bina.json()["data"]["data"]
    assert len(h) == 500 and len(b) == 500
    assert h != b

def test_long_short_does_not_accept_hyperliquid(client):
    response = client.get("/coinglass/api/futures/global-long-short-account-ratio/history", headers=CG_HEADERS, params={"exchange":"Hyperliquid","symbol":"BTCUSDT","interval":"1m","limit":500})
    assert response.status_code == 400

def test_public_liquidation_map_ranges_are_supported_distinct_and_fixed_500(client):
    payloads = {}
    for range_id in ("1d", "7d", "30d"):
        response = client.get(
            "/coinglass/api/futures/liquidation/map",
            headers=CG_HEADERS,
            params={"exchange": "Binance", "symbol": "BTCUSDT", "range": range_id},
        )
        assert response.status_code == 200, response.text
        levels = response.json()["data"]["data"]
        assert len(levels) == 500
        payloads[range_id] = levels
    assert payloads["1d"] != payloads["7d"]
    assert payloads["7d"] != payloads["30d"]
