from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from copy            import deepcopy
from numbers         import Integral
from pathlib         import Path
from typing          import Any

from processing_signals.classification.volatility_market_regimes.volatility_market_regimes_classifier       import classify_volatility_market_regimes
from processing_signals.classification.volatility_market_regimes.volatility_market_regimes_contract_builder import build_volatility_market_regimes_screen
from processing_signals.input.volatility_market_regimes.volatility_market_regimes_data_raw_extract          import extract_volatility_market_regimes_raw
from processing_signals.input.volatility_market_regimes.volatility_market_regimes_data_raw_preprocessing    import preprocess_volatility_market_regimes_input
from processing_signals.processing.volatility_market_regimes.volatility_market_regimes_processor            import process_volatility_market_regimes


VOLATILITY_MARKET_REGIMES_FAMILY = "volatility_market_regimes"
VALID_VERTICAL_MODES             = {"bootstrap", "incremental", "recovery"}
DEFAULT_SELECTED_RANGE           = "7d"
DEFAULT_SCREEN_EXPORT_PATH       = Path("runtime/contracts/hmi_contract/volatility_market_regimes_screen.json")
SCREEN_JSON_INDENT               = 2


class VolatilityMarketRegimesVerticalError(RuntimeError):
    def __init__(self, stage: str, message: str, cause: str | None = None) -> None:
        self.stage   = stage
        self.message = message
        self.cause   = cause
        super().__init__(f"{stage}: {message}" + (f": {cause}" if cause else ""))


def validate_previous_volatility_market_regimes_output(previous: Any) -> None:
    if not isinstance(previous, Mapping):
        raise ValueError("previous_vertical_output:mapping_required")
    for section in ("input", "processing", "classification", "screen"):
        if not isinstance(previous.get(section), Mapping):
            raise ValueError(f"previous_vertical_output.{section}:mapping_required")
    if previous["input"].get("family") != VOLATILITY_MARKET_REGIMES_FAMILY or previous["input"].get("stage") != "input":
        raise ValueError("previous_vertical_output.input:identity_invalid")
    if previous["processing"].get("stage") != "processing" or previous["classification"].get("stage") != "classification":
        raise ValueError("previous_vertical_output:stage_invalid")
    if previous["screen"].get("screen") != VOLATILITY_MARKET_REGIMES_FAMILY:
        raise ValueError("previous_vertical_output.screen:identity_invalid")
    for section in ("input", "processing", "classification"):
        if previous[section].get("family") != VOLATILITY_MARKET_REGIMES_FAMILY or previous[section].get("mode") not in VALID_VERTICAL_MODES:
            raise ValueError(f"previous_vertical_output.{section}:identity_invalid")


def validate_volatility_market_regimes_vertical_request(
    *, mode: str, fetcher: Any, reference_timestamp: Any, runtime_context: Any, selected_range: str = DEFAULT_SELECTED_RANGE,
    previous_vertical_output: Any = None, recovery_requests: Any = None, derive_recovery_from_gaps: bool = False,
) -> None:
    if mode not in VALID_VERTICAL_MODES:
        raise ValueError("mode:invalid")
    if isinstance(reference_timestamp, bool) or not isinstance(reference_timestamp, Integral):
        raise ValueError("reference_timestamp:integer_required")
    if not callable(fetcher):
        raise ValueError("fetcher:callable_required")
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context:mapping_required")
    if mode == "bootstrap":
        if previous_vertical_output is not None or recovery_requests:
            raise ValueError("bootstrap:previous_or_recovery_not_allowed")
    else:
        validate_previous_volatility_market_regimes_output(previous_vertical_output)
    if mode == "incremental" and recovery_requests:
        raise ValueError("incremental:recovery_requests_not_allowed")
    if mode != "recovery" and derive_recovery_from_gaps:
        raise ValueError("derive_recovery_from_gaps:recovery_only")
    if selected_range not in {"7d", "30d", "90d", "360d"}:
        raise ValueError("selected_range:invalid")


def derive_volatility_market_regimes_recovery_requests(previous_input_contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    datasets = (
        ("coinglass", "top_position_ratio", "top_position_long_short_ratio"),
        ("glassnode", "realized_volatility", "realized_volatility"),
        ("glassnode", "dvol", "dvol_ohlc"),
    )
    requests  = []
    providers = previous_input_contract.get("providers", {})
    for provider, dataset, endpoint_id in datasets:
        source = providers.get(provider, {}).get(dataset, {})
        ranges = sorted(source.get("gap_ranges", []), key=lambda item: item["after_timestamp"])
        merged = []
        for gap in ranges:
            start, end = int(gap["after_timestamp"]), int(gap["before_timestamp"])
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        requests.extend({"provider": provider, "endpoint_id": endpoint_id, "start_timestamp": start, "end_timestamp": end} for start, end in merged)
    return requests


def serialize_volatility_market_regimes_screen(screen_contract: Mapping[str, Any]) -> str:
    try:
        return json.dumps(screen_contract, ensure_ascii=False, allow_nan=False, indent=SCREEN_JSON_INDENT, sort_keys=False) + "\n"
    except (TypeError, ValueError, OverflowError) as exc:
        raise VolatilityMarketRegimesVerticalError("serialization", "screen_json_invalid", str(exc)) from exc


def write_volatility_market_regimes_screen_atomic(screen_contract: Mapping[str, Any], export_path: str | Path) -> Path:
    serialized  = serialize_volatility_market_regimes_screen(screen_contract)
    destination = Path(export_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        return destination
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if isinstance(exc, VolatilityMarketRegimesVerticalError):
            raise
        raise VolatilityMarketRegimesVerticalError("export", "atomic_screen_export_failed", str(exc)) from exc


def _check_stage(contract: Any, stage: str, mode: str) -> None:
    if not isinstance(contract, Mapping) or contract.get("family") != VOLATILITY_MARKET_REGIMES_FAMILY:
        raise ValueError("invalid_contract")
    if stage == "screen":
        if contract.get("screen") != VOLATILITY_MARKET_REGIMES_FAMILY or contract.get("schema_version") != "1.2.0-no-regime-timeline":
            raise ValueError("invalid_screen_contract")
    elif contract.get("stage") != stage or contract.get("mode") != mode:
        raise ValueError("invalid_stage_contract")


class VolatilityMarketRegimesVertical:
    def run(
        self, *, mode: str, fetcher: Callable[..., Any], reference_timestamp: int, runtime_context: Mapping[str, Any], selected_range: str = DEFAULT_SELECTED_RANGE,
        previous_vertical_output: Mapping[str, Any] | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
        derive_recovery_from_gaps: bool = False, export_screen: bool = False, export_path: str | Path | None = None,
        execution_clock: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        try:
            validate_volatility_market_regimes_vertical_request(mode=mode, fetcher=fetcher, reference_timestamp=reference_timestamp, runtime_context=runtime_context,
                selected_range=selected_range, previous_vertical_output=previous_vertical_output, recovery_requests=recovery_requests,
                derive_recovery_from_gaps=derive_recovery_from_gaps)
        except (TypeError, ValueError) as exc:
            raise VolatilityMarketRegimesVerticalError("validation", "vertical_request_invalid", str(exc)) from exc
        previous_input     = deepcopy(previous_vertical_output["input"]) if previous_vertical_output is not None else None
        effective_requests = deepcopy(list(recovery_requests)) if recovery_requests else None
        if mode == "recovery" and derive_recovery_from_gaps and not effective_requests:
            effective_requests = derive_volatility_market_regimes_recovery_requests(previous_input)
        if mode == "recovery" and not effective_requests:
            raise VolatilityMarketRegimesVerticalError("validation", "recovery_targets_required")
        try:
            raw_bundle = extract_volatility_market_regimes_raw(fetcher=fetcher, mode=mode, reference_timestamp=int(reference_timestamp),
                recovery_requests=effective_requests, clock=execution_clock)
        except Exception as exc:
            raise VolatilityMarketRegimesVerticalError("raw_extract", "raw_extract_failed", str(exc)) from exc
        try:
            input_contract = preprocess_volatility_market_regimes_input(raw_bundle, existing_contract=previous_input)
            _check_stage(input_contract, "input", mode)
        except Exception as exc:
            raise VolatilityMarketRegimesVerticalError("input_preprocessing", "input_preprocessing_failed", str(exc)) from exc
        try:
            processing_contract = process_volatility_market_regimes(input_contract)
            _check_stage(processing_contract, "processing", mode)
        except Exception as exc:
            raise VolatilityMarketRegimesVerticalError("processing", "processing_failed", str(exc)) from exc
        try:
            classification_contract = classify_volatility_market_regimes(processing_contract)
            _check_stage(classification_contract, "classification", mode)
        except Exception as exc:
            raise VolatilityMarketRegimesVerticalError("classification", "classification_failed", str(exc)) from exc
        try:
            screen_contract = build_volatility_market_regimes_screen(processing_contract, classification_contract,
                runtime_context=runtime_context, selected_range=selected_range)
            _check_stage(screen_contract, "screen", mode)
            if screen_contract.get("quality", {}).get("status") == "invalid":
                raise ValueError("screen_quality_invalid")
        except Exception as exc:
            raise VolatilityMarketRegimesVerticalError("contract_builder", "contract_builder_failed", str(exc)) from exc
        output = {"input": input_contract, "processing": processing_contract, "classification": classification_contract, "screen": screen_contract}
        try:
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VolatilityMarketRegimesVerticalError("serialization", "vertical_output_invalid", str(exc)) from exc
        if export_screen:
            write_volatility_market_regimes_screen_atomic(screen_contract, export_path or DEFAULT_SCREEN_EXPORT_PATH)
        return output


def run_volatility_market_regimes_vertical(**kwargs: Any) -> dict[str, Any]:
    return VolatilityMarketRegimesVertical().run(**kwargs)
