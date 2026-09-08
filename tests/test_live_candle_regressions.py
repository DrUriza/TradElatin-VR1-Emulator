from __future__ import annotations

import math


CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}


def _oi(client, interval: str = "5m") -> list[dict]:
    response = client.get(
        "/coinglass/api/futures/open-interest/aggregated-history",
        headers=CG_HEADERS,
        params={"symbol": "BTC", "interval": interval, "limit": 500},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 500
    return rows


def _prices(client, interval: str) -> list[dict]:
    response = client.get(
        "/coinglass/api/spot/price/history",
        headers=CG_HEADERS,
        params={
            "exchange": "Binance",
            "symbol": "BTCUSDT",
            "interval": interval,
            "limit": 500,
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 500
    return rows


def test_latest_oi_changes_after_admin_advance_and_closed_history_is_immutable(client):
    client.post("/admin/reset")
    before = _oi(client)
    advanced = client.post("/admin/advance")
    assert advanced.status_code == 200
    after = _oi(client)

    assert before[:-1] == after[:-1]
    assert before[-1]["time"] == after[-1]["time"]
    assert before[-1]["close"] != after[-1]["close"]
    assert math.isclose(
        float(after[-1]["close"]),
        float(advanced.json()["open_interest"]),
        rel_tol=2e-12,
    )


def test_prices_remain_valid_and_non_flat_across_repeated_live_updates(client):
    client.post("/admin/reset")
    for interval in ("1m", "5m", "15m", "4h"):
        previous = _prices(client, interval)
        for _ in range(8):
            assert client.post("/admin/advance").status_code == 200
            current = _prices(client, interval)
            assert len(current) == 500
            if previous[-1]["time"] == current[-1]["time"]:
                assert previous[:-1] == current[:-1]
            else:
                assert previous[1:-1] == current[:-2]
            for row in current:
                open_, high, low, close = map(
                    float, (row["open"], row["high"], row["low"], row["close"])
                )
                assert math.isfinite(open_ + high + low + close)
                assert low <= min(open_, close) <= max(open_, close) <= high
            closes = [float(row["close"]) for row in current]
            assert all(a != b for a, b in zip(closes[:-2], closes[1:-1]))
            previous = current
