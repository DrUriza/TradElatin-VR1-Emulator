from __future__ import annotations

from typing import Any, Mapping, Sequence
from copy import deepcopy

from .prices_ohlcv.prices_ohlcv_contract_builder import build_prices_screen_contract
from .etf_exchange_flows.etf_exchange_flows_contract_builder import build_etf_exchange_flows_contract
from .liquidity_microstructure.liquidity_microstructure_contract_builder import build_liquidity_microstructure_screen_contract
from .long_short_liquidations.long_short_liquidations_contract_builder import build_long_short_liquidations_contract
from .on_chain_miners.on_chain_miners_contract_builder import build_on_chain_miners_screen_contract
from .open_interest_and_funding.open_interest_and_funding_contract_builder import build_open_interest_and_funding_contract
from .volatility_market_regimes.volatility_market_regimes_contract_builder import build_volatility_market_regimes_screen
from .cvd_volume_orderflow.cvd_volume_orderflow_contract_builder import build_cvd_volume_orderflow_contract
from .final_screen_projection import project_final_screen_contract


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


def _prices(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_prices_screen_contract(processing, classification, **dict(arguments))


def _etf(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_etf_exchange_flows_contract(
        processing_contract=processing,
        classification_contract=classification,
        **dict(arguments),
    )


def _liquidity(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = dict(arguments)
    selected = str(args.pop("selected_market", "perpetual"))
    built_by_market = {
        market: build_liquidity_microstructure_screen_contract(
            {"processing": processing, "classification": classification}, selected_market=market, **args
        )
        for market in ("spot", "perpetual")
    }
    base = built_by_market[selected if selected in built_by_market else "perpetual"]
    base["_prebuilt_market_views"] = {
        market: {
            "kpis": deepcopy(view.get("kpis", {})), "charts": deepcopy(view.get("charts", {})),
            "tables": deepcopy(view.get("tables", {})), "widgets": deepcopy(view.get("widgets", {})),
            "context": {"market": market},
        } for market, view in built_by_market.items()
    }
    return base


def _liquidations(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_long_short_liquidations_contract(processing, classification, **dict(arguments))


def _on_chain(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("on_chain_miners Contract Builder does not accept family arguments")
    return build_on_chain_miners_screen_contract(processing, classification)


def _open_interest(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_open_interest_and_funding_contract(
        {"processing": processing, "classification": classification},
        **dict(arguments),
    )


def _volatility(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_volatility_market_regimes_screen(processing, classification, **dict(arguments))


def _cvd(processing: Mapping[str, Any], classification: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return build_cvd_volume_orderflow_contract(
        {"processing": processing, "classification": classification},
        **dict(arguments),
    )


CONTRACT_BUILDER_FAMILY_HANDLERS = {
    "prices_ohlcv": _prices,
    "cvd_volume_orderflow": _cvd,
    "open_interest_and_funding": _open_interest,
    "etf_exchange_flows": _etf,
    "on_chain_miners": _on_chain,
    "volatility_market_regimes": _volatility,
    "long_short_liquidations": _liquidations,
    "liquidity_microstructure": _liquidity,
}


def run_contract_builder_pipeline(
    *,
    processing_contracts: Mapping[str, Mapping[str, Any]],
    classification_contracts: Mapping[str, Mapping[str, Any]],
    enabled_families: Sequence[str] = FAMILY_ORDER,
    family_arguments: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one presentation contract per enabled family.

    Runtime/presentation arguments remain explicit because Contract Builder is not
    responsible for inventing timestamps, selection state, cache state, or other
    orchestration metadata.
    """
    arguments = family_arguments or {}
    outputs: dict[str, Any] = {}
    for family in enabled_families:
        handler = CONTRACT_BUILDER_FAMILY_HANDLERS.get(family)
        if handler is None:
            raise ValueError(f"No Contract Builder handler registered for family: {family}")
        if family not in processing_contracts:
            raise ValueError(f"Processing contract missing for family: {family}")
        if family not in classification_contracts:
            raise ValueError(f"Classification contract missing for family: {family}")
        built = handler(
            processing_contracts[family],
            classification_contracts[family],
            dict(arguments.get(family, {})),
        )
        outputs[family] = project_final_screen_contract(family, built, processing_contracts[family])
    return outputs
