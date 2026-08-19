from __future__ import annotations

import json
from datetime import UTC, datetime

from processing_signals.classification.classification_pipeline import run_classification_pipeline
from processing_signals.classification.contract_builder_pipeline import (
    CONTRACT_BUILDER_FAMILY_HANDLERS,
    FAMILY_ORDER,
    run_contract_builder_pipeline,
)
from processing_signals.input.input_pipeline import run_input_pipeline
from processing_signals.processing.processing_pipeline import run_processing_pipeline
from processing_signals.runtime.emulator import SyntheticProviderRouter
from test_eight_family_input_processing_pipeline import FAMILIES, _arguments


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()


def _builder_arguments(processing: dict) -> dict:
    liquidity = processing["liquidity_microstructure"]
    liquidations = processing["long_short_liquidations"]
    return {
        "etf_exchange_flows": {
            "selected_range": "30d",
            "generated_at": _iso(processing["etf_exchange_flows"]["data_as_of"]),
        },
        "liquidity_microstructure": {
            "runtime_context": {
                "data_mode": "synthetic",
                "is_demo": True,
                "generated_at": _iso(liquidity["execution_timestamp"]),
                "updated_at": _iso(liquidity["reference_timestamp"]),
                "connection_status": "not_reported",
                "cache_status": "not_reported",
                "latency_ms": None,
                "refresh_interval_seconds": None,
                "cache_ttl_seconds": None,
            }
        },
        "long_short_liquidations": {
            "context": {
                "symbol": "BTCUSDT",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "market": "futures",
                "price_precision": 2,
            },
            "runtime_context": {
                "generated_at": liquidations["reference_timestamp"] + 10,
                "updated_at": liquidations["reference_timestamp"],
                "data_mode": "synthetic",
                "is_demo": True,
                "cache_status": "disabled",
            },
        },
        "open_interest_and_funding": {"selected_timeframe": "1h"},
        "volatility_market_regimes": {
            "runtime_context": {
                "data_mode": "synthetic",
                "is_demo": True,
                "generated_at": "2027-01-15T08:00:00Z",
                "updated_at": "2027-01-15T08:01:00+00:00",
            },
            "selected_range": "7d",
        },
        "cvd_volume_orderflow": {"selected_market": "spot", "selected_timeframe": "15m"},
    }


def _contract_family(contract: dict) -> str | None:
    if isinstance(contract.get("family"), str):
        return contract["family"]
    screen = contract.get("screen")
    if isinstance(screen, dict):
        return screen.get("family")
    return None


def _quality_status(contract: dict) -> str | None:
    quality = contract.get("quality")
    return quality.get("status") if isinstance(quality, dict) else None


def test_all_eight_families_run_raw_through_contract_builder() -> None:
    assert FAMILY_ORDER == FAMILIES
    assert tuple(CONTRACT_BUILDER_FAMILY_HANDLERS) == FAMILIES

    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    inputs = run_input_pipeline(enabled_families=FAMILIES, family_arguments=_arguments(router))
    processing = run_processing_pipeline(
        input_contracts=inputs,
        enabled_families=FAMILIES,
        now_timestamp=1786150805,
    )
    classification = run_classification_pipeline(
        processing_contracts=processing,
        enabled_families=FAMILIES,
    )
    contracts = run_contract_builder_pipeline(
        processing_contracts=processing,
        classification_contracts=classification,
        enabled_families=FAMILIES,
        family_arguments=_builder_arguments(processing),
    )

    for family in FAMILIES:
        assert _contract_family(contracts[family]) == family
        assert _quality_status(contracts[family]) in {"ok", "available", "partial"}
        json.dumps(contracts[family], ensure_ascii=False, allow_nan=False)

    # Fully supplied families must be presentation-complete.
    for family in (
        "prices_ohlcv",
        "etf_exchange_flows",
        "liquidity_microstructure",
        "on_chain_miners",
        "open_interest_and_funding",
        "volatility_market_regimes",
        "cvd_volume_orderflow",
    ):
        assert _quality_status(contracts[family]) == "ok"

    # Liquidations truthfully remains partial because no current-price context is
    # supplied upstream; Contract Builder must not invent one.
    liquidations = contracts["long_short_liquidations"]
    assert _quality_status(liquidations) == "partial"
    assert "current_price" in liquidations["quality"]["unavailable_view_models"]

    prices_text = json.dumps(contracts["prices_ohlcv"], ensure_ascii=False).lower()
    assert "general" not in prices_text
    assert contracts["prices_ohlcv"]["selectors"]["market"]["options"] == ["spot", "futures"]
    assert "spot_futures" in contracts["prices_ohlcv"]["comparison"]

    volatility = contracts["volatility_market_regimes"]
    assert volatility["schema_version"] == "2.0.0-native-volatility-screen-b-demo"
    assert set(volatility["charts"]) == {"realized_volatility", "implied_volatility", "implied_vs_realized", "term_structure"}
    assert "technical_analysis" not in volatility
    assert volatility["volatility_analysis"]["recalculate_in_hmi"] is False

    liquidity = contracts["liquidity_microstructure"]
    assert liquidity["quality"]["status"] == "ok"
    assert liquidity["charts"]["market_history"]["status"] == "unavailable"

    cvd = contracts["cvd_volume_orderflow"]
    assert cvd["quality"]["status"] == "ok"
    assert cvd["availability"]["optional"]["kpis.price_vs_vwap"]["status"] == "unavailable"
