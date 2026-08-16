from copy import deepcopy
from datetime import datetime, timezone

NOW = 1_740_000_000


def feature_input(*, hourly=24, scope="all_exchange", glassnode=True):
    timestamps = [NOW - 3600 * index for index in reversed(range(hourly))]
    current_day = NOW - NOW % 86_400
    reserve_end = current_day - 3_600
    reserve_timestamps = [reserve_end - 3600 * index for index in reversed(range(hourly))]
    def cq(endpoint, field, value):
        source_timestamps = [*reserve_timestamps, NOW] if endpoint == "exchange_reserve" else timestamps
        return [{"timestamp": timestamp, "window": "hour", "exchange_scope": scope, field: value,
                 "provider": "cryptoquant", "endpoint_id": endpoint} for timestamp in source_timestamps]
    datasets = {
        "etf_flows_daily": [
            {"timestamp": NOW - 3 * 86400, "flow_usd": 100.0, "price_usd": 50.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"},
            {"timestamp": NOW, "flow_usd": -120.0, "price_usd": 60.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"}],
        "etf_fund_flows_daily": [
            {"timestamp": NOW, "ticker": "GBTC", "flow_usd": -30.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"},
            {"timestamp": NOW, "ticker": "IBIT", "flow_usd": 70.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"}],
        "etf_funds_snapshot": [
            {"ticker": "GBTC", "fund_name": "Grayscale Bitcoin Trust", "aum_usd": 400.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_list"},
            {"ticker": "IBIT", "fund_name": "iShares Bitcoin Trust", "aum_usd": 600.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_list"}],
        "etf_net_assets_daily": [{"timestamp": NOW, "scope": "aggregate", "ticker": None, "net_assets_usd": 1100.0,
            "change_usd": 10.0, "price_usd": 60.0, "provider": "coinglass", "endpoint_id": "bitcoin_etf_net_assets_history"}],
        "etf_premium_discount_daily": [{"timestamp": NOW, "ticker": "GBTC", "nav_usd": 10.0, "market_price_usd": 12.0,
            "premium_discount_percent": 7.25, "provider": "coinglass", "endpoint_id": "bitcoin_etf_premium_discount_history"}],
        "exchange_balances_snapshot": [
            {"exchange_name": "A", "symbol": "BTC", "total_balance": 100.0, "provider": "coinglass", "endpoint_id": "exchange_balance_list"},
            {"exchange_name": "B", "symbol": "BTC", "total_balance": 200.0, "provider": "coinglass", "endpoint_id": "exchange_balance_list"}],
        "exchange_balances_history": [{"timestamp": NOW, "exchange_name": "A", "balance_btc": 100.0, "price_usd": 60.0,
            "symbol": "BTC", "provider": "coinglass", "endpoint_id": "exchange_balance_chart"}],
        "exchange_inflow": {"hour": cq("exchange_inflow", "inflow_total", 2.0), "day": []},
        "exchange_outflow": {"hour": cq("exchange_outflow", "outflow_total", 1.0), "day": []},
        "exchange_netflow": {"hour": cq("exchange_netflow", "netflow_total", 1.0), "day": []},
        "exchange_reserve": {"hour": cq("exchange_reserve", "reserve", 1000.0), "day": []},
        "secondary_sources": {},
    }
    if glassnode:
        datasets["secondary_sources"] = {"glassnode": {"exchange_balance": {"1h": [{"timestamp": NOW, "value": 990.0,
            "value_raw": 990.0, "asset": "BTC", "interval": "1h", "exchange_scope": None,
            "provider": "glassnode", "endpoint_id": "exchange_balance"}]}}}
    iso = datetime.fromtimestamp(NOW, timezone.utc).isoformat().replace("+00:00", "Z")
    return {"family": "etf_exchange_flows", "stage": "input", "mode": "bootstrap", "data_mode": "live", "is_demo": False,
        "requested_at": iso, "generated_at": iso, "data_as_of": NOW, "datasets": datasets, "invalid_records": {},
        "quality": {"status": "ok", "endpoints": {}, "warnings": [], "errors": []},
        "provenance": {"providers": {}, "endpoint_requests": [], "requested_at": iso, "generated_at": iso, "data_as_of": NOW}}


def cloned_input(**kwargs):
    return deepcopy(feature_input(**kwargs))
