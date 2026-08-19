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


def test_bootstrap_input_keeps_only_real_provider_markets() -> None:
    output = run_prices_ohlcv_input(fetcher=_fetcher(), requested_mode="bootstrap", bootstrap_limit=500)
    assert output["family"] == "prices_ohlcv"
    assert output["stage"] == "input"
    assert tuple(output["markets"]) == ("spot", "futures")
    assert output["context"]["canonical_contract_market"] == "spot"
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


def test_incremental_fetch_plan_refreshes_only_canonical_spot_by_default() -> None:
    plan = build_prices_fetch_plan(mode="incremental")
    assert len(plan) == 2
    assert {item["market"] for item in plan} == {"spot"}
    assert {item["timeframe"] for item in plan} == {"1m", "15m"}


def test_incremental_secondary_refresh_is_explicit() -> None:
    plan = build_prices_fetch_plan(mode="incremental", refresh_secondary=True)
    assert len(plan) == 4
    assert {item["market"] for item in plan} == {"spot", "futures"}
    assert {item["timeframe"] for item in plan} == {"1m", "15m"}


def test_incremental_reuses_secondary_state_without_paid_refresh() -> None:
    base_fetcher = _fetcher()
    bootstrap = run_prices_ohlcv_input(
        fetcher=base_fetcher,
        requested_mode="bootstrap",
        bootstrap_limit=500,
    )
    calls: list[dict] = []

    def counting_fetcher(**request):
        calls.append(dict(request))
        return base_fetcher(**request)

    incremental = run_prices_ohlcv_input(
        fetcher=counting_fetcher,
        existing_contract=bootstrap,
        requested_mode="incremental",
    )
    assert len(calls) == 2
    assert all(call["provider"] == "coinglass" for call in calls)
    assert {call["params"]["interval"] for call in calls} == {"1m", "15m"}
    assert all(call["endpoint_id"] == "spot_ohlcv" for call in calls)
    assert incremental["markets"]["futures"]["timeframes"]["1h"]["records"] == bootstrap["markets"]["futures"]["timeframes"]["1h"]["records"]
    assert incremental["confirmations"]["glassnode"]["price_ohlc"]["records"] == bootstrap["confirmations"]["glassnode"]["price_ohlc"]["records"]
    assert incremental["provider_features"]["market_cap"]["current"] == bootstrap["provider_features"]["market_cap"]["current"]
