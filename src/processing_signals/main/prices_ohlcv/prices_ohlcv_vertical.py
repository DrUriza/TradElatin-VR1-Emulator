from __future__ import annotations

from copy     import deepcopy
from datetime import UTC, datetime
from typing   import Any, Mapping

from processing_signals.classification.classification_pipeline                    import run_classification_pipeline
from processing_signals.classification.prices_ohlcv.prices_ohlcv_contract_builder import build_prices_screen_contract, build_prices_selected_view
from processing_signals.input.input_pipeline                                      import run_input_pipeline
from processing_signals.input.prices_ohlcv.prices_ohlcv_data_raw_extract          import PricesFetcher
from processing_signals.processing.processing_pipeline                            import run_processing_pipeline


def run_prices_vertical(*, fetcher: PricesFetcher, input_arguments: Mapping[str, Any] | None = None,
                        previous_state: Mapping[str, Any] | None = None, now_timestamp: int | None = None,
                        runtime_metadata: Mapping[str, Any] | None = None,
                        cvd_processing_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    arguments = {"fetcher": fetcher, **dict(input_arguments or {})}
    if previous_state and "existing_contract" not in arguments:
        arguments["existing_contract"] = previous_state.get("input")
    input_outputs      = run_input_pipeline(family_arguments={"prices_ohlcv": arguments})
    processing_outputs = run_processing_pipeline(input_contracts=input_outputs,
                                                     existing_processing={"prices_ohlcv": (previous_state or {}).get("processing")},
                                                     now_timestamp=now_timestamp)
    classification_outputs = run_classification_pipeline(processing_contracts=processing_outputs)
    input_output           = input_outputs["prices_ohlcv"]
    processing_output      = processing_outputs["prices_ohlcv"]
    classification_output  = classification_outputs["prices_ohlcv"]
    updated_at             = datetime.fromtimestamp(now_timestamp, tz=UTC).isoformat() if now_timestamp is not None else datetime.now(tz=UTC).isoformat()
    input_output["updated_at"]          = updated_at
    processing_output["updated_at"]     = updated_at
    classification_output["updated_at"] = updated_at
    spot_metadata = input_output.get("markets", {}).get("spot", {})
    metadata      = {"symbol": spot_metadata.get("symbol"), "exchange": spot_metadata.get("exchange"),
                     "provider": spot_metadata.get("provider"), "quote_asset": "USDT", "data_mode": "live", "is_demo": False,
                     "reference_timestamp": now_timestamp,
                     **dict(runtime_metadata or {})}
    processing_output["metadata"]     = metadata
    classification_output["metadata"] = deepcopy(metadata)
    screen_contract = build_prices_screen_contract(
        processing_output, classification_output, cvd_processing_context=cvd_processing_context,
    )
    return {"input": input_output, "processing": processing_output, "classification": classification_output, "screen": screen_contract}


def build_prices_view(*, vertical_output: Mapping[str, Any], market: str, timeframe: str) -> dict[str, Any]:
    """Resolve a lightweight selector view without rerunning any pipeline stage."""
    processing_output     = vertical_output.get("processing")
    classification_output = vertical_output.get("classification")
    if not isinstance(processing_output, Mapping) or not isinstance(classification_output, Mapping):
        raise ValueError("Prices vertical output must contain processing and classification state")
    return build_prices_selected_view(processing_output, classification_output, market=market, timeframe=timeframe)
