from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def build_volatility_market_regimes_features(
    positioning: Mapping[str, Any],
    realized_volatility: Mapping[str, Any],
    dvol: Mapping[str, Any],
    volatility_spread: Mapping[str, Any],
    daily_regime_basis: Mapping[str, Any],
) -> dict[str, Any]:
    """Package already-computed numeric results without presentation semantics."""
    return {
        "positioning": deepcopy(dict(positioning)),
        "realized_volatility": deepcopy(dict(realized_volatility)),
        "dvol": deepcopy(dict(dvol)),
        "volatility_spread": deepcopy(dict(volatility_spread)),
        "daily_regime_basis": deepcopy(dict(daily_regime_basis)),
    }


class VolatilityMarketRegimesFeatureBuilder:
    def build(
        self,
        positioning: Mapping[str, Any],
        realized_volatility: Mapping[str, Any],
        dvol: Mapping[str, Any],
        volatility_spread: Mapping[str, Any],
        daily_regime_basis: Mapping[str, Any],
    ) -> dict[str, Any]:
        return build_volatility_market_regimes_features(
            positioning, realized_volatility, dvol, volatility_spread, daily_regime_basis,
        )
