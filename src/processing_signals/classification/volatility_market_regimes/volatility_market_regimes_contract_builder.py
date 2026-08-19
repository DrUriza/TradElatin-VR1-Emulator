"""Canonical Volatility screen builder.

Volatility owns volatility only. Long/short positioning is owned by the
Long/Short & Liquidations family and is intentionally absent here.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
import json
import math
from typing import Any

from .volatility_market_regimes_sp_v2_0_adapter import (
    SP_SCHEMA_VERSION,
    align_volatility_market_regimes_to_sp_v2_0,
)

FAMILY = "volatility_market_regimes"
PROCESSING_VERSION = "0.2.0"
CLASSIFICATION_VERSION = "0.2.0"
SCREEN_SCHEMA_VERSION = SP_SCHEMA_VERSION
DISPLAY_RANGE_OPTIONS = ("7d", "30d", "90d", "360d")
DEFAULT_DISPLAY_RANGE = "30d"
_VALID_MODES = {"bootstrap", "incremental", "recovery"}


def _iso(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}:timezone_iso8601_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path}:timezone_iso8601_required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path}:timezone_iso8601_required")
    return value


def _strict_json(value: Any, path: str = "root") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}:finite_number_required")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}:string_keys_required")
            _strict_json(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _strict_json(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path}:json_value_required")


def validate_runtime_context(runtime_context: Any) -> None:
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context:mapping_required")
    data_mode, is_demo = runtime_context.get("data_mode"), runtime_context.get("is_demo")
    if data_mode not in {"synthetic", "live"} or type(is_demo) is not bool:
        raise ValueError("runtime_context:data_mode_or_is_demo_invalid")
    if (data_mode == "synthetic") != is_demo:
        raise ValueError("runtime_context:data_mode_is_demo_mismatch")
    _iso(runtime_context.get("generated_at"), "runtime_context.generated_at")
    _iso(runtime_context.get("updated_at"), "runtime_context.updated_at")


def validate_volatility_market_regimes_builder_inputs(
    processing: Any,
    classification: Any,
    runtime_context: Any,
    selected_range: str = DEFAULT_DISPLAY_RANGE,
) -> None:
    if selected_range not in DISPLAY_RANGE_OPTIONS:
        raise ValueError("selected_range:invalid")
    validate_runtime_context(runtime_context)
    for name, contract, stage, version in (
        ("processing", processing, "processing", PROCESSING_VERSION),
        ("classification", classification, "classification", CLASSIFICATION_VERSION),
    ):
        if not isinstance(contract, Mapping):
            raise ValueError(f"{name}:mapping_required")
        if contract.get("family") != FAMILY or contract.get("stage") != stage or contract.get("version") != version:
            raise ValueError(f"{name}:identity_invalid")
        if contract.get("mode") not in _VALID_MODES or not isinstance(contract.get("context"), Mapping):
            raise ValueError(f"{name}:mode_or_context_invalid")
    if processing.get("mode") != classification.get("mode"):
        raise ValueError("builder_contract_mismatch:mode")
    features = processing.get("features")
    classes = classification.get("classifications")
    if not isinstance(features, Mapping):
        raise ValueError("processing.features:mapping_required")
    if not isinstance(classes, Mapping):
        raise ValueError("classification.classifications:mapping_required")
    for key in ("realized_volatility", "dvol", "volatility_spread", "daily_regime_basis", "volatility_native_analytics"):
        if key not in features:
            raise ValueError(f"processing.features.{key}:required")
    for key in ("daily_regimes", "volatility_context"):
        if key not in classes:
            raise ValueError(f"classification.classifications.{key}:required")
    _strict_json(processing, "processing")
    _strict_json(classification, "classification")
    _strict_json(runtime_context, "runtime_context")


def _invalid_screen(error: str) -> dict[str, Any]:
    return {
        "family": FAMILY,
        "screen": FAMILY,
        "schema_version": SCREEN_SCHEMA_VERSION,
        "context": {},
        "badges": [],
        "selectors": {},
        "kpis": {"items": []},
        "charts": {},
        "volatility_analysis": {"indicator_order": [], "indicators": {}},
        "quality": {"status": "invalid", "errors": [error]},
        "provenance": {},
    }


class VolatilityMarketRegimesContractBuilder:
    def build(
        self,
        processing_contract: Mapping[str, Any],
        classification_contract: Mapping[str, Any],
        *,
        runtime_context: Mapping[str, Any],
        selected_range: str = DEFAULT_DISPLAY_RANGE,
    ) -> dict[str, Any]:
        before = deepcopy((processing_contract, classification_contract, runtime_context))
        try:
            validate_volatility_market_regimes_builder_inputs(
                processing_contract, classification_contract, runtime_context, selected_range
            )
            # SP 2.0 is the sole screen projection. No legacy positioning/TA layer
            # is allowed to mutate the canonical native-volatility contract.
            screen = align_volatility_market_regimes_to_sp_v2_0(
                {}, processing_contract, classification_contract,
                runtime_context=runtime_context, selected_range=selected_range,
            )
            _strict_json(screen, "screen_contract")
            json.dumps(screen, ensure_ascii=False, allow_nan=False, sort_keys=False)
            if (processing_contract, classification_contract, runtime_context) != before:
                raise RuntimeError("Contract Builder mutated upstream state")
            return screen
        except RuntimeError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            return _invalid_screen(str(exc))


def build_volatility_market_regimes_screen(
    processing_contract: Any,
    classification_contract: Any,
    *,
    runtime_context: Any,
    selected_range: str = DEFAULT_DISPLAY_RANGE,
) -> dict[str, Any]:
    return VolatilityMarketRegimesContractBuilder().build(
        processing_contract,
        classification_contract,
        runtime_context=runtime_context,
        selected_range=selected_range,
    )
