import asyncio
import math
import random
from collections import deque
from copy import deepcopy
from threading import Lock

from app.core.black_scholes import black_scholes_snapshot
from app.core.market_state import MarketState, initial_market_state
from app.core.regimes import REGIME_MODEL_PARAMETERS, REGIME_TRANSITIONS, SUPPORTED_REGIMES
from app.settings import (
    SIMULATION_HISTORY_SIZE,
    SIMULATION_INITIAL_REGIME,
    SIMULATION_SEED,
    SIMULATION_UPDATE_SECONDS,
)

SECONDS_PER_YEAR = 365.25 * 24.0 * 3600.0
RISK_FREE_RATE = 0.04
OPTION_TENOR_YEARS = 30.0 / 365.25
SPOT_FLOW_PRICE_IMPACT = 0.42
FUTURES_FLOW_PRICE_IMPACT = 0.16
LIQUIDATION_SHOCK_MEAN = 0.008
LIQUIDATION_SHOCK_SIGMA = 0.007
MAX_ABS_LOG_RETURN = 0.12


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _student_t_unit_variance(rng: random.Random, df: float = 5.0) -> float:
    """Heavy-tail innovation standardized to approximately unit variance."""
    z = rng.gauss(0.0, 1.0)
    chi2 = rng.gammavariate(df / 2.0, 2.0)
    t_value = z / math.sqrt(max(chi2 / df, 1e-12))
    return t_value * math.sqrt((df - 2.0) / df)


def _weighted_choice(rng: random.Random, items: tuple[tuple[str, float], ...]) -> str:
    draw = rng.random()
    cumulative = 0.0
    for name, weight in items:
        cumulative += weight
        if draw <= cumulative:
            return name
    return items[-1][0]


class SimulationEngine:
    """Coherent multi-factor synthetic BTC market.

    Quantitative layers:
    - Black-Scholes GBM as the base spot diffusion.
    - EWMA/GARCH-like stochastic annualized volatility.
    - Student-t innovations + Merton jumps for fat tails.
    - Markov market regimes.
    - Black-Scholes option valuation/Greeks as latent risk state.
    - Spot/futures order flow, OI, funding, liquidations and liquidity coupled
      to the same state rather than independently randomized endpoints.
    """

    def __init__(
        self,
        update_seconds: int = 10,
        history_size: int = 500,
        seed: int = 4271,
    ) -> None:
        self.update_seconds = update_seconds
        self.seed = int(seed)
        self.random = random.Random(seed)
        self.state = initial_market_state()
        self.state.regime = SIMULATION_INITIAL_REGIME
        self.history: deque[MarketState] = deque(maxlen=history_size)
        self.history.append(deepcopy(self.state))
        self.lock = Lock()
        self.running = True
        self._history_cache: dict[tuple[int, int, int, int, int], list[MarketState]] = {}

    def _maybe_transition_regime(self, state: MarketState, dt_seconds: float, rng: random.Random) -> None:
        params = REGIME_MODEL_PARAMETERS[state.regime]
        mean_duration = max(params["mean_duration_seconds"], 1.0)
        transition_probability = 1.0 - math.exp(-max(dt_seconds, 0.0) / mean_duration)
        if rng.random() < transition_probability:
            state.regime = _weighted_choice(rng, REGIME_TRANSITIONS[state.regime])

    def _option_layer(self, state: MarketState) -> None:
        # IV includes a volatility-risk premium and rises during stressed books.
        regime_vol = REGIME_MODEL_PARAMETERS[state.regime]["volatility"]
        iv = (
            0.72 * state.annualized_volatility
            + 0.20 * regime_vol
            + 0.08 * state.realized_volatility
        )
        iv *= 1.0 + 0.28 * state.liquidity_stress
        state.implied_volatility = _clamp(iv, 0.20, 1.80)

        # Round to the nearest $1k strike so ATM moneyness evolves naturally.
        strike = max(1000.0, round(state.btc_price / 1000.0) * 1000.0)
        option = black_scholes_snapshot(
            spot=state.btc_price,
            strike=strike,
            time_to_expiry_years=OPTION_TENOR_YEARS,
            risk_free_rate=RISK_FREE_RATE,
            implied_volatility=state.implied_volatility,
        )
        state.option_call_price = option.call_price
        state.option_put_price = option.put_price
        state.option_delta = option.call_delta
        state.option_gamma = option.gamma
        state.option_vega = option.vega

        # Synthetic dealer gamma pressure: crowded leveraged positioning tends
        # to produce short-gamma amplification; balanced positioning dampens.
        crowding = math.tanh(
            1.8 * state.futures_order_flow_imbalance
            + 4200.0 * state.funding_rate
            + 65.0 * state.futures_basis_pct
        )
        gamma_scale = _clamp(option.gamma * state.btc_price * 65.0, 0.0, 1.0)
        state.dealer_gamma_pressure = _clamp(-crowding * gamma_scale, -1.0, 1.0)
        state.dvol = state.implied_volatility * 100.0

    def _step_state(
        self,
        previous: MarketState,
        *,
        dt_seconds: float,
        rng: random.Random,
        allow_regime_transition: bool = True,
    ) -> MarketState:
        dt_seconds = max(float(dt_seconds), 1.0)
        dt_years = dt_seconds / SECONDS_PER_YEAR
        root_dt = math.sqrt(dt_years)
        state = deepcopy(previous)
        if allow_regime_transition:
            self._maybe_transition_regime(state, dt_seconds, rng)
        params = REGIME_MODEL_PARAMETERS[state.regime]

        # Correlated spot/futures aggression.  Futures is intentionally more
        # reflexive to leverage and funding than spot.
        heavy_tail = _student_t_unit_variance(rng, 5.0)
        flow_noise = 0.10 * _student_t_unit_variance(rng, 7.0)
        state.order_flow_imbalance = _clamp(
            0.82 * previous.order_flow_imbalance
            + 0.12 * params["flow_bias"]
            + 0.10 * math.tanh(heavy_tail / 1.8)
            + flow_noise,
            -0.96,
            0.96,
        )
        # Futures flow shares the macro impulse with spot but has its own
        # leverage/funding memory and heavier independent innovation.  This
        # keeps the two CVDs causally related without producing two rescaled
        # copies of the same path.
        leverage_noise = 0.20 * _student_t_unit_variance(rng, 6.0)
        state.futures_order_flow_imbalance = _clamp(
            0.48 * state.order_flow_imbalance
            + 0.31 * previous.futures_order_flow_imbalance
            + 0.12 * math.tanh(5000.0 * previous.funding_rate)
            + leverage_noise,
            -0.98,
            0.98,
        )

        sigma = _clamp(previous.annualized_volatility, 0.15, 1.80)
        mu = params["drift"]

        # Black-Scholes underlying dynamics (GBM) in log-return form.
        diffusion = (mu - 0.5 * sigma * sigma) * dt_years + sigma * root_dt * heavy_tail

        # Order-flow impact.  It is deliberately imperfect: price/CVD
        # correlation is positive but not 1, allowing absorption/divergence.
        flow_impact = state.order_flow_imbalance * sigma * root_dt * SPOT_FLOW_PRICE_IMPACT
        futures_impact = (
            state.futures_order_flow_imbalance
            * sigma
            * root_dt
            * FUTURES_FLOW_PRICE_IMPACT
        )

        # Latent gamma environment from the previous option state.
        gamma_multiplier = 1.0
        if previous.dealer_gamma_pressure < 0.0:
            gamma_multiplier += 0.32 * abs(previous.dealer_gamma_pressure)
        else:
            gamma_multiplier -= 0.18 * previous.dealer_gamma_pressure

        # Merton jump-diffusion component.
        jump = 0.0
        jump_probability = 1.0 - math.exp(-params["jump_intensity"] * dt_years)
        if rng.random() < jump_probability:
            jump = rng.gauss(params["jump_mean"], params["jump_sigma"])

        # Liquidation-event regime receives a one-sided reflexive shock whose
        # sign depends on leverage crowding (long squeeze vs short squeeze).
        event_shock = 0.0
        if state.regime == "liquidation_event":
            crowding_sign = 1.0 if previous.funding_rate < -0.00015 else -1.0
            event_shock = crowding_sign * abs(
                rng.gauss(LIQUIDATION_SHOCK_MEAN, LIQUIDATION_SHOCK_SIGMA)
            ) * min(1.0, dt_seconds / 60.0)

        log_return = (diffusion + flow_impact + futures_impact) * gamma_multiplier + jump + event_shock
        log_return = _clamp(log_return, -MAX_ABS_LOG_RETURN, MAX_ABS_LOG_RETURN)
        state.last_log_return = log_return
        state.btc_price = max(1.0, previous.btc_price * math.exp(log_return))
        state.timestamp = previous.timestamp + int(round(dt_seconds))

        # Stochastic volatility: long-run regime variance + realized shock.
        realized_ann_var = min(3.24, (log_return * log_return) / max(dt_years, 1e-12))
        long_run_var = params["volatility"] ** 2
        new_var = 0.92 * (sigma ** 2) + 0.055 * long_run_var + 0.025 * realized_ann_var
        state.annualized_volatility = _clamp(math.sqrt(max(new_var, 1e-8)), 0.15, 1.80)
        state.realized_volatility = _clamp(
            math.sqrt(max(0.90 * previous.realized_volatility ** 2 + 0.10 * realized_ann_var, 1e-8)),
            0.12,
            2.00,
        )

        # Liquidity stress clusters after volatility/jumps/liquidations instead
        # of resetting independently at every endpoint.
        shock_z = abs(log_return) / max(sigma * root_dt, 1e-8)
        stress_target = _clamp(
            0.10
            + 0.16 * abs(state.order_flow_imbalance)
            + 0.12 * abs(state.futures_order_flow_imbalance)
            + 0.10 * min(shock_z, 5.0)
            + (0.35 if jump else 0.0),
            0.02,
            1.0,
        )
        state.liquidity_stress = _clamp(0.82 * previous.liquidity_stress + 0.18 * stress_target, 0.02, 1.0)

        # Volume is scale-consistent with dt and expands with volatility,
        # imbalance and absolute price movement.  Lognormal noise prevents the
        # nearly constant bars seen in the previous emulator.
        base_btc_per_second = 1.45
        activity = (
            0.55
            + 0.85 * (state.annualized_volatility / 0.55)
            + 1.15 * abs(state.order_flow_imbalance)
            + 0.65 * min(shock_z, 4.0)
            + 0.70 * state.liquidity_stress
        )
        volume_noise = math.exp(rng.gauss(-0.5 * 0.28 * 0.28, 0.28))
        state.volume = max(0.01, base_btc_per_second * dt_seconds * activity * volume_noise)
        quote_volume = state.volume * state.btc_price

        # Spot and futures taker flow are separate.  Buy/sell shares use a
        # smooth logistic-style map so they can reach extremes without clipping
        # into repeated fixed percentages.
        spot_buy_share = 0.5 + 0.46 * math.tanh(1.55 * state.order_flow_imbalance + rng.gauss(0.0, 0.10))
        futures_buy_share = 0.5 + 0.47 * math.tanh(1.70 * state.futures_order_flow_imbalance + rng.gauss(0.0, 0.11))
        spot_buy_share = _clamp(spot_buy_share, 0.03, 0.97)
        futures_buy_share = _clamp(futures_buy_share, 0.02, 0.98)

        state.taker_buy_volume_usd = quote_volume * spot_buy_share
        state.taker_sell_volume_usd = quote_volume * (1.0 - spot_buy_share)
        spot_delta = state.taker_buy_volume_usd - state.taker_sell_volume_usd
        state.cumulative_volume_delta_usd = previous.cumulative_volume_delta_usd + spot_delta

        futures_multiplier = _clamp(
            1.35
            + 0.75 * abs(state.futures_order_flow_imbalance)
            + 0.50 * state.liquidity_stress
            + rng.gauss(0.0, 0.10),
            0.85,
            3.8,
        )
        futures_quote_volume = quote_volume * futures_multiplier
        state.futures_taker_buy_volume_usd = futures_quote_volume * futures_buy_share
        state.futures_taker_sell_volume_usd = futures_quote_volume * (1.0 - futures_buy_share)
        futures_delta = state.futures_taker_buy_volume_usd - state.futures_taker_sell_volume_usd
        state.futures_cumulative_volume_delta_usd = previous.futures_cumulative_volume_delta_usd + futures_delta

        # Futures basis / funding / OI share the same leveraged-flow state.
        basis_target = (
            0.00010
            + 0.00120 * state.futures_order_flow_imbalance
            + 0.00035 * math.tanh(log_return * 350.0)
        )
        state.futures_basis_pct = _clamp(
            0.88 * previous.futures_basis_pct + 0.12 * basis_target + rng.gauss(0.0, 0.000025),
            -0.006,
            0.006,
        )
        # Funding stays centered near zero, but forms short-lived clusters
        # when basis, leveraged flow and volatility align.
        funding_target = (
            0.050 * state.futures_basis_pct
            + 0.000060 * state.futures_order_flow_imbalance
            + 0.000018 * math.tanh(log_return * 420.0)
        )
        minute_fraction = max(dt_seconds / 60.0, 1.0 / 60.0)
        # 0.22 was historically calibrated as a one-minute update weight.
        # Convert it to the actual emulator dt so a 1-second internal clock
        # does not apply one minute of funding mean reversion sixty times.
        funding_alpha = 1.0 - (1.0 - 0.22) ** minute_fraction
        funding_shock = rng.gauss(0.0, 0.000010 * math.sqrt(minute_fraction))
        if state.regime in {"high_volatility", "liquidation_event"}:
            funding_shock += rng.gauss(0.0, 0.000018 * math.sqrt(minute_fraction))
        state.funding_rate = _clamp(
            (1.0 - funding_alpha) * previous.funding_rate + funding_alpha * funding_target + funding_shock,
            -0.00045,
            0.00045,
        )

        # Liquidations are produced by standardized moves + leveraged crowding.
        trigger = max(0.0, shock_z - 1.15)
        crowding = 1.0 + 1.5 * abs(state.futures_order_flow_imbalance) + 1200.0 * abs(state.funding_rate)
        liq_fraction = _clamp((trigger ** 1.55) * 0.00055 * crowding, 0.0, 0.055)
        liq_notional = previous.open_interest * liq_fraction
        if log_return < 0.0:
            state.long_liquidations = liq_notional
            state.short_liquidations = liq_notional * 0.06
        else:
            state.short_liquidations = liq_notional
            state.long_liquidations = liq_notional * 0.06

        if state.regime == "liquidation_event":
            extra = previous.open_interest * _clamp(0.004 + 0.02 * state.liquidity_stress, 0.0, 0.04)
            if log_return < 0.0:
                state.long_liquidations += extra
            else:
                state.short_liquidations += extra

        liquidation_ratio = (state.long_liquidations + state.short_liquidations) / max(previous.open_interest, 1.0)

        # OI is participation, not a monotonic function of |price return|.
        # Leverage builds in active regimes, contracts in reversals/risk-off,
        # mean-reverts around a broad market anchor and deleverages on shocks.
        oi_anchor = 35_000_000_000.0
        anchor_gap = math.log(max(previous.open_interest, 1.0) / oi_anchor)
        participation = (
            0.000085 * (abs(state.futures_order_flow_imbalance) - 0.30)
            + 0.000032 * (min(shock_z, 4.0) - 0.70)
            + 0.000020 * math.tanh(abs(state.funding_rate) * 4200.0 - 0.35)
        )
        # Participation persists by regime: directional markets can build
        # leverage, while reversals/liquidation regimes visibly deleverage.
        # The magnitudes are deliberately below one basis point per internal
        # step so 1m/5m/15m candles remain plausible rather than saw-toothed.
        regime_term = {
            "bullish": 0.000055,
            "bearish": 0.000045,
            "lateral": -0.000012,
            "reversal": -0.000085,
            "high_volatility": -0.000035,
            "liquidation_event": -0.000180,
        }.get(state.regime, 0.0)
        oi_noise = rng.gauss(0.0, 0.00024 * math.sqrt(minute_fraction))

        # Rare participation bursts are specified as a per-minute hazard and
        # converted to the actual dt.  This avoids ~60x excessive shocks when
        # the private market state advances once per second.
        jump_probability_minute = _clamp(0.004 + 0.018 * state.liquidity_stress + 0.003 * min(shock_z, 4.0), 0.0, 0.045)
        jump_probability = 1.0 - (1.0 - jump_probability_minute) ** minute_fraction
        oi_jump = 0.0
        if rng.random() < jump_probability:
            jump_size = abs(rng.gauss(0.00055, 0.00040))
            if state.regime in {"reversal", "liquidation_event"} or liquidation_ratio > 0.002:
                oi_jump = -jump_size
            elif state.regime in {"bullish", "bearish"} and abs(state.futures_order_flow_imbalance) > 0.45:
                oi_jump = jump_size
            else:
                oi_jump = jump_size if rng.random() >= 0.5 else -jump_size

        oi_log_change = (
            (participation + regime_term) * minute_fraction
            - 0.006 * anchor_gap * min(dt_seconds / 3600.0, 1.0)
            - 0.88 * min(liquidation_ratio, 0.020)
            + oi_noise
            + oi_jump
        )
        state.open_interest = max(1.0e8, previous.open_interest * math.exp(_clamp(oi_log_change, -0.0055, 0.0055)))

        # Option surface and Greeks are refreshed after spot/OI/funding so the
        # next step sees a coherent latent gamma environment.
        self._option_layer(state)

        # Exchange/on-chain/miner variables are slower factors.  Their scale is
        # proportional to simulated dt rather than fluctuating like short-horizon CVD.
        day_fraction = dt_seconds / 86_400.0
        # Exchange flows are large two-sided transfers, while the reserve is a
        # slow stock variable.  Use one shared activity process and a bounded
        # directional split so gross inflow/outflow can be volatile without
        # creating an unrealistic one-way multi-million-BTC reserve drift.
        flow_base = 6_000.0 * day_fraction
        flow_noise_scale = math.sqrt(max(day_fraction, 1e-6))
        gross_activity = (1.0 + 0.55 * state.liquidity_stress) * math.exp(rng.gauss(0.0, 0.14 * flow_noise_scale))
        sell_bias = max(0.0, -state.order_flow_imbalance) + max(0.0, -log_return * 80.0)
        buy_bias = max(0.0, state.order_flow_imbalance) + max(0.0, log_return * 80.0)
        reserve_anchor = 2_350_000.0
        reserve_gap = (reserve_anchor - previous.exchange_reserve) / reserve_anchor
        direction = math.tanh(0.90 * (sell_bias - buy_bias) + 10.0 * reserve_gap)
        inflow_share = _clamp(0.50 + 0.075 * direction + rng.gauss(0.0, 0.018 * flow_noise_scale), 0.38, 0.62)
        gross_two_sided = max(0.0, 2.0 * flow_base * gross_activity)
        state.exchange_inflow = gross_two_sided * inflow_share
        state.exchange_outflow = gross_two_sided * (1.0 - inflow_share)
        state.exchange_reserve = max(
            1.0,
            previous.exchange_reserve + state.exchange_inflow - state.exchange_outflow,
        )

        # On-chain / miner variables evolve much more slowly than price, but
        # they should not look like straight lines.  SOPR mean-reverts around
        # 1 with realized-price sensitivity and low-frequency innovations.
        slow_scale = math.sqrt(max(day_fraction, 1e-6))
        sopr_alpha = 1.0 - math.exp(-max(day_fraction, 0.0) / 3.0)
        sopr_target = 1.0 + 0.075 * math.tanh(log_return * 120.0) + 0.012 * math.tanh(previous.mpi)
        sopr_noise = rng.gauss(0.0, 0.010 * slow_scale)
        state.sopr = _clamp(previous.sopr + sopr_alpha * (sopr_target - previous.sopr) + sopr_noise, 0.78, 1.22)

        mpi_alpha = 1.0 - math.exp(-max(day_fraction, 0.0) / 2.5)
        mpi_target = 2.0 * math.tanh(-log_return * 95.0) + 0.55 * math.tanh(state.liquidity_stress * 2.0 - 0.5)
        state.mpi = _clamp(
            previous.mpi + mpi_alpha * (mpi_target - previous.mpi) + rng.gauss(0.0, 0.32 * slow_scale),
            -4.5,
            5.5,
        )

        transfer_base = 1_850.0 * day_fraction
        transfer_activity = (1.0 + 0.28 * max(state.mpi, 0.0) + 0.18 * state.liquidity_stress)
        transfer_noise = math.exp(rng.gauss(-0.5 * (0.30 * slow_scale) ** 2, 0.30 * slow_scale))
        transfer_burst = 1.0
        if rng.random() < (1.0 - math.exp(-0.055 * max(day_fraction, 0.0))):
            transfer_burst += abs(rng.gauss(1.25, 0.45))
        state.miner_transfer_volume = max(0.01, transfer_base * transfer_activity * transfer_noise * transfer_burst)

        # Miner reserve is an inventory stock: issuance/retention can offset
        # distributions, and stressed days can create discrete selling bursts.
        reserve_anchor = 1_820_000.0
        miner_gap = (reserve_anchor - previous.miner_balance) / reserve_anchor
        retained_issuance = 285.0 * day_fraction * (1.0 + 0.20 * max(-state.mpi, 0.0))
        distributed = 0.10 * state.miner_transfer_volume * (1.0 + 0.16 * max(state.mpi, 0.0))
        inventory_noise = rng.gauss(0.0, 105.0 * slow_scale)
        mean_reversion = 0.020 * miner_gap * reserve_anchor * day_fraction
        state.miner_balance = max(1.0, previous.miner_balance + retained_issuance - distributed + inventory_noise + mean_reversion)

        # Hashrate is noisy and persistent.  Short outages/shocks are rare but
        # visible; the long-run process mean-reverts gently toward the network
        # baseline rather than tracing a perfectly smooth exponential line.
        hash_anchor = 8.5e20
        hash_gap = math.log(hash_anchor / max(previous.hash_rate, 1.0))
        hash_log_change = 0.00035 * day_fraction + 0.025 * hash_gap * day_fraction + rng.gauss(0.0, 0.0065 * slow_scale)
        if rng.random() < (1.0 - math.exp(-0.035 * max(day_fraction, 0.0))):
            hash_log_change -= abs(rng.gauss(0.018, 0.010))
        state.hash_rate = max(1.0, previous.hash_rate * math.exp(_clamp(hash_log_change, -0.08, 0.06)))

        # Bitcoin difficulty is intentionally stepwise: it adjusts only when a
        # synthetic ~14-day epoch boundary is crossed, following the prevailing
        # hashrate level with an 8% safety cap per adjustment.
        difficulty_epoch_seconds = 14 * 86_400
        previous_epoch = previous.timestamp // difficulty_epoch_seconds
        current_epoch = state.timestamp // difficulty_epoch_seconds
        if current_epoch != previous_epoch:
            target_difficulty = 1.29e14 * (state.hash_rate / hash_anchor)
            raw_factor = target_difficulty / max(previous.difficulty, 1.0)
            adjustment = _clamp(raw_factor * math.exp(rng.gauss(0.0, 0.004)), 0.92, 1.08)
            state.difficulty = max(1.0, previous.difficulty * adjustment)
        else:
            state.difficulty = previous.difficulty

        state.miner_revenue = max(
            1.0,
            45.0 * state.btc_price * max(day_fraction, 1.0 / 8640.0) * (1.0 + rng.gauss(0.0, 0.08 * slow_scale)),
        )
        return state

    def advance(self) -> MarketState:
        """Advance the private live market state. No provider JSON is emitted."""
        with self.lock:
            self.state = self._step_state(
                self.state,
                dt_seconds=self.update_seconds,
                rng=self.random,
                allow_regime_transition=True,
            )
            self.history.append(deepcopy(self.state))
            self._history_cache.clear()
            return deepcopy(self.state)

    def _historical_seed(self, interval_seconds: int, end: int, samples_per_period: int) -> int:
        # Stable integer mixing (do not use Python's salted hash()).
        return (
            (self.seed * 1_000_003)
            ^ (int(interval_seconds) * 97_409)
            ^ ((int(end) // max(int(interval_seconds), 1)) * 65_537)
            ^ (int(samples_per_period) * 8_191)
        ) & 0x7FFFFFFF

    def _reanchor_history(self, states: list[MarketState], anchor: MarketState) -> None:
        if not states:
            return
        final_price = max(states[-1].btc_price, 1e-9)
        log_price_correction = math.log(max(anchor.btc_price, 1e-9) / final_price)
        final_oi = max(states[-1].open_interest, 1e-9)
        log_oi_correction = math.log(max(anchor.open_interest, 1e-9) / final_oi)
        cvd_correction = anchor.cumulative_volume_delta_usd - states[-1].cumulative_volume_delta_usd
        fcvd_correction = anchor.futures_cumulative_volume_delta_usd - states[-1].futures_cumulative_volume_delta_usd
        # Exchange reserve is a stock variable.  Historical simulation starts
        # from the live anchor and then integrates synthetic daily net flows;
        # without re-anchoring, a 500-day request can drift hundreds of
        # thousands of BTC away from the current reserve.  A constant level
        # correction preserves every historical reserve delta exactly, so
        # reserve[t]-reserve[t-1] remains coherent with inflow-outflow while
        # the last point matches the live market state.
        reserve_correction = anchor.exchange_reserve - states[-1].exchange_reserve
        miner_balance_correction = anchor.miner_balance - states[-1].miner_balance
        sopr_correction = anchor.sopr - states[-1].sopr
        mpi_correction = anchor.mpi - states[-1].mpi
        hash_rate_scale = anchor.hash_rate / max(states[-1].hash_rate, 1e-9)
        difficulty_scale = anchor.difficulty / max(states[-1].difficulty, 1e-9)

        count = len(states)
        for index, state in enumerate(states):
            progress = (index + 1) / count
            old_price = max(state.btc_price, 1e-9)
            state.btc_price *= math.exp(log_price_correction * progress)
            price_scale = state.btc_price / old_price
            state.open_interest *= math.exp(log_oi_correction * progress)
            state.cumulative_volume_delta_usd += cvd_correction * progress
            state.futures_cumulative_volume_delta_usd += fcvd_correction * progress
            state.exchange_reserve = max(1.0, state.exchange_reserve + reserve_correction)
            state.miner_balance = max(1.0, state.miner_balance + miner_balance_correction)
            state.sopr = _clamp(state.sopr + sopr_correction, 0.78, 1.22)
            state.mpi = _clamp(state.mpi + mpi_correction, -4.5, 5.5)
            state.hash_rate = max(1.0, state.hash_rate * hash_rate_scale)
            state.difficulty = max(1.0, state.difficulty * difficulty_scale)
            state.taker_buy_volume_usd *= price_scale
            state.taker_sell_volume_usd *= price_scale
            state.futures_taker_buy_volume_usd *= price_scale
            state.futures_taker_sell_volume_usd *= price_scale
            self._option_layer(state)

    def get_synthetic_history(
        self,
        *,
        interval_seconds: int,
        periods: int,
        end_timestamp: int | None = None,
        samples_per_period: int = 4,
        anchor_state: MarketState | None = None,
    ) -> list[MarketState]:
        """Generate a deterministic coherent stochastic history on demand.

        Every endpoint still returns the fixed 500 provider records, but those
        records now come from a shared quantitative path rather than sinusoidal
        fixtures.  Repeated calls at the same live anchor/interval reuse a cache
        so Prices/CVD/OI/volatility observe the same underlying market path.
        """
        if interval_seconds <= 0 or periods <= 0 or samples_per_period <= 0:
            raise ValueError("historical interval, periods and samples must be positive")

        # A provider request must be built against one immutable engine
        # snapshot.  History generation can be expensive enough for the
        # background engine clock to advance while it is running; fetching a
        # second anchor here used to mix two live states in one response.  That
        # race rewrote closed candles and could leave the latest OI unchanged
        # after /admin/advance.  Callers that already captured the live state
        # pass it through explicitly.
        anchor = deepcopy(anchor_state) if anchor_state is not None else self.get_state()
        end = min(int(end_timestamp or anchor.timestamp), int(anchor.timestamp))
        key = (anchor.timestamp, int(interval_seconds), int(periods), int(end), int(samples_per_period))
        with self.lock:
            cached = self._history_cache.get(key)
            if cached is not None:
                return [deepcopy(item) for item in cached]

        last_bucket = (end // interval_seconds) * interval_seconds
        first_bucket = last_bucket - (int(periods) - 1) * interval_seconds
        sample_times: list[int] = []
        for bucket in range(first_bucket, last_bucket + 1, interval_seconds):
            bucket_samples = []
            for sample_index in range(samples_per_period):
                fraction = (sample_index + 1) / (samples_per_period + 1)
                timestamp = bucket + max(1, int(interval_seconds * fraction))
                if timestamp <= end:
                    bucket_samples.append(timestamp)
            if not bucket_samples and bucket <= end:
                bucket_samples.append(end)
            sample_times.extend(bucket_samples)

        if not sample_times:
            return []

        rng = random.Random(self._historical_seed(interval_seconds, end, samples_per_period))
        state = deepcopy(anchor)
        state.timestamp = sample_times[0] - max(1, int(interval_seconds / max(samples_per_period + 1, 2)))

        # Start the path away from the live anchor.  The progressive re-anchor
        # later makes the last observation equal to live state without erasing
        # the stochastic shape in between.
        total_years = max((end - state.timestamp) / SECONDS_PER_YEAR, 1e-6)
        start_dispersion = min(0.35, anchor.annualized_volatility * math.sqrt(total_years) * 0.55)
        state.btc_price = max(1.0, anchor.btc_price * math.exp(rng.gauss(0.0, start_dispersion)))
        state.open_interest = max(1.0e8, anchor.open_interest * math.exp(rng.gauss(0.0, min(0.12, start_dispersion * 0.4))))
        state.exchange_reserve = max(1.0, anchor.exchange_reserve * math.exp(rng.gauss(0.0, min(0.025, 0.012 + 0.006 * math.sqrt(total_years)))))
        state.miner_balance = max(1.0, anchor.miner_balance * math.exp(rng.gauss(0.0, min(0.010, 0.003 + 0.003 * math.sqrt(total_years)))))
        state.sopr = _clamp(anchor.sopr + rng.gauss(0.0, min(0.045, 0.018 + 0.010 * math.sqrt(total_years))), 0.78, 1.22)
        state.mpi = _clamp(anchor.mpi + rng.gauss(0.0, min(1.2, 0.45 + 0.25 * math.sqrt(total_years))), -4.5, 5.5)
        state.hash_rate = max(1.0, anchor.hash_rate * math.exp(rng.gauss(0.0, min(0.08, 0.025 + 0.025 * math.sqrt(total_years)))))
        state.difficulty = max(1.0, anchor.difficulty * math.exp(rng.gauss(0.0, min(0.05, 0.015 + 0.015 * math.sqrt(total_years)))))
        state.cumulative_volume_delta_usd = anchor.cumulative_volume_delta_usd + rng.gauss(0.0, 2.0e9)
        state.futures_cumulative_volume_delta_usd = anchor.futures_cumulative_volume_delta_usd + rng.gauss(0.0, 4.0e9)
        state.regime = rng.choice(tuple(sorted(SUPPORTED_REGIMES - {"liquidation_event"})))
        self._option_layer(state)

        output: list[MarketState] = []
        previous_timestamp = state.timestamp
        for timestamp in sample_times:
            dt = max(1, timestamp - previous_timestamp)
            state = self._step_state(state, dt_seconds=dt, rng=rng, allow_regime_transition=True)
            state.timestamp = timestamp
            output.append(deepcopy(state))
            previous_timestamp = timestamp

        self._reanchor_history(output, anchor)
        with self.lock:
            # Keep cache bounded because the live anchor invalidates it every 10s.
            if len(self._history_cache) >= 24:
                self._history_cache.clear()
            self._history_cache[key] = [deepcopy(item) for item in output]
        return output

    def get_state(self) -> MarketState:
        with self.lock:
            return deepcopy(self.state)

    def get_history(self, limit: int = 100) -> list[MarketState]:
        with self.lock:
            return [deepcopy(item) for item in list(self.history)[-limit:]]

    def set_regime(self, regime: str) -> None:
        if regime not in SUPPORTED_REGIMES:
            raise ValueError(f"Unsupported regime: {regime}")
        with self.lock:
            self.state.regime = regime
            self._history_cache.clear()

    def trigger_liquidation_event(self) -> MarketState:
        with self.lock:
            self.state.regime = "liquidation_event"
            self._history_cache.clear()
        return self.advance()

    def reset(self) -> MarketState:
        with self.lock:
            self.running = True
            self.random = random.Random(self.seed)
            self.state = initial_market_state()
            self.state.regime = SIMULATION_INITIAL_REGIME
            self.history.clear()
            self.history.append(deepcopy(self.state))
            self._history_cache.clear()
            return deepcopy(self.state)

    async def run(self) -> None:
        while self.running:
            await asyncio.sleep(self.update_seconds)
            self.advance()


engine = SimulationEngine(
    update_seconds=SIMULATION_UPDATE_SECONDS,
    history_size=SIMULATION_HISTORY_SIZE,
    seed=SIMULATION_SEED,
)
