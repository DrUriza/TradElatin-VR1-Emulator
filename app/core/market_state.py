from dataclasses import asdict, dataclass
from time import time

from app.core.black_scholes import black_scholes_snapshot


@dataclass
class MarketState:
    timestamp: int
    btc_price: float
    volume: float
    open_interest: float
    funding_rate: float
    exchange_reserve: float
    sopr: float
    long_liquidations: float
    short_liquidations: float
    regime: str
    taker_buy_volume_usd: float = 0.0
    taker_sell_volume_usd: float = 0.0
    cumulative_volume_delta_usd: float = 0.0
    futures_taker_buy_volume_usd: float = 0.0
    futures_taker_sell_volume_usd: float = 0.0
    futures_cumulative_volume_delta_usd: float = 0.0
    order_flow_imbalance: float = 0.0
    futures_order_flow_imbalance: float = 0.0
    annualized_volatility: float = 0.55
    realized_volatility: float = 0.55
    implied_volatility: float = 0.60
    option_call_price: float = 0.0
    option_put_price: float = 0.0
    option_delta: float = 0.5
    option_gamma: float = 0.0
    option_vega: float = 0.0
    dealer_gamma_pressure: float = 0.0
    futures_basis_pct: float = 0.0
    liquidity_stress: float = 0.10
    last_log_return: float = 0.0
    exchange_inflow: float = 0.0
    exchange_outflow: float = 0.0
    miner_balance: float = 0.0
    mpi: float = 0.0
    hash_rate: float = 0.0
    difficulty: float = 0.0
    miner_transfer_volume: float = 0.0
    miner_revenue: float = 0.0
    dvol: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def initial_market_state() -> MarketState:
    price = 100_000.0
    # Approximate per-10-second BTC flow.  Historical generation scales this
    # with dt, so 1m/1h/1d volumes remain naturally different.
    base_volume = 18.0
    quote_volume = price * base_volume
    iv = 0.60
    strike = round(price / 1000.0) * 1000.0
    option = black_scholes_snapshot(
        spot=price,
        strike=strike,
        time_to_expiry_years=30.0 / 365.25,
        risk_free_rate=0.04,
        implied_volatility=iv,
    )
    return MarketState(
        timestamp=int(time()),
        btc_price=price,
        volume=base_volume,
        open_interest=35_000_000_000.0,
        funding_rate=0.0001,
        exchange_reserve=2_350_000.0,
        sopr=1.0,
        long_liquidations=0.0,
        short_liquidations=0.0,
        regime="lateral",
        taker_buy_volume_usd=quote_volume * 0.5,
        taker_sell_volume_usd=quote_volume * 0.5,
        cumulative_volume_delta_usd=0.0,
        futures_taker_buy_volume_usd=quote_volume * 0.85,
        futures_taker_sell_volume_usd=quote_volume * 0.85,
        futures_cumulative_volume_delta_usd=0.0,
        order_flow_imbalance=0.0,
        futures_order_flow_imbalance=0.0,
        annualized_volatility=0.55,
        realized_volatility=0.55,
        implied_volatility=iv,
        option_call_price=option.call_price,
        option_put_price=option.put_price,
        option_delta=option.call_delta,
        option_gamma=option.gamma,
        option_vega=option.vega,
        dealer_gamma_pressure=0.0,
        futures_basis_pct=0.0002,
        liquidity_stress=0.10,
        last_log_return=0.0,
        exchange_inflow=310.0,
        exchange_outflow=315.0,
        miner_balance=1_820_000.0,
        mpi=0.0,
        hash_rate=8.5e20,
        difficulty=1.29e14,
        miner_transfer_volume=275.0,
        miner_revenue=4.7e6,
        dvol=iv * 100.0,
    )
