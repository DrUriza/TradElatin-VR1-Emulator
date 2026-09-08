import math
import statistics

from app.core.black_scholes import black_scholes_snapshot
from app.core.series import aggregate_candles
from app.core.simulation_engine import SimulationEngine


def _corr(a, b):
    ma = statistics.mean(a)
    mb = statistics.mean(b)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    denominator = math.sqrt(
        sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)
    )
    return numerator / denominator


def test_black_scholes_put_call_parity_and_greeks():
    snapshot = black_scholes_snapshot(
        spot=100_000.0,
        strike=100_000.0,
        time_to_expiry_years=30.0 / 365.25,
        risk_free_rate=0.04,
        implied_volatility=0.60,
    )
    rhs = snapshot.spot - snapshot.strike * math.exp(
        -snapshot.risk_free_rate * snapshot.time_to_expiry_years
    )
    assert abs((snapshot.call_price - snapshot.put_price) - rhs) < 1e-6
    assert 0.0 < snapshot.call_delta < 1.0
    assert snapshot.gamma > 0.0
    assert snapshot.vega > 0.0


def test_history_has_nontrivial_returns_and_volume_variation():
    engine = SimulationEngine(update_seconds=10, history_size=500, seed=4271)
    history = engine.get_synthetic_history(
        interval_seconds=60,
        periods=500,
        samples_per_period=4,
    )
    candles = aggregate_candles(
        history,
        "1m",
        lambda item: item.btc_price,
        lambda item: item.volume * item.btc_price,
    )
    returns = [math.log(candles[i].close / candles[i - 1].close) for i in range(1, len(candles))]
    volumes = [item.volume for item in candles]
    assert len(candles) == 500
    assert statistics.pstdev(returns) > 0.0002
    assert statistics.pstdev(volumes) / statistics.mean(volumes) > 0.10
    assert max(returns) - min(returns) > 0.001


def test_order_flow_is_causal_but_not_perfectly_correlated():
    engine = SimulationEngine(update_seconds=10, history_size=500, seed=4271)
    history = engine.get_synthetic_history(
        interval_seconds=60,
        periods=500,
        samples_per_period=4,
    )
    candles = aggregate_candles(history, "1m", lambda item: item.btc_price)
    returns = [math.log(candles[i].close / candles[i - 1].close) for i in range(1, len(candles))]
    spot_delta = [
        sum(state.taker_buy_volume_usd - state.taker_sell_volume_usd for state in candle.states)
        for candle in candles[1:]
    ]
    futures_delta = [
        sum(state.futures_taker_buy_volume_usd - state.futures_taker_sell_volume_usd for state in candle.states)
        for candle in candles[1:]
    ]
    price_flow_corr = _corr(returns, spot_delta)
    spot_futures_corr = _corr(spot_delta, futures_delta)
    assert 0.15 < price_flow_corr < 0.90
    assert 0.35 < spot_futures_corr < 0.98


def test_option_and_risk_state_evolves():
    engine = SimulationEngine(update_seconds=10, history_size=500, seed=77)
    before = engine.get_state()
    for _ in range(20):
        engine.advance()
    after = engine.get_state()
    assert after.implied_volatility != before.implied_volatility
    assert after.option_call_price != before.option_call_price
    assert after.option_gamma > 0.0
    assert -1.0 <= after.dealer_gamma_pressure <= 1.0


def test_liquidation_shocks_deleverage_open_interest_across_public_timeframes():
    """Severe liquidation buckets must reduce OI on average on public OI timeframes."""
    for interval_seconds in (60, 300, 900):
        engine = SimulationEngine(update_seconds=10, history_size=500, seed=4271)
        history = engine.get_synthetic_history(
            interval_seconds=interval_seconds,
            periods=500,
            samples_per_period=4,
        )
        observations = []
        for previous, current in zip(history, history[1:]):
            liquidation_usd = current.long_liquidations + current.short_liquidations
            oi_log_return = math.log(current.open_interest / previous.open_interest)
            observations.append((liquidation_usd, oi_log_return))
        observations.sort(key=lambda item: item[0])
        cutoff = int(len(observations) * 0.95)
        severe = observations[cutoff:]
        normal = observations[:cutoff]
        assert statistics.mean(item[1] for item in severe) < 0.0
        assert statistics.mean(item[1] for item in severe) < statistics.mean(item[1] for item in normal)
