"""Black-Scholes quantitative kernel used by the synthetic market engine.

Black-Scholes prices *options*, not BTC spot.  The emulator therefore uses
geometric Brownian motion (the Black-Scholes underlying model) for the base
spot diffusion and this module for a latent 30-day option surface / greeks.
The option state is not a new external endpoint; it feeds implied volatility,
gamma pressure and risk dynamics shared by the existing 33 endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, pi, sqrt

SQRT_2 = sqrt(2.0)
SQRT_2PI = sqrt(2.0 * pi)


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / SQRT_2))


def normal_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / SQRT_2PI


@dataclass(frozen=True)
class BlackScholesSnapshot:
    spot: float
    strike: float
    time_to_expiry_years: float
    risk_free_rate: float
    implied_volatility: float
    call_price: float
    put_price: float
    call_delta: float
    put_delta: float
    gamma: float
    vega: float


def black_scholes_snapshot(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    implied_volatility: float,
) -> BlackScholesSnapshot:
    """Return European call/put values and primary greeks.

    Closed-form solution of the Black-Scholes PDE.  No SciPy dependency is
    required; the normal CDF is evaluated with ``erf``.
    """
    s = max(float(spot), 1e-9)
    k = max(float(strike), 1e-9)
    t = max(float(time_to_expiry_years), 1e-9)
    r = float(risk_free_rate)
    sigma = max(float(implied_volatility), 1e-6)

    root_t = sqrt(t)
    d1 = (log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    discount = exp(-r * t)

    nd1 = normal_cdf(d1)
    nd2 = normal_cdf(d2)
    call = s * nd1 - k * discount * nd2
    put = k * discount * normal_cdf(-d2) - s * normal_cdf(-d1)
    gamma = normal_pdf(d1) / (s * sigma * root_t)
    vega = s * normal_pdf(d1) * root_t

    return BlackScholesSnapshot(
        spot=s,
        strike=k,
        time_to_expiry_years=t,
        risk_free_rate=r,
        implied_volatility=sigma,
        call_price=max(0.0, call),
        put_price=max(0.0, put),
        call_delta=nd1,
        put_delta=nd1 - 1.0,
        gamma=max(0.0, gamma),
        vega=max(0.0, vega),
    )
