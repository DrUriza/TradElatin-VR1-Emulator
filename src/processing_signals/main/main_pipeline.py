from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .prices_ohlcv import run_prices_vertical
from .long_short_liquidations import run_long_short_liquidations_vertical
from .on_chain_miners import run_on_chain_miners_vertical
from .etf_exchange_flows import run_etf_exchange_flows_vertical
from .cvd_volume_orderflow import run_cvd_volume_orderflow_vertical
from .open_interest_and_funding import run_open_interest_and_funding_vertical
from .volatility_market_regimes import run_volatility_market_regimes_vertical
from .liquidity_microstructure import run_liquidity_microstructure_vertical


def _run_cvd_volume_orderflow_registered(
    *, previous_state: Mapping[str, Any] | None = None, **arguments: Any
) -> dict[str, Any]:
    """Adapt the CVD screen vertical to the shared family-pipeline envelope."""
    if previous_state is not None and "existing_input" not in arguments:
        previous_input = previous_state.get("input") if isinstance(previous_state, Mapping) else None
        if isinstance(previous_input, Mapping):
            arguments["existing_input"] = previous_input
    return {"screen": run_cvd_volume_orderflow_vertical(**arguments)}


def _run_open_interest_and_funding_registered(
    *, previous_state: Mapping[str, Any] | None = None, **arguments: Any
) -> dict[str, Any]:
    """Adapt the OI/Funding vertical to the shared family-pipeline envelope."""
    if previous_state is not None and "input_state" not in arguments:
        previous_input = previous_state.get("input") if isinstance(previous_state, Mapping) else None
        if isinstance(previous_input, Mapping):
            arguments["input_state"] = previous_input
    arguments["include_debug_bundle"] = True
    output = run_open_interest_and_funding_vertical(**arguments)
    if isinstance(output, Mapping) and isinstance(output.get("screen"), Mapping):
        return dict(output)
    return {"screen": output}


def _run_volatility_market_regimes_registered(
    *, previous_state: Mapping[str, Any] | None = None, **arguments: Any
) -> dict[str, Any]:
    """Adapt the Volatility vertical previous-state parameter name."""
    if previous_state is not None and "previous_vertical_output" not in arguments:
        arguments["previous_vertical_output"] = previous_state
    return run_volatility_market_regimes_vertical(**arguments)


VERTICAL_FAMILY_HANDLERS = {
    "prices_ohlcv": run_prices_vertical,
    "cvd_volume_orderflow": _run_cvd_volume_orderflow_registered,
    "open_interest_and_funding": _run_open_interest_and_funding_registered,
    "etf_exchange_flows": run_etf_exchange_flows_vertical,
    "on_chain_miners": run_on_chain_miners_vertical,
    "volatility_market_regimes": _run_volatility_market_regimes_registered,
    "long_short_liquidations": run_long_short_liquidations_vertical,
    "liquidity_microstructure": run_liquidity_microstructure_vertical,
}


def run_main_pipeline(
    *,
    enabled_families: Sequence[str] = tuple(VERTICAL_FAMILY_HANDLERS),
    family_arguments: Mapping[str, Mapping[str, Any]] | None = None,
    previous_state: Mapping[str, Mapping[str, Any]] | None = None,
    screens_only: bool = False,
) -> dict[str, Any]:
    """Run explicitly selected family verticals without publishing runtime JSON.

    The canonical full-system entrypoint is ``main.py`` / ``runtime_orchestrator``.
    This function remains the deterministic family-level orchestration surface
    used by vertical tests and targeted development runs.
    """
    arguments = family_arguments or {}
    previous = previous_state or {}
    outputs: dict[str, Any] = {}
    for family in enabled_families:
        handler = VERTICAL_FAMILY_HANDLERS.get(family)
        if handler is None:
            raise ValueError(f"No vertical handler registered for family: {family}")
        family_output = handler(
            previous_state=previous.get(family),
            **dict(arguments.get(family, {})),
        )
        outputs[family] = family_output["screen"] if screens_only else family_output
    return outputs
