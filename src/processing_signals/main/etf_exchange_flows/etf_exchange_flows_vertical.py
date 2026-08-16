"""ETF Exchange Flows Input -> Processing -> Classification -> screen vertical."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
import time
from typing import Any

from processing_signals.classification.classification_pipeline import run_classification_pipeline
from processing_signals.classification.etf_exchange_flows import build_etf_exchange_flows_contract
from processing_signals.input.input_pipeline import run_input_pipeline
from processing_signals.main.screen_contract_export import export_etf_exchange_flows_screen_json
from processing_signals.processing.processing_pipeline import run_processing_pipeline

DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH = Path("runtime/contracts/hmi_contract/etf_exchange_flows_screen.json")


def run_etf_exchange_flows_vertical(
    *, fetcher: Any, input_arguments: Mapping[str, Any] | None = None,
    processing_arguments: Mapping[str, Any] | None = None,
    classification_arguments: Mapping[str, Any] | None = None,
    contract_arguments: Mapping[str, Any] | None = None,
    previous_state: Mapping[str, Any] | None = None,
    now_timestamp: int | None = None, publish_screen: bool = False,
    output_path: str | Path = DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH,
) -> dict[str, Any]:
    """Run the frozen ETF chain and optionally atomically publish its screen JSON."""
    timestamp = int(time.time()) if now_timestamp is None else now_timestamp
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("now_timestamp must be a positive integer")
    if not isinstance(publish_screen, bool):
        raise ValueError("publish_screen must be a boolean")
    previous = deepcopy(dict(previous_state or {}))
    input_args = deepcopy(dict(input_arguments or {}))
    processing_args = deepcopy(dict(processing_arguments or {}))
    classification_args = deepcopy(dict(classification_arguments or {}))
    contract_args = deepcopy(dict(contract_arguments or {}))
    input_args["fetcher"] = fetcher
    input_args.setdefault("now", timestamp)
    if "existing_contract" not in input_args and isinstance(previous.get("input"), Mapping):
        input_args["existing_contract"] = deepcopy(previous["input"])
    input_contract = run_input_pipeline(enabled_families=("etf_exchange_flows",),
        family_arguments={"etf_exchange_flows": input_args})["etf_exchange_flows"]
    processing_contract = run_processing_pipeline(input_contracts={"etf_exchange_flows": input_contract},
        enabled_families=("etf_exchange_flows",), now_timestamp=timestamp,
        family_arguments={"etf_exchange_flows": processing_args})["etf_exchange_flows"]
    classification_contract = run_classification_pipeline(
        processing_contracts={"etf_exchange_flows": processing_contract},
        enabled_families=("etf_exchange_flows",),
        family_arguments={"etf_exchange_flows": classification_args})["etf_exchange_flows"]
    screen_contract = build_etf_exchange_flows_contract(processing_contract=processing_contract,
        classification_contract=classification_contract, **contract_args)
    output = {"input": input_contract, "processing": processing_contract,
              "classification": classification_contract, "screen": screen_contract}
    if publish_screen:
        export_etf_exchange_flows_screen_json(vertical_output=output, output_path=output_path)
    return output
