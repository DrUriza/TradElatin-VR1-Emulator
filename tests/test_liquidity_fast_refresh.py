from app.core.simulation_engine import engine
from app.settings import EMULATOR_RECORD_COUNT

CG_HEADERS = {"CG-API-KEY": "emulator-coinglass-key"}


def _get(client, path: str):
    response = client.get(
        path,
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT", "interval": "1m", "limit": 500},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == EMULATOR_RECORD_COUNT
    return data


def test_liquidity_primitives_change_on_next_simulation_tick(client):
    orderbook_a = _get(client, "/coinglass/api/spot/orderbook/history")
    footprint_a = _get(client, "/coinglass/api/spot/volume/footprint-history")
    whales_a = client.get(
        "/coinglass/api/spot/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"]
    assert len(whales_a) == EMULATOR_RECORD_COUNT

    engine.advance()

    orderbook_b = _get(client, "/coinglass/api/spot/orderbook/history")
    footprint_b = _get(client, "/coinglass/api/spot/volume/footprint-history")
    whales_b = client.get(
        "/coinglass/api/spot/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"]
    assert len(whales_b) == EMULATOR_RECORD_COUNT

    assert orderbook_a[-1] != orderbook_b[-1]
    assert footprint_a[-1] != footprint_b[-1]

    ids_a = {row["id"] for row in whales_a}
    ids_b = {row["id"] for row in whales_b}
    common = ids_a & ids_b
    assert 0 < len(common) < EMULATOR_RECORD_COUNT
    by_id_a = {row["id"]: row for row in whales_a}
    by_id_b = {row["id"]: row for row in whales_b}
    assert any(by_id_a[item]["current_usd_value"] != by_id_b[item]["current_usd_value"] for item in common)


def test_large_limit_order_side_geometry_matches_bid_ask_semantics(client):
    rows = client.get(
        "/coinglass/api/spot/orderbook/large-limit-order",
        headers=CG_HEADERS,
        params={"exchange": "Binance", "symbol": "BTCUSDT"},
    ).json()["data"]
    assert len(rows) == EMULATOR_RECORD_COUNT
    mid = engine.get_state().btc_price
    buys = [row for row in rows if row["order_side"] == 1]
    sells = [row for row in rows if row["order_side"] == 2]
    assert buys and sells
    assert all(float(row["price"]) < mid for row in buys)
    assert all(float(row["price"]) > mid for row in sells)


def test_liquidity_orderbook_exposes_at_least_fifteen_levels_per_side(client):
    data = _get(client, "/coinglass/api/spot/orderbook/history")
    latest = data[-1]
    assert len(latest[1]) >= 15
    assert len(latest[2]) >= 15
