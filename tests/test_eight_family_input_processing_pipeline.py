from __future__ import annotations

import json

from processing_signals.input.input_pipeline import INPUT_FAMILY_HANDLERS, run_input_pipeline
from processing_signals.processing.processing_pipeline import PROCESSING_FAMILY_HANDLERS, run_processing_pipeline
from processing_signals.runtime.emulator import SyntheticProviderRouter


FAMILIES = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


def _arguments(router: SyntheticProviderRouter):
    pairs = {"Binance": "BTCUSDT", "OKX": "BTCUSDT", "Bybit": "BTCUSDT", "Hyperliquid": "BTCUSDT"}
    return {
        "prices_ohlcv": {"fetcher": router.for_family("prices_ohlcv"), "requested_mode": "bootstrap", "bootstrap_limit": 500},
        "etf_exchange_flows": {"fetcher": router.for_family("etf_exchange_flows"), "requested_mode": "bootstrap", "include_secondary": True,
                               "data_mode": "synthetic", "is_demo": True, "exchange_scope": "all_exchange", "symbol": "BTC", "now": 1786147200},
        "liquidity_microstructure": {"fetcher": router.for_family("liquidity_microstructure"), "requested_mode": "bootstrap",
                                     "reference_timestamp": 1786150800, "execution_timestamp": 1786150805, "data_mode": "synthetic",
                                     "is_demo": True, "history_limit": 100},
        "long_short_liquidations": {"fetcher": router.for_family("long_short_liquidations"), "requested_mode": "bootstrap",
                                    "reference_timestamp": 1740000000, "clock": lambda: 1740000005, "exchange_pairs": pairs, "history_hours": 72},
        "on_chain_miners": {"fetcher": router.for_family("on_chain_miners"), "requested_mode": "bootstrap", "reference_timestamp": 1786060800,
                            "execution_timestamp": 1786060805, "data_mode": "synthetic", "is_demo": True},
        "open_interest_and_funding": {"fetcher": router.for_family("open_interest_and_funding"), "requested_mode": "bootstrap",
                                      "reference_timestamp": 1786150800, "execution_timestamp": 1786150805, "data_mode": "synthetic", "is_demo": True},
        "volatility_market_regimes": {"fetcher": router.for_family("volatility_market_regimes"), "requested_mode": "bootstrap",
                                      "reference_timestamp": 1741618800, "clock": lambda: 1741618805},
        "cvd_volume_orderflow": {"fetcher": router.for_family("cvd_volume_orderflow"), "requested_mode": "bootstrap",
                                 "reference_timestamp": 1786136400, "clock": lambda: 1786136405},
    }


def test_all_eight_families_are_registered_and_run_raw_to_processing() -> None:
    assert tuple(INPUT_FAMILY_HANDLERS) == FAMILIES
    assert tuple(PROCESSING_FAMILY_HANDLERS) == FAMILIES
    router = SyntheticProviderRouter("runtime/contracts/input_raw")
    inputs = run_input_pipeline(enabled_families=FAMILIES, family_arguments=_arguments(router))
    processing = run_processing_pipeline(input_contracts=inputs, enabled_families=FAMILIES, now_timestamp=1786150805)

    for family in FAMILIES:
        assert inputs[family]["stage"] == "input"
        assert processing[family]["stage"] == "processing"
        assert processing[family]["quality"]["status"] in {"ok", "available"}
        json.dumps(processing[family], allow_nan=False)

    assert tuple(processing["prices_ohlcv"]["markets"]) == ("spot", "futures")
    assert "general" not in json.dumps(processing["prices_ohlcv"]).lower()
    comparison = processing["prices_ohlcv"]["features"]["spot_futures_comparison"]["by_timeframe"]["1h"]["series"]
    if comparison:
        assert {"basis_usd", "basis_percent"}.issubset(comparison[-1])

    volatility_text = json.dumps(processing["volatility_market_regimes"]).lower()
    assert "deribit" not in volatility_text
    assert "implied_volatility" not in volatility_text
    assert set(inputs["volatility_market_regimes"]["providers"]) == {"coinglass", "glassnode"}

    liquidity_market_history = inputs["liquidity_microstructure"]["providers"]["coinglass"]["market_history"]
    assert liquidity_market_history["status"] == "unavailable"
    assert processing["liquidity_microstructure"]["quality"]["status"] == "ok"

    oi_quality = processing["open_interest_and_funding"]["quality"]
    assert "oi_change_24h.1m" in oi_quality["optional_calculations"]
    assert processing["cvd_volume_orderflow"]["quality"]["core_status"] == "available"
