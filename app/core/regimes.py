"""Regime parameters for the synthetic quantitative market engine."""
from __future__ import annotations

SUPPORTED_REGIMES = {
    "bullish",
    "bearish",
    "lateral",
    "high_volatility",
    "reversal",
    "liquidation_event",
}

# Annualized parameters.  The engine scales them to each simulated dt.
# drift: annualized log-return drift
# volatility: long-run annualized volatility
# flow_bias: mean spot order-flow imbalance [-1, 1]
# jump_intensity: expected jumps per year (Merton jump-diffusion)
# jump_mean/sigma: log-jump distribution
# mean_duration_seconds: Markov persistence
REGIME_MODEL_PARAMETERS: dict[str, dict[str, float]] = {
    "bullish": {
        "drift": 0.75,
        "volatility": 0.60,
        "flow_bias": 0.18,
        "jump_intensity": 80.0,
        "jump_mean": 0.0017,
        "jump_sigma": 0.0069,
        "mean_duration_seconds": 8 * 3600.0,
    },
    "bearish": {
        "drift": -0.70,
        "volatility": 0.72,
        "flow_bias": -0.20,
        "jump_intensity": 110.0,
        "jump_mean": -0.0023,
        "jump_sigma": 0.0092,
        "mean_duration_seconds": 7 * 3600.0,
    },
    "lateral": {
        "drift": 0.02,
        "volatility": 0.44,
        "flow_bias": 0.00,
        "jump_intensity": 45.0,
        "jump_mean": 0.0,
        "jump_sigma": 0.0046,
        "mean_duration_seconds": 5 * 3600.0,
    },
    "high_volatility": {
        "drift": 0.00,
        "volatility": 1.10,
        "flow_bias": 0.00,
        "jump_intensity": 420.0,
        "jump_mean": 0.0,
        "jump_sigma": 0.0161,
        "mean_duration_seconds": 2 * 3600.0,
    },
    "reversal": {
        "drift": -0.08,
        "volatility": 0.88,
        "flow_bias": -0.05,
        "jump_intensity": 220.0,
        "jump_mean": 0.0,
        "jump_sigma": 0.0115,
        "mean_duration_seconds": 75 * 60.0,
    },
    "liquidation_event": {
        "drift": -0.30,
        "volatility": 1.55,
        "flow_bias": -0.35,
        "jump_intensity": 1200.0,
        "jump_mean": -0.0092,
        "jump_sigma": 0.0207,
        "mean_duration_seconds": 25 * 60.0,
    },
}

# Conditional transition weights.  Persistence itself is controlled by the
# mean duration above, so these rows only determine the destination regime.
REGIME_TRANSITIONS: dict[str, tuple[tuple[str, float], ...]] = {
    "lateral": (("bullish", 0.25), ("bearish", 0.25), ("high_volatility", 0.22), ("reversal", 0.28)),
    "bullish": (("lateral", 0.34), ("high_volatility", 0.22), ("reversal", 0.32), ("bearish", 0.12)),
    "bearish": (("lateral", 0.30), ("high_volatility", 0.26), ("reversal", 0.30), ("bullish", 0.14)),
    "high_volatility": (("lateral", 0.30), ("bullish", 0.18), ("bearish", 0.24), ("reversal", 0.20), ("liquidation_event", 0.08)),
    "reversal": (("lateral", 0.34), ("bullish", 0.26), ("bearish", 0.26), ("high_volatility", 0.14)),
    "liquidation_event": (("high_volatility", 0.48), ("reversal", 0.32), ("lateral", 0.20)),
}

# Backward-compatible tuple map retained for any external local test/import.
REGIME_PARAMETERS: dict[str, tuple[float, float]] = {
    name: (params["drift"], params["volatility"])
    for name, params in REGIME_MODEL_PARAMETERS.items()
}


def is_supported(regime: str) -> bool:
    return regime in SUPPORTED_REGIMES
