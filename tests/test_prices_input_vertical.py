from __future__ import annotations

import json

from processing_signals.input.prices_ohlcv.prices_ohlcv_data_raw_extract import (
    BOOTSTRAP_TIMEFRAMES,
    build_prices_fetch_plan,
)
from processing_signals.input.prices_ohlcv.prices_ohlcv_data_raw_preprocessing import run_prices_ohlcv_input
from processing_signals.runtime.emulator import SyntheticProviderRouter


def _fetcher():
    return SyntheticProviderRouter("runtime/contracts/input_raw").for_family("prices_ohlcv")


def test_bootstrap_fetch_plan_is_spot_and_futures_only() -> None:
    plan = build_prices_fetch_plan(mode="bootstrap", bootstrap_limit=500)
    assert len(plan) == 12
    assert {item["market"] for item in plan} == {"spot", "futures"}
    assert {item["timeframe"] for item in plan} == set(BOOTSTRAP_TIMEFRAMES)
    assert all(item["limit"] == 500 for item in plan)


def test_bootstrap_input_keeps_provider_markets_and_declares_general_contract_alias() -> None:
    output = run_prices_ohlcv_input(fetcher=_fetcher(), requested_mode="bootstrap", bootstrap_limit=500)
    assert output["family"] == "prices_ohlcv"
    assert output["stage"] == "input"
    assert tuple(output["markets"]) == ("spot", "futures")
    assert "general" not in output["markets"]
    assert output["context"]["canonical_contract_market"] == "general"
    assert output["context"]["canonical_source_market"] == "spot"
    assert output["confirmations"]["glassnode"]["price_ohlc"]["status"] == "available"
    assert output["provider_features"]["market_cap"]["status"] == "available"
    for market in ("spot", "futures"):
        assert set(output["markets"][market]["timeframes"]) == set(BOOTSTRAP_TIMEFRAMES)
        for timeframe in BOOTSTRAP_TIMEFRAMES:
            payload = output["markets"][market]["timeframes"][timeframe]
            assert len(payload["records"]) == 500
            assert payload["warnings"] == []


def test_spot_is_canonical_price_market_in_context() -> None:
    output = run_prices_ohlcv_input(fetcher=_fetcher(), requested_mode="bootstrap", bootstrap_limit=500)
    assert output["context"]["price_market"] == "spot"
    assert output["context"]["available_markets"] == ["spot", "futures"]
    json.dumps(output, allow_nan=False)
