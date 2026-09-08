from app.core.regimes import REGIME_MODEL_PARAMETERS
from app.core.simulation_engine import (
    FUTURES_FLOW_PRICE_IMPACT,
    LIQUIDATION_SHOCK_MEAN,
    LIQUIDATION_SHOCK_SIGMA,
    MAX_ABS_LOG_RETURN,
    SPOT_FLOW_PRICE_IMPACT,
)


def test_hf2_price_volatility_and_jump_amplitude_profile_is_exact():
    expected = {
        "bullish": (0.60, 0.0017, 0.0069),
        "bearish": (0.72, -0.0023, 0.0092),
        "lateral": (0.44, 0.0, 0.0046),
        "high_volatility": (1.10, 0.0, 0.0161),
        "reversal": (0.88, 0.0, 0.0115),
        "liquidation_event": (1.55, -0.0092, 0.0207),
    }
    actual = {
        name: (
            params["volatility"],
            params["jump_mean"],
            params["jump_sigma"],
        )
        for name, params in REGIME_MODEL_PARAMETERS.items()
    }
    assert actual == expected


def test_hf2_keeps_jump_frequency_and_regime_duration_unchanged():
    expected = {
        "bullish": (80.0, 8 * 3600.0),
        "bearish": (110.0, 7 * 3600.0),
        "lateral": (45.0, 5 * 3600.0),
        "high_volatility": (420.0, 2 * 3600.0),
        "reversal": (220.0, 75 * 60.0),
        "liquidation_event": (1200.0, 25 * 60.0),
    }
    actual = {
        name: (params["jump_intensity"], params["mean_duration_seconds"])
        for name, params in REGIME_MODEL_PARAMETERS.items()
    }
    assert actual == expected


def test_hf2_flow_shock_and_safety_limits_are_explicit():
    assert SPOT_FLOW_PRICE_IMPACT == 0.42
    assert FUTURES_FLOW_PRICE_IMPACT == 0.16
    assert LIQUIDATION_SHOCK_MEAN == 0.008
    assert LIQUIDATION_SHOCK_SIGMA == 0.007
    assert MAX_ABS_LOG_RETURN == 0.12
