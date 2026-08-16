from __future__ import annotations

from typing import Any, Mapping, Sequence

from .prices_ohlcv.prices_ohlcv_classifier import run_prices_ohlcv_classification
from .etf_exchange_flows import run_etf_exchange_flows_classification
from .liquidity_microstructure import classify_liquidity_microstructure
from .long_short_liquidations.long_short_liquidations_classifier import classify_long_short_liquidations
from .on_chain_miners.on_chain_miners_classifier import classify_on_chain_miners
from .open_interest_and_funding.open_interest_and_funding_classifier import classify_open_interest_and_funding
from .volatility_market_regimes.volatility_market_regimes_classifier import classify_volatility_market_regimes
from .cvd_volume_orderflow.cvd_volume_orderflow_classifier import classify_cvd_volume_orderflow


FAMILY_ORDER = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


def _prices(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("prices_ohlcv Classification does not accept family arguments")
    return run_prices_ohlcv_classification(processing_contract)


def _etf(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return run_etf_exchange_flows_classification(processing_contract=processing_contract, **dict(arguments))


def _liquidity(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return classify_liquidity_microstructure(processing_contract, **dict(arguments))


def _liquidations(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return classify_long_short_liquidations(processing_contract, **dict(arguments))


def _on_chain(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("on_chain_miners Classification does not accept family arguments")
    return classify_on_chain_miners(processing_contract)


def _open_interest(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("open_interest_and_funding Classification does not accept family arguments")
    return classify_open_interest_and_funding(processing_contract)


def _volatility(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("volatility_market_regimes Classification does not accept family arguments")
    return classify_volatility_market_regimes(processing_contract)


def _cvd(processing_contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return classify_cvd_volume_orderflow(processing_contract, **dict(arguments))


CLASSIFICATION_FAMILY_HANDLERS = {
    "prices_ohlcv": _prices,
    "cvd_volume_orderflow": _cvd,
    "open_interest_and_funding": _open_interest,
    "etf_exchange_flows": _etf,
    "on_chain_miners": _on_chain,
    "volatility_market_regimes": _volatility,
    "long_short_liquidations": _liquidations,
    "liquidity_microstructure": _liquidity,
}


def run_classification_pipeline(
    *,
    processing_contracts: Mapping[str, Mapping[str, Any]],
    enabled_families: Sequence[str] = ("prices_ohlcv",),
    family_arguments: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    arguments = family_arguments or {}
    outputs: dict[str, Any] = {}
    for family in enabled_families:
        handler = CLASSIFICATION_FAMILY_HANDLERS.get(family)
        if handler is None:
            raise ValueError(f"No Classification handler registered for family: {family}")
        if family not in processing_contracts:
            raise ValueError(f"Processing contract missing for family: {family}")
        outputs[family] = handler(processing_contracts[family], dict(arguments.get(family, {})))
    return outputs
