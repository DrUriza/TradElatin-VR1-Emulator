from __future__ import annotations

import json

from processing_signals.input.volatility_market_regimes.volatility_market_regimes_data_raw_preprocessing import run_volatility_market_regimes_input
from processing_signals.processing.volatility_market_regimes.volatility_market_regimes_processor import process_volatility_market_regimes
from processing_signals.runtime.emulator import SyntheticProviderRouter

NOW = 1741618800


def _processing():
    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    source = run_volatility_market_regimes_input(
        fetcher=router.for_family("volatility_market_regimes"), reference_timestamp=NOW,
        requested_mode="bootstrap", clock=lambda: NOW + 5,
    )
    return process_volatility_market_regimes(source)


def test_processing_v2_is_realized_volatility_plus_positioning_only() -> None:
    output = _processing()
    assert output["version"] == "0.2.0"
    assert output["quality"]["status"] == "ok"
    assert set(output["features"]) == {"positioning", "realized_volatility", "daily_regime_basis", "technical_analysis"}
    text = json.dumps(output).lower()
    assert "deribit" not in text
    assert "implied_volatility" not in text
    assert "spread_volatility" not in text


def test_daily_regime_basis_has_transversal_statistics_and_positioning_context() -> None:
    basis = _processing()["features"]["daily_regime_basis"]
    assert basis["status"] == "available"
    assert basis["records"]
    current = basis["current"]
    assert current["realized_volatility_percent"] is not None
    assert current["realized_z_score_30d"] is not None
    assert current["realized_percentile_rank_90d"] is not None
    assert "long_short_ratio" in current
