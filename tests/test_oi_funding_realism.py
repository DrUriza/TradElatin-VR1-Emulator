import math
import statistics

CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}
PUBLIC_OI_TIMEFRAMES = ("5m", "15m", "4h")

def _rows(client, path, interval):
    response = client.get(path, headers=CG_HEADERS, params={"symbol": "BTC", "interval": interval, "limit": 500})
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 500
    return rows

def test_oi_public_surface_is_three_timeframes_and_non_monotonic(client):
    for interval in PUBLIC_OI_TIMEFRAMES:
        rows = _rows(client, "/coinglass/api/futures/open-interest/aggregated-history", interval)
        closes = [float(row["close"]) for row in rows]
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        up = sum(value > 0 for value in returns)
        down = sum(value < 0 for value in returns)
        assert up > 90 and down > 90
        assert statistics.pstdev(returns) > 8e-5
        ranged = sum(float(row["high"]) > float(row["low"]) for row in rows[:-1])
        assert ranged / max(1, len(rows) - 1) > 0.95

def test_funding_is_zero_centered_but_not_flat(client):
    for interval in PUBLIC_OI_TIMEFRAMES:
        rows = _rows(client, "/coinglass/api/futures/funding-rate/oi-weight-history", interval)
        closes = [float(row["close"]) for row in rows]
        assert abs(statistics.mean(closes)) < 5e-5
        assert statistics.pstdev(closes) > 5e-6
        assert min(closes) < 0 < max(closes)


def test_oi_one_minute_is_not_public():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    headers = CG_HEADERS
    for path in ("/coinglass/api/futures/open-interest/aggregated-history", "/coinglass/api/futures/funding-rate/oi-weight-history"):
        response = client.get(path, headers=headers, params={"symbol":"BTC","interval":"1m","limit":500})
        assert response.status_code == 400
