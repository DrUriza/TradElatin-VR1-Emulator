"""Open Interest and Funding Input -> Processing -> Classification -> screen."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
from typing import Any

from processing_signals.classification.open_interest_and_funding.open_interest_and_funding_classifier import (
    classify_open_interest_and_funding,
)
from processing_signals.classification.open_interest_and_funding.open_interest_and_funding_contract_builder import (
    build_open_interest_and_funding_contract,
)
from processing_signals.input.open_interest_and_funding.open_interest_and_funding_data_raw_extract import (
    OpenInterestAndFundingFetcher,
)
from processing_signals.input.open_interest_and_funding.open_interest_and_funding_data_raw_preprocessing import (
    run_open_interest_and_funding_input,
)
from processing_signals.processing.open_interest_and_funding.open_interest_and_funding_processor import (
    process_open_interest_and_funding,
)


FAMILY = "open_interest_and_funding"
VERSION = "0.1"
MODES = {"bootstrap", "incremental", "recovery"}
TIMEFRAMES = {"5m", "15m", "1h", "4h", "1d"}
SCREEN_ROOT = (
    "family", "screen", "schema_version", "context", "badges", "kpis", "widgets", "selectors",
    "charts", "tables", "events", "quality", "screen_layout", "history_contract", "technical_analysis",
)
VISUAL_CONTEXT_FIELDS = (
    "asset", "exchange_scope", "primary_provider", "confirmation_providers", "data_mode", "is_demo",
    "reference_timestamp", "generated_at",
)


def _input_error(path: str, cause: Exception | None = None) -> ValueError:
    error = ValueError(f"vertical_input_invalid:{path}")
    if cause is not None:
        error.__cause__ = cause
    return error


def _json_copy(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _input_error(path)
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _input_error(path)
            result[key] = _json_copy(item, f"{path}.{key}")
        return result
    if isinstance(value, list):
        return [_json_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise _input_error(path)


def _strict_json(value: Any, stage: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"vertical_output_invalid:{stage}") from exc


def _validate_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _input_error("root")
    if value.get("family") != FAMILY:
        raise _input_error("family")
    if value.get("stage") != "input":
        raise _input_error("stage")
    if value.get("mode") not in MODES:
        raise _input_error("mode")
    if not isinstance(value.get("context"), Mapping):
        raise _input_error("context")
    return _json_copy(value, "input_contract")


def _validate_stage(value: Any, *, stage: str, previous: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"vertical_output_invalid:{stage}")
    if value.get("stage") != stage:
        raise ValueError(f"vertical_stage_mismatch:{stage}")
    if value.get("family") != FAMILY or value.get("version") != VERSION:
        raise ValueError(f"vertical_output_invalid:{stage}")
    if value.get("mode") != previous.get("mode"):
        raise ValueError(f"vertical_mode_mismatch:{stage}")
    if not isinstance(value.get("context"), Mapping) or value.get("context") != previous.get("context"):
        raise ValueError(f"vertical_context_mismatch:{stage}")
    result = _json_copy(value, stage)
    _strict_json(result, stage)
    return result


def _validate_screen(value: Any, classification: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("vertical_output_invalid:screen")
    if (value.get("family") != FAMILY or value.get("screen") != FAMILY
            or value.get("schema_version") != "1.12.0-oi-native-screen-b-demo" or tuple(value) != SCREEN_ROOT):
        raise ValueError("vertical_output_invalid:screen")
    classification_context = classification.get("context")
    if not isinstance(classification_context, Mapping):
        raise ValueError("vertical_context_mismatch:screen")
    if (not isinstance(value.get("context"), Mapping)
            or any(value["context"].get(field) != classification_context.get(field) for field in VISUAL_CONTEXT_FIELDS)
            or value["context"].get("data_as_of") != classification_context.get("reference_timestamp")):
        raise ValueError("vertical_context_mismatch:screen")
    result = _json_copy(value, "screen")
    _strict_json(result, "screen")
    return result


def build_open_interest_and_funding_screen(
    input_contract: Mapping[str, Any],
    *,
    selected_timeframe: str = "1h",
    include_debug_bundle: bool = False,
) -> dict[str, Any]:
    """Run the three pure downstream stages and return one screen contract."""
    if type(selected_timeframe) is not str or selected_timeframe not in TIMEFRAMES:
        raise _input_error("selected_timeframe")
    if type(include_debug_bundle) is not bool:
        raise _input_error("include_debug_bundle")
    input_copy = _validate_input(input_contract)
    try:
        processing_raw = process_open_interest_and_funding(deepcopy(input_copy))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("vertical_output_invalid:processing") from exc
    processing = _validate_stage(processing_raw, stage="processing", previous=input_copy)
    try:
        classification_raw = classify_open_interest_and_funding(deepcopy(processing))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("vertical_output_invalid:classification") from exc
    classification = _validate_stage(classification_raw, stage="classification", previous=processing)
    bundle = {"processing": deepcopy(processing), "classification": deepcopy(classification)}
    try:
        screen_raw = build_open_interest_and_funding_contract(
            bundle, selected_timeframe=selected_timeframe,
        )
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        if str(exc) == "contract_builder_selected_timeframe_invalid":
            raise _input_error("selected_timeframe", exc) from exc
        raise ValueError("vertical_output_invalid:screen") from exc
    screen = _validate_screen(screen_raw, classification)
    if not include_debug_bundle:
        return deepcopy(screen)
    debug = {"input": deepcopy(input_copy), "processing": deepcopy(processing),
             "classification": deepcopy(classification), "screen": deepcopy(screen)}
    _strict_json(debug, "debug")
    return debug


def run_open_interest_and_funding_vertical(
    *,
    mode: str,
    fetcher: OpenInterestAndFundingFetcher,
    reference_timestamp: int,
    execution_timestamp: int,
    selected_timeframe: str = "1h",
    input_state: Mapping[str, Any] | None = None,
    recovery_requests: Sequence[Mapping[str, Any]] | None = None,
    include_snapshots: bool = True,
    include_confirmations: bool = True,
    data_mode: str = "live",
    is_demo: bool = False,
    include_debug_bundle: bool = False,
    refresh_secondary: bool = False,
) -> dict[str, Any]:
    """Execute injected Input once, then delegate to the pure vertical."""
    if mode not in MODES:
        raise _input_error("mode")
    for name, value in (("reference_timestamp", reference_timestamp),
                        ("execution_timestamp", execution_timestamp)):
        if type(value) is not int or value < 0:
            raise _input_error(name)
    for name, value in (("include_snapshots", include_snapshots),
                        ("include_confirmations", include_confirmations),
                        ("is_demo", is_demo), ("include_debug_bundle", include_debug_bundle),
                        ("refresh_secondary", refresh_secondary)):
        if type(value) is not bool:
            raise _input_error(name)
    if data_mode not in {"live", "synthetic"}:
        raise _input_error("data_mode")
    if (data_mode == "synthetic") is not is_demo:
        raise _input_error("is_demo")
    if mode == "bootstrap":
        if input_state is not None:
            raise _input_error("input_state")
        if recovery_requests is not None:
            raise _input_error("recovery_requests")
    elif mode == "incremental":
        if not isinstance(input_state, Mapping):
            raise _input_error("input_state")
        if recovery_requests is not None:
            raise _input_error("recovery_requests")
    else:
        if not isinstance(input_state, Mapping):
            raise _input_error("input_state")
        if (not isinstance(recovery_requests, Sequence) or isinstance(recovery_requests, (str, bytes))
                or not recovery_requests or any(not isinstance(item, Mapping) for item in recovery_requests)):
            raise _input_error("recovery_requests")
    state_copy = _json_copy(input_state, "input_state") if input_state is not None else None
    requests_copy = _json_copy(list(recovery_requests), "recovery_requests") if recovery_requests is not None else None
    try:
        input_contract = run_open_interest_and_funding_input(
            fetcher=fetcher, reference_timestamp=reference_timestamp, requested_mode=mode,
            recovery_requests=requests_copy, existing_state=state_copy,
            include_snapshots=include_snapshots, include_confirmations=include_confirmations,
            data_mode=data_mode, is_demo=is_demo, execution_timestamp=execution_timestamp,
            refresh_secondary=refresh_secondary,
        )
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise _input_error("runtime", exc) from exc
    return build_open_interest_and_funding_screen(
        input_contract, selected_timeframe=selected_timeframe,
        include_debug_bundle=include_debug_bundle,
    )
