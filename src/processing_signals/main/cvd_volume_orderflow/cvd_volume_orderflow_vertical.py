"""CVD Volume Orderflow Input -> Processing -> Classification -> screen."""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from processing_signals.classification.cvd_volume_orderflow.cvd_volume_orderflow_classifier import classify_cvd_volume_orderflow
from processing_signals.classification.cvd_volume_orderflow.cvd_volume_orderflow_contract_builder import build_cvd_volume_orderflow_contract
from processing_signals.input.cvd_volume_orderflow.cvd_volume_orderflow_data_raw_extract import CvdVolumeOrderflowFetcher
from processing_signals.input.cvd_volume_orderflow.cvd_volume_orderflow_data_raw_preprocessing import run_cvd_volume_orderflow_input
from processing_signals.processing.cvd_volume_orderflow.cvd_volume_orderflow_processor import process_cvd_volume_orderflow

FAMILY = "cvd_volume_orderflow"
MODES = {"bootstrap", "incremental", "recovery"}
MARKETS = ("spot", "futures")
CLASSIFICATION_MARKETS = ("spot", "futures")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
SCREEN_ROOT = ("schema", "screen", "stage", "mode", "context", "badges", "selectors", "operational_status",
    "kpis", "charts", "tables", "drilldowns", "events", "availability", "quality", "technical_analysis",
    "history_contract")


def _error(stage: str, cause: Exception | None = None) -> ValueError:
    error = ValueError(f"cvd_vertical_invalid:{stage}")
    if cause is not None:
        error.__cause__ = cause
    return error


def _json_copy(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _error(path)
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error(path)
        return {key: _json_copy(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise _error(path)


def _strict(value: Any, stage: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _error(stage, exc) from exc


def _validate_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("family") != FAMILY or value.get("stage") != "input" or value.get("mode") not in MODES:
        raise _error("input")
    if not isinstance(value.get("context"), Mapping):
        raise _error("input_context")
    result = _json_copy(value, "input")
    _strict(result, "input")
    return result


def _validate_processing(value: Any, source: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("family") != FAMILY or value.get("stage") != "processing" or value.get("version") != "0.1.0":
        raise _error("processing")
    if value.get("mode") != source.get("mode") or not isinstance(value.get("context"), Mapping):
        raise _error("processing_context")
    current, previous = value["context"], source["context"]
    pairs = (("base_asset", "base_asset"), ("pair_symbol", "pair_symbol"), ("data_mode", "data_mode"),
        ("is_demo", "is_demo"), ("reference_timestamp", "reference_timestamp"),
        ("input_requested_at", "requested_at"), ("input_execution_timestamp", "execution_timestamp"))
    if any(current.get(target) != previous.get(origin) for target, origin in pairs):
        raise _error("processing_context")
    result = _json_copy(value, "processing")
    _strict(result, "processing")
    return result


def _validate_classification(value: Any, processing: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("family") != FAMILY or value.get("stage") != "classification" or value.get("version") != "0.1.0":
        raise _error("classification")
    if value.get("mode") != processing.get("mode") or not isinstance(value.get("context"), Mapping):
        raise _error("classification_context")
    current, previous = value["context"], processing["context"]
    for key in ("base_asset", "pair_symbol", "data_mode", "is_demo", "reference_timestamp", "processing_timestamp"):
        if current.get(key) != previous.get(key):
            raise _error("classification_context")
    if tuple(current.get("markets", ())) != CLASSIFICATION_MARKETS or tuple(current.get("timeframes", ())) != TIMEFRAMES:
        raise _error("classification_context")
    result = _json_copy(value, "classification")
    _strict(result, "classification")
    return result


def _validate_screen(value: Any, classification: Mapping[str, Any], selected_market: str,
                     selected_timeframe: str, display_point_limit: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or tuple(value) != SCREEN_ROOT or value.get("stage") != "screen_contract":
        raise _error("screen")
    schema, identity, context = value.get("schema"), value.get("screen"), value.get("context")
    if (not isinstance(schema, Mapping) or schema.get("id") != "trad_elatin.cvd_volume_orderflow.screen.v1"
            or schema.get("version") != "1.5.0" or not isinstance(identity, Mapping)
            or identity.get("id") != FAMILY or identity.get("family") != FAMILY or not isinstance(context, Mapping)):
        raise _error("screen")
    source = classification["context"]
    expected_core = {"base_asset": source.get("base_asset"), "pair_symbol": source.get("pair_symbol"),
        "markets": list(MARKETS), "timeframes": list(TIMEFRAMES), "data_mode": source.get("data_mode"),
        "is_demo": source.get("is_demo"), "reference_timestamp": source.get("reference_timestamp"),
        "processing_timestamp": source.get("processing_timestamp"),
        "classification_timestamp": source.get("classification_timestamp"),
        "presentation_default_market": selected_market, "presentation_default_timeframe": selected_timeframe,
        "display_point_limit": display_point_limit}
    if value.get("mode") != classification.get("mode") or any(context.get(key) != expected for key, expected in expected_core.items()):
        raise _error("screen_context")
    result = _json_copy(value, "screen")
    _strict(result, "screen")
    return result


def build_cvd_volume_orderflow_screen(input_contract: Mapping[str, Any], *, selected_market: str = "spot",
                                      selected_timeframe: str = "15m", display_point_limit: int = 220,
                                      clock: Callable[[], Any] | None = None,
                                      include_debug_bundle: bool = False) -> dict[str, Any]:
    """Run each frozen downstream layer once and return its visual contract."""
    if selected_market not in MARKETS or selected_timeframe not in TIMEFRAMES:
        raise _error("selection")
    if type(display_point_limit) is not int or not 0 < display_point_limit <= 220 or type(include_debug_bundle) is not bool:
        raise _error("options")
    source = _validate_input(input_contract)
    try:
        processing = _validate_processing(process_cvd_volume_orderflow(copy.deepcopy(source), clock=clock), source)
        classification = _validate_classification(classify_cvd_volume_orderflow(copy.deepcopy(processing), clock=clock), processing)
        bundle = {"processing": copy.deepcopy(processing), "classification": copy.deepcopy(classification)}
        raw_screen = build_cvd_volume_orderflow_contract(bundle, selected_market=selected_market,
            selected_timeframe=selected_timeframe, display_point_limit=display_point_limit)
        screen = _validate_screen(raw_screen, classification, selected_market, selected_timeframe, display_point_limit)
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        if str(exc).startswith("cvd_vertical_invalid:"):
            raise
        raise _error("downstream", exc) from exc
    if not include_debug_bundle:
        return copy.deepcopy(screen)
    debug = {"input": copy.deepcopy(source), "processing": copy.deepcopy(processing),
        "classification": copy.deepcopy(classification), "screen": copy.deepcopy(screen)}
    _strict(debug, "debug")
    return debug


def run_cvd_volume_orderflow_vertical(*, fetcher: CvdVolumeOrderflowFetcher, reference_timestamp: int,
                                      mode: str = "bootstrap", selected_market: str = "spot",
                                      selected_timeframe: str = "15m", display_point_limit: int = 220,
                                      existing_input: Mapping[str, Any] | None = None,
                                      recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                      data_mode: str = "synthetic", is_demo: bool = True,
                                      clock: Callable[[], Any] | None = None, include_debug_bundle: bool = False,
                                      **input_options: Any) -> dict[str, Any]:
    """Execute injected Input and the complete CVD vertical without global registration."""
    if mode not in MODES or type(reference_timestamp) is not int or reference_timestamp < 0:
        raise _error("runtime_options")
    if data_mode not in {"synthetic", "live"} or (data_mode == "synthetic") is not is_demo:
        raise _error("runtime_options")
    if mode == "bootstrap" and (existing_input is not None or recovery_requests is not None):
        raise _error("runtime_state")
    if mode == "incremental" and (not isinstance(existing_input, Mapping) or recovery_requests is not None):
        raise _error("runtime_state")
    if mode == "recovery" and (not isinstance(existing_input, Mapping) or not isinstance(recovery_requests, Sequence)
            or isinstance(recovery_requests, (str, bytes)) or not recovery_requests):
        raise _error("runtime_state")
    try:
        input_contract = run_cvd_volume_orderflow_input(fetcher=fetcher, reference_timestamp=reference_timestamp,
            requested_mode=mode, existing_input=copy.deepcopy(existing_input), recovery_requests=copy.deepcopy(recovery_requests),
            data_mode=data_mode, is_demo=is_demo, clock=clock, **copy.deepcopy(input_options))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise _error("input_runtime", exc) from exc
    return build_cvd_volume_orderflow_screen(input_contract, selected_market=selected_market,
        selected_timeframe=selected_timeframe, display_point_limit=display_point_limit, clock=clock,
        include_debug_bundle=include_debug_bundle)
