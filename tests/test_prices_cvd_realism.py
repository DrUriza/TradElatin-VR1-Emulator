import math
import statistics

CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}
PRICE_TIMEFRAMES = ("1m", "5m", "15m", "4h")
CVD_TIMEFRAMES = ("5m", "15m", "4h")


def _corr(left, right):
    ml = statistics.mean(left)
    mr = statistics.mean(right)
    numerator = sum((a - ml) * (b - mr) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - ml) ** 2 for a in left) * sum((b - mr) ** 2 for b in right)
    )
    return numerator / denominator if denominator else 0.0


def _max_cycle_autocorrelation(values, min_lag=8, max_lag=80):
    upper = min(max_lag, len(values) // 3)
    return max(abs(_corr(values[:-lag], values[lag:])) for lag in range(min_lag, upper + 1))


def test_prices_are_spot_only_four_timeframes_and_non_artificial(client):
    registry = client.get("/admin/endpoints").json()["endpoints"]
    price_entries = [row for row in registry if row["family"] == "prices"]
    assert {(row["provider"], row["endpoint_id"]) for row in price_entries} == {("coinglass", "spot_ohlcv"), ("glassnode", "marketcap_usd")}
    assert not any("futures" in str(row.get("path", "")).lower() and "price" in str(row.get("path", "")).lower() for row in registry)

    for interval in PRICE_TIMEFRAMES:
        response = client.get(
            "/coinglass/api/spot/price/history",
            headers=CG_HEADERS,
            params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": interval, "limit": 500},
        )
        assert response.status_code == 200, response.text
        candles = response.json()["data"]
        assert len(candles) == 500
        closes = [float(row["close"]) for row in candles]
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        assert sum(abs(value) < 1e-12 for value in returns) == 0
        assert statistics.pstdev(returns) > 1e-4
        assert max(returns) - min(returns) > 5e-4
        assert _max_cycle_autocorrelation(returns) < 0.45
        zero_range = []
        for index, row in enumerate(candles):
            o, h, l, c = map(float, (row["open"], row["high"], row["low"], row["close"]))
            assert l <= min(o, c) <= max(o, c) <= h
            if h == l:
                zero_range.append(index)
        # Only the still-forming current bucket may legitimately have one sample.
        assert zero_range in ([], [len(candles) - 1])


def test_spot_and_futures_cvd_are_distinct_and_not_sinusoidal(client):
    for interval in CVD_TIMEFRAMES:
        params = {"exchange_list": "Binance", "symbol": "BTC", "interval": interval, "limit": 500, "unit": "usd"}
        spot = client.get("/coinglass/api/spot/aggregated-cvd/history", headers=CG_HEADERS, params=params)
        futures = client.get("/coinglass/api/futures/aggregated-cvd/history", headers=CG_HEADERS, params=params)
        assert spot.status_code == futures.status_code == 200
        spot_rows, futures_rows = spot.json()["data"], futures.json()["data"]
        assert len(spot_rows) == len(futures_rows) == 500
        spot_cvd = [float(row["cum_vol_delta"]) for row in spot_rows]
        futures_cvd = [float(row["cum_vol_delta"]) for row in futures_rows]
        assert spot_cvd != futures_cvd
        spot_delta = [spot_cvd[i] - spot_cvd[i - 1] for i in range(1, len(spot_cvd))]
        futures_delta = [futures_cvd[i] - futures_cvd[i - 1] for i in range(1, len(futures_cvd))]
        correlation = _corr(spot_delta, futures_delta)
        assert 0.20 < correlation < 0.92
        assert statistics.pstdev(futures_delta) > statistics.pstdev(spot_delta) * 1.10
        # A deterministic sine fixture has near-perfect autocorrelation at its period.
        # Stochastic order flow may be persistent at short lags, but must not repeat a cycle.
        assert _max_cycle_autocorrelation(spot_delta) < 0.65
        assert _max_cycle_autocorrelation(futures_delta) < 0.65


def test_price_candles_have_realistic_intrabar_wicks(client):
    """Twelve intra-bar samples should avoid body-only staircase candles."""
    for interval in PRICE_TIMEFRAMES:
        response = client.get(
            "/coinglass/api/spot/price/history", headers=CG_HEADERS,
            params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": interval, "limit": 500},
        )
        assert response.status_code == 200
        rows = response.json()["data"][:-1]
        body_fractions = []
        wick_bars = 0
        for row in rows:
            o, h, l, c = map(float, (row["open"], row["high"], row["low"], row["close"]))
            span = h - l
            if span <= 0:
                continue
            body = abs(c - o)
            body_fractions.append(body / span)
            if h > max(o, c) and l < min(o, c):
                wick_bars += 1
        assert len(body_fractions) > 450
        assert statistics.median(body_fractions) < 0.72
        assert wick_bars > len(body_fractions) * 0.18


def test_cvd_rejects_removed_one_minute_timeframe_but_prices_keep_it(client):
    cvd = client.get(
        "/coinglass/api/spot/aggregated-cvd/history",
        headers=CG_HEADERS,
        params={"exchange_list": "Binance", "symbol": "BTC", "interval": "1m", "limit": 500, "unit": "usd"},
    )
    assert cvd.status_code == 400
    assert "Allowed values" in cvd.json()["msg"]

    prices = client.get(
        "/coinglass/api/spot/price/history",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 500},
    )
    assert prices.status_code == 200
    assert len(prices.json()["data"]) == 500
