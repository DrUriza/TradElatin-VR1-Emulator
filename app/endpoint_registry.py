ENDPOINTS = [
    # CoinGlass — 20
    {"provider": "coinglass", "family": "prices", "endpoint_id": "spot_ohlcv", "path": "/api/spot/price/history"},
    {"provider": "coinglass", "family": "cvd_volume_orderflow", "endpoint_id": "spot_aggregated_cvd", "path": "/api/spot/aggregated-cvd/history"},
    {"provider": "coinglass", "family": "cvd_volume_orderflow", "endpoint_id": "futures_aggregated_cvd", "path": "/api/futures/aggregated-cvd/history"},
    {"provider": "coinglass", "family": "cvd_volume_orderflow|liquidity_microstructure", "endpoint_id": "spot_footprint", "path": "/api/spot/volume/footprint-history"},
    {"provider": "coinglass", "family": "cvd_volume_orderflow|liquidity_microstructure", "endpoint_id": "futures_footprint", "path": "/api/futures/volume/footprint-history"},
    {"provider": "coinglass", "family": "open_interest_and_funding", "endpoint_id": "aggregated_open_interest_ohlc", "path": "/api/futures/open-interest/aggregated-history"},
    {"provider": "coinglass", "family": "open_interest_and_funding", "endpoint_id": "oi_weighted_funding_rate_ohlc", "path": "/api/futures/funding-rate/oi-weight-history"},
    {"provider": "coinglass", "family": "etf_exchange_flows", "endpoint_id": "bitcoin_etf_flows", "path": "/api/etf/bitcoin/flow-history"},
    {"provider": "coinglass", "family": "etf_exchange_flows", "endpoint_id": "bitcoin_etf_list", "path": "/api/etf/bitcoin/list"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "aggregated_liquidation_history", "path": "/api/futures/liquidation/aggregated-history"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "liquidation_order_events", "path": "/api/futures/liquidation/order"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "aggregated_liquidation_map", "path": "/api/futures/liquidation/aggregated-map"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "pair_liquidation_map", "path": "/api/futures/liquidation/map"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "top_position_long_short_ratio", "path": "/api/futures/top-long-short-position-ratio/history"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "top_account_long_short_ratio", "path": "/api/futures/top-long-short-account-ratio/history"},
    {"provider": "coinglass", "family": "liquidations", "endpoint_id": "global_account_long_short_ratio", "path": "/api/futures/global-long-short-account-ratio/history"},
    {"provider": "coinglass", "family": "liquidity_microstructure", "endpoint_id": "spot_orderbook_heatmap", "path": "/api/spot/orderbook/history"},
    {"provider": "coinglass", "family": "liquidity_microstructure", "endpoint_id": "perpetual_orderbook_heatmap", "path": "/api/futures/orderbook/history"},
    {"provider": "coinglass", "family": "liquidity_microstructure", "endpoint_id": "spot_large_limit_orders", "path": "/api/spot/orderbook/large-limit-order"},
    {"provider": "coinglass", "family": "liquidity_microstructure", "endpoint_id": "perpetual_large_limit_orders", "path": "/api/futures/orderbook/large-limit-order"},
    # CryptoQuant — 4
    {"provider": "cryptoquant", "family": "etf_exchange_flows", "endpoint_id": "exchange_inflow", "path": "/btc/exchange-flows/inflow"},
    {"provider": "cryptoquant", "family": "etf_exchange_flows", "endpoint_id": "exchange_outflow", "path": "/btc/exchange-flows/outflow"},
    {"provider": "cryptoquant", "family": "etf_exchange_flows", "endpoint_id": "exchange_reserve", "path": "/btc/exchange-flows/reserve"},
    {"provider": "cryptoquant", "family": "on_chain_miners", "endpoint_id": "mpi", "path": "/btc/flow-indicator/mpi"},
    # Glassnode — 9
    {"provider": "glassnode", "family": "prices", "endpoint_id": "marketcap_usd", "path": "/v1/metrics/market/marketcap_usd"},
    {"provider": "glassnode", "family": "open_interest_and_funding", "endpoint_id": "futures_estimated_leverage_ratio", "path": "/v1/metrics/derivatives/futures_estimated_leverage_ratio"},
    {"provider": "glassnode", "family": "volatility_market_regimes", "endpoint_id": "dvol_ohlc", "path": "/v1/metrics/derivatives/dvol_ohlc"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "balance_miners_sum", "path": "/v1/metrics/distribution/balance_miners_sum"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "sopr", "path": "/v1/metrics/indicators/sopr"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "hash_rate_mean", "path": "/v1/metrics/mining/hash_rate_mean"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "difficulty_latest", "path": "/v1/metrics/mining/difficulty_latest"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "transfers_volume_from_miners_sum", "path": "/v1/metrics/transactions/transfers_volume_from_miners_sum"},
    {"provider": "glassnode", "family": "on_chain_miners", "endpoint_id": "revenue_sum", "path": "/v1/metrics/mining/revenue_sum"},
]


def registered_endpoints() -> list[dict[str, str]]:
    return [dict(item) for item in ENDPOINTS]
