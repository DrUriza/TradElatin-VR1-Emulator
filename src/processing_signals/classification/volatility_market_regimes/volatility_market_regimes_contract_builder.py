"""Pure screen-contract builder for the realized-volatility market-regime family.

The builder is intentionally presentation-only: it copies Processing numerics and
Classification semantics, applies visual windowing, formats display fields, and
never recalculates indicators or market states.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import json
import math
from numbers import Real
from typing import Any

from .volatility_market_regimes_sp_v1_2_adapter import (
    SP_SCHEMA_VERSION, align_volatility_market_regimes_to_sp_v1_2,
)

FAMILY = "volatility_market_regimes"
PROCESSING_VERSION = "0.2.0"
CLASSIFICATION_VERSION = "0.2.0"
SCREEN_SCHEMA_VERSION = SP_SCHEMA_VERSION
DISPLAY_RANGE_OPTIONS = ("7d", "30d", "90d", "360d")
DEFAULT_DISPLAY_RANGE = "30d"
_RANGE_SECONDS = {"7d": 604_800, "30d": 2_592_000, "90d": 7_776_000, "360d": 31_104_000}
_VALID_MODES = {"bootstrap", "incremental", "recovery"}
_VALID_STATUS = {"available", "partial", "unavailable", "invalid"}
_REGIME_LABELS = {"low_vol": "Low Vol", "normal": "Normal", "high_vol": "High Vol"}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return 0.0 if number == 0 else number if math.isfinite(number) else None


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


def _json_copy(value: Any, path: str = "root") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}:finite_number_required")
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path}:string_keys_required")
        return {key: _json_copy(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path}:json_value_required")


def validate_runtime_context(runtime_context: Any) -> None:
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context:mapping_required")
    data_mode = runtime_context.get("data_mode")
    is_demo = runtime_context.get("is_demo")
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
    for field in ("reference_timestamp", "input_execution_timestamp", "asset", "symbol", "exchange", "base_interval"):
        if processing["context"].get(field) != classification["context"].get(field):
            raise ValueError(f"builder_contract_mismatch:{field}")
    for key in ("positioning", "realized_volatility", "daily_regime_basis"):
        if key not in processing.get("features", {}):
            raise ValueError(f"processing.features.{key}:required")
    for key in ("daily_regimes", "positioning"):
        if key not in classification.get("classifications", {}):
            raise ValueError(f"classification.classifications.{key}:required")
    _json_copy(processing, "processing")
    _json_copy(classification, "classification")
    _json_copy(runtime_context, "runtime_context")


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
        "tables": {},
        "widgets": {},
        "events": {"by_id": {}, "regime_transition_ids": []},
        "technical_analysis": {},
        "quality": {
            "status": "invalid",
            "contract_complete": False,
            "data_complete": False,
            "availability": {},
            "missing_fields": [],
            "warnings": [],
            "errors": [error],
        },
    }


def build_volatility_market_regimes_badges(runtime_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"badge_id": "demo", "text": "DEMO", "status": "active"}] if runtime_context.get("is_demo") else []


def build_volatility_market_regimes_selectors(selected_range: str) -> dict[str, Any]:
    return {
        "display_range": {
            "selector_id": "volatility_display_range",
            "selected": selected_range,
            "default": DEFAULT_DISPLAY_RANGE,
            "options": list(DISPLAY_RANGE_OPTIONS),
        }
    }


def _window(anchor: int, selected_range: str) -> tuple[int, int]:
    return anchor - _RANGE_SECONDS[selected_range], anchor


def _filter_records(records: Sequence[Mapping[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    output = []
    for record in records:
        timestamp = record.get("timestamp")
        if type(timestamp) is int and start <= timestamp <= end:
            output.append(deepcopy(dict(record)))
    return output


def _metric(metric_id: str, label: str, value: Any, *, unit: str, status: str, reason: str | None = None,
            display_value: str | None = None, classification: Any = None) -> dict[str, Any]:
    usable = status in {"available", "partial"} and value is not None
    return {
        "metric_id": metric_id,
        "label": label,
        "value": deepcopy(value) if usable else None,
        "display_value": display_value if usable and display_value is not None else (str(value) if usable else "--"),
        "unit": unit,
        "status": status if status in _VALID_STATUS else "invalid",
        "reason": None if usable else (reason or "source_not_available"),
        "classification": deepcopy(classification),
    }


def build_volatility_market_regimes_kpis(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    daily = classification["classifications"]["daily_regimes"]
    regime = daily.get("current") or {}
    positioning = classification["classifications"]["positioning"]
    position = positioning.get("current") or {}
    realized_feature = processing["features"]["realized_volatility"]
    realized = realized_feature.get("current") or ((realized_feature.get("records") or [{}])[-1])

    regime_value = regime.get("regime")
    confidence = _finite(regime.get("confidence_score"))
    realized_value = _finite(realized.get("realized_volatility_percent"))
    ratio = _finite(position.get("long_short_ratio"))
    persistence = regime.get("persistence_days") if type(regime.get("persistence_days")) is int else None

    items = [
        _metric("current_regime", "CURRENT REGIME", regime_value, unit="semantic_state", status=daily.get("status", "unavailable"),
                reason=daily.get("reason"), display_value=_REGIME_LABELS.get(regime_value, str(regime_value)), classification=regime_value),
        _metric("confidence", "REGIME CONFIDENCE", confidence, unit="decimal", status="available" if confidence is not None else "unavailable",
                display_value=f"{confidence * 100:.0f}%" if confidence is not None else None, classification=regime.get("confidence_state")),
        _metric("realized_volatility", "REALIZED VOLATILITY", realized_value, unit="percent",
                status=realized_feature.get("status", "unavailable"), reason=realized_feature.get("reason"),
                display_value=f"{realized_value:.2f}%" if realized_value is not None else None),
        _metric("positioning_ratio", "LONG / SHORT RATIO", ratio, unit="ratio", status=positioning.get("status", "unavailable"),
                reason=positioning.get("reason"), display_value=f"{ratio:.3f}" if ratio is not None else None,
                classification=position.get("positioning_state")),
        _metric("persistence", "REGIME PERSISTENCE", persistence, unit="days", status="available" if persistence is not None else "unavailable",
                display_value=(f"{persistence} day" if persistence == 1 else f"{persistence} days") if persistence is not None else None),
    ]
    return {"items": items}


def build_realized_volatility_chart(feature: Mapping[str, Any], start: int, end: int,
                                    technical: Mapping[str, Any] | None = None) -> dict[str, Any]:
    records = _filter_records(feature.get("records", []), start, end)
    return {
        "chart_id": "realized_volatility", "title": "Realized Volatility",
        "chart_type": "line",
        "status": feature.get("status", "unavailable"),
        "reason": feature.get("reason"),
        "unit": "percent",
        "series": ["realized_volatility_percent"],
        "records": records,
        "current": deepcopy(feature.get("current")),
        "records_available": len(feature.get("records", [])),
        "records_returned": len(records),
        "history_truncated": len(records) < len(feature.get("records", [])),
        "source": deepcopy(feature.get("source", {})),
        "candles": deepcopy((technical or {}).get("candles", [])),
        "technical_analysis_allowed": True,
    }


def build_positioning_ratio_chart(feature: Mapping[str, Any], classification: Mapping[str, Any], start: int | None = None,
                                  end: int | None = None) -> dict[str, Any]:
    numeric = {row.get("timestamp"): row for row in feature.get("records", []) if type(row.get("timestamp")) is int}
    semantic = {row.get("timestamp"): row for row in classification.get("records", []) if type(row.get("timestamp")) is int}
    timestamps = sorted(numeric)
    if start is not None and end is not None:
        timestamps = [timestamp for timestamp in timestamps if start <= timestamp <= end]
    records = []
    missing_semantics = 0
    for timestamp in timestamps:
        raw = numeric[timestamp]
        atom = semantic.get(timestamp)
        if atom is None:
            missing_semantics += 1
        records.append({
            "timestamp": timestamp,
            "long_percent": deepcopy(raw.get("long_percent")),
            "short_percent": deepcopy(raw.get("short_percent")),
            "long_short_ratio": deepcopy(raw.get("long_short_ratio")),
            "net_long_percentage_points": deepcopy(raw.get("net_long_percentage_points")),
            "positioning_state": deepcopy(atom.get("positioning_state")) if atom else None,
            "crowding_state": deepcopy(atom.get("crowding_state")) if atom else None,
            "color_token": "positioning_long" if (atom or {}).get("positioning_state") == "long_bias" else
                "positioning_short" if (atom or {}).get("positioning_state") == "short_bias" else "positioning_balanced",
        })
    source_status = feature.get("status", "unavailable")
    status = "partial" if source_status == "available" and missing_semantics else source_status
    return {
        "chart_id": "long_short_positioning_ratio",
        "title": "Long / Short Positioning Ratio",
        "chart_type": "line",
        "status": status,
        "reason": "classification_alignment_incomplete" if status == "partial" else feature.get("reason"),
        "unit": "ratio",
        "series": ["long_short_ratio", "long_percent", "short_percent"],
        "records": records,
        "current": deepcopy(classification.get("current")),
        "reference_lines": [{"value": 1.0, "label": "Balanced"}],
        "selector_behavior": "timestamp_window_filter", "warnings": [],
        "records_available": len(feature.get("records", [])),
        "records_returned": len(records),
        "history_truncated": len(records) < len(feature.get("records", [])),
        "first_available_timestamp": feature.get("first_available_timestamp"),
        "last_available_timestamp": feature.get("last_available_timestamp"),
        "first_returned_timestamp": records[0]["timestamp"] if records else None,
        "last_returned_timestamp": records[-1]["timestamp"] if records else None,
        "source": deepcopy(feature.get("source", {})),
    }


def build_visible_regime_events(source: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
    ids = source.get("regime_transition_ids", [])
    has_technical = "technical_cross_ids" in source
    technical_ids = source.get("technical_cross_ids", [])
    by_id = source.get("by_id", {})
    if (not isinstance(ids, list) or not isinstance(technical_ids, list) or len(ids) != len(set(ids))
            or len(technical_ids) != len(set(technical_ids)) or not isinstance(by_id, Mapping)):
        raise ValueError("events:invalid_registry")
    visible_ids = []
    visible = {}
    visible_technical_ids = []
    for event_id in [*ids, *technical_ids]:
        event = by_id.get(event_id)
        if not isinstance(event, Mapping) or event.get("event_id") != event_id:
            raise ValueError("events:invalid_reference")
        timestamp = event.get("timestamp")
        if type(timestamp) is int and start <= timestamp <= end:
            visible_ids.append(event_id)
            visible[event_id] = deepcopy(dict(event))
            if event_id in technical_ids:
                visible_technical_ids.append(event_id)
    output = {"by_id": visible, "regime_transition_ids": [item for item in visible_ids if item in ids]}
    if has_technical:
        output.update(technical_cross_ids=visible_technical_ids,
            technical_cross_policy={"implementation_owner": "Classification", "recalculate_in_hmi": False})
    return output


def build_regime_timeline_chart(classification: Mapping[str, Any], events: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
    records = _filter_records(classification.get("records", []), start, end)
    event_by_timestamp: dict[int, list[str]] = {}
    for event_id in events.get("regime_transition_ids", []):
        event = events["by_id"][event_id]
        event_by_timestamp.setdefault(event["timestamp"], []).append(event_id)
    for record in records:
        record["event_ids"] = list(event_by_timestamp.get(record["timestamp"], []))
    return {
        "chart_id": "regime_timeline",
        "chart_type": "categorical_timeline",
        "status": classification.get("status", "unavailable"),
        "reason": classification.get("reason"),
        "records": records,
        "current": deepcopy(classification.get("current")),
        "records_available": len(classification.get("records", [])),
        "records_returned": len(records),
        "history_truncated": len(records) < len(classification.get("records", [])),
    }


def build_regime_distribution_chart(summaries: Mapping[str, Any], selected_range: str) -> dict[str, Any]:
    full_history = deepcopy(summaries.get("full_history", {}))
    trailing_30d = deepcopy(summaries.get("trailing_30d", {}))
    selected_basis = "trailing_30d" if selected_range == "30d" else "full_history"
    selected = trailing_30d if selected_basis == "trailing_30d" else full_history
    counts, shares = selected.get("counts", {}), selected.get("shares", {})
    labels = {"low_vol": "Low Vol", "normal": "Normal", "high_vol": "High Vol"}
    items = [{"regime": state, "label": labels[state], "count": counts.get(state), "share": shares.get(state),
        "display_share": "--" if shares.get(state) is None else f"{shares[state] * 100:.1f}%",
        "color_token": f"regime_{state}"} for state in ("low_vol", "normal", "high_vol")]
    return {
        "chart_id": "regime_distribution",
        "title": "Regime Distribution", "chart_type": "donut",
        "status": selected.get("status", "unavailable"),
        "reason": selected.get("reason"),
        "basis": selected.get("basis"), "window": selected.get("window"),
        "classified_days": selected.get("classified_days"), "items": items,
        "selected_basis": selected_basis, "selected": selected,
        "full_history": full_history, "trailing_30d": trailing_30d,
        "selector_behavior": "fixed_full_history_summary" if selected_basis == "full_history" else "fixed_30d_summary",
    }


def build_market_regime_table(statistics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    order = ("low_vol", "normal", "high_vol")
    by_state = {row.get("regime"): row for row in statistics if isinstance(row, Mapping)}
    labels = {"low_vol": "Low Vol", "normal": "Normal", "high_vol": "High Vol"}
    rows = []
    for state in order:
        if state not in by_state:
            continue
        row = deepcopy(dict(by_state[state]))
        row.update(row_id=f"regime:{state}", label=labels[state], color_token=f"regime_{state}",
            display_share="--" if row.get("empirical_share") is None else f"{row['empirical_share'] * 100:.1f}%")
        rows.append(row)
    return {
        "table_id": "market_regime_table",
        "title": "Market Regime Table", "reason": None,
        "status": "available" if len(rows) == len(order) else "partial",
        "share_basis": "empirical_classified_day_share",
        "columns": ["regime", "classified_days", "empirical_share", "episode_count", "average_episode_days", "maximum_episode_days", "current_episode_days"],
        "rows": rows,
    }


def build_technical_analysis(feature: Mapping[str, Any], events: Mapping[str, Any]) -> dict[str, Any]:
    packages = feature.get("indicators", {}) if isinstance(feature.get("indicators"), Mapping) else {}
    indicator_ids = ("macd", "rsi", "tsi", "stochastic", "williams_r", "cci", "adx", "atr",
        "wasserstein_distance", "bollinger_band_width")
    indicators = {}
    for indicator_id in indicator_ids:
        source = packages.get(indicator_id, {}) if isinstance(packages.get(indicator_id), Mapping) else {}
        timestamps = deepcopy(source.get("timestamps", []))
        indicators[indicator_id] = {"status": "available" if timestamps else "unavailable",
            "unit": "percent" if indicator_id in {"rsi", "stochastic", "williams_r"} else "value",
            "parameters": deepcopy(source.get("parameters", {})), "timestamps": timestamps,
            "series": deepcopy(source.get("series", {})), "current": deepcopy(source.get("current", {})),
            "thresholds": deepcopy(source.get("thresholds", [])), "recalculate_in_hmi": False,
            "summary": {"indicator_id": indicator_id, "status": "available" if timestamps else "unavailable"},
            "calculation_metadata": deepcopy(source.get("calculation", {})),
            "warmup_records": source.get("warmup_records"), "calculation_history_records": len(timestamps)}
        current_values = [value for value in indicators[indicator_id]["current"].values() if value is not None]
        current_value = current_values[-1] if current_values else None
        indicators[indicator_id]["summary"].update(label=indicator_id.upper(), section="technical_analysis",
            value=current_value, display_value="--" if current_value is None else f"{current_value:.4f}",
            signal="neutral", signal_color="neutral", strength=0, secondary={})
    indicators["rsi"].update(thresholds=[{"role": "oversold", "value": 30.0}, {"role": "overbought", "value": 70.0}],
        scale={"min": 0.0, "max": 100.0, "unit": "percent"}, threshold_basis="oscillator_domain")
    indicators["stochastic"].update(thresholds=[{"role": "oversold", "value": 20.0}, {"role": "overbought", "value": 80.0}],
        scale={"min": 0.0, "max": 100.0, "unit": "percent"}, threshold_basis="oscillator_domain",
        cross_gate={"bullish": "k_crosses_above_d_at_or_below_20", "bearish": "k_crosses_below_d_at_or_above_80"})
    indicators["tsi"].update(thresholds=[{"role": "oversold", "value": -25.0}, {"role": "neutral", "value": 0.0},
        {"role": "overbought", "value": 25.0}], scale={"min": -100.0, "max": 100.0, "unit": "index"},
        threshold_basis="oscillator_domain")
    indicators["williams_r"]["thresholds"] = [{"role": "overbought", "value": -20.0}, {"role": "oversold", "value": -80.0}]
    moving = packages.get("moving_averages", {})
    bollinger = packages.get("bollinger_bands", {})
    regression = feature.get("regression_channel", {}) if isinstance(feature.get("regression_channel"), Mapping) else {}
    return {"analysis_id": "realized_volatility_technical_analysis", "source_chart_id": "realized_volatility",
        "source_path": "charts.realized_volatility.candles", "source_market": "realized_volatility", "source_timeframe": "1d",
        "recalculate_in_hmi": False, "selector_contract": {
            "trend": ["ema_9", "ema_21", "ema_50", "sma_20", "sma_50", "sma_100", "sma_200", "wma_20", "wma_50"],
            "bands": ["bollinger_bands"], "derived_analysis": ["adx", "bollinger_band_width"],
            "momentum": ["macd", "rsi", "tsi", "stochastic", "williams_r", "cci"],
            "volatility": ["atr", "wasserstein_distance"], "excluded": ["volume", "mfi"]},
        "timestamps": [row["timestamp"] for row in feature.get("candles", [])],
        "overlays": {"moving_averages": {"series": deepcopy(moving.get("series", {})), "recalculate_in_hmi": False},
            "bollinger_bands": {"series": deepcopy(bollinger.get("series", {})), "recalculate_in_hmi": False},
            "regression_channel": {"series": deepcopy(regression.get("series", {})),
                "parameters": deepcopy(regression.get("parameters", {})), "recalculate_in_hmi": False}},
        "indicators": indicators,
        "events": [deepcopy(events["by_id"][event_id]) for event_id in events.get("technical_cross_ids", [])],
        "oscillator_display_contract": {"basis": "fixed_indicator_domain", "recalculate_in_hmi": False,
            "rsi": {"min": 0.0, "max": 100.0}, "stochastic": {"min": 0.0, "max": 100.0},
            "tsi": {"min": -100.0, "max": 100.0}},
        "history": {"calculation_records": feature.get("calculation_history_records", 0),
            "minimum_warmup_records": feature.get("minimum_warmup_records", 200),
            "all_visible_moving_averages_warm": feature.get("calculation_history_records", 0) >= feature.get("minimum_warmup_records", 200),
            "technical_indicators_precomputed": True, "hmi_recalculation": False}}


def build_source_status_widget(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    features = processing.get("features", {})
    rows = [
        {
            "provider_id": "coinglass",
            "role": "positioning",
            "status": features.get("positioning", {}).get("status", "unavailable"),
            "source": deepcopy(features.get("positioning", {}).get("source", {})),
        },
        {
            "provider_id": "glassnode",
            "role": "realized_volatility",
            "status": features.get("realized_volatility", {}).get("status", "unavailable"),
            "source": deepcopy(features.get("realized_volatility", {}).get("source", {})),
        },
        {
            "provider_id": "glassnode_dvol",
            "role": "implied_volatility",
            "status": features.get("dvol", {}).get("status", "unavailable"),
            "source": deepcopy(features.get("dvol", {}).get("source", {})),
        },
        {
            "provider_id": "internal",
            "role": "regime_classification",
            "status": classification.get("classifications", {}).get("daily_regimes", {}).get("status", "unavailable"),
            "source": {"basis": "processing.daily_regime_basis"},
        },
    ]
    return {"widget_id": "source_status", "status": "available", "items": rows}


def _quality(processing: Mapping[str, Any], classification: Mapping[str, Any], parts: Mapping[str, Any]) -> dict[str, Any]:
    required = [
        *parts["kpis"]["items"],
        parts["charts"]["realized_volatility"],
        parts["charts"]["positioning_ratio"],
        parts["charts"]["regime_timeline"],
        parts["tables"]["market_regime_table"],
    ]
    statuses = [item.get("status", "invalid") for item in required]
    errors = [*processing.get("quality", {}).get("errors", []), *classification.get("quality", {}).get("errors", [])]
    warnings = [*processing.get("quality", {}).get("warnings", []), *classification.get("quality", {}).get("warnings", [])]
    if errors or "invalid" in statuses:
        status = "invalid"
    elif any(value != "available" for value in statuses) or warnings:
        status = "partial"
    else:
        status = "ok"
    availability = {
        "kpis_available": sum(item.get("status") == "available" for item in parts["kpis"]["items"]),
        "kpis_total": len(parts["kpis"]["items"]),
        "charts_available": sum(item.get("status") == "available" for item in parts["charts"].values()),
        "charts_total": len(parts["charts"]),
        "tables_available": sum(item.get("status") == "available" for item in parts["tables"].values()),
        "tables_total": len(parts["tables"]),
    }
    return {
        "status": status,
        "contract_complete": status != "invalid",
        "data_complete": all(value == "available" for value in statuses),
        "availability": availability,
        "missing_fields": [],
        "warnings": [str(value) for value in warnings],
        "errors": [str(value) for value in errors],
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
            validate_volatility_market_regimes_builder_inputs(processing_contract, classification_contract, runtime_context, selected_range)
            anchor = int(processing_contract["context"]["reference_timestamp"])
            start, end = _window(anchor, selected_range)
            events = build_visible_regime_events(classification_contract.get("interpreted_events", {}), start, end)
            kpis = build_volatility_market_regimes_kpis(processing_contract, classification_contract)
            charts = {
                "realized_volatility": build_realized_volatility_chart(processing_contract["features"]["realized_volatility"], start, end,
                    processing_contract["features"].get("technical_analysis")),
                "positioning_ratio": build_positioning_ratio_chart(
                    processing_contract["features"]["positioning"], classification_contract["classifications"]["positioning"], start, end
                ),
                "regime_timeline": build_regime_timeline_chart(classification_contract["classifications"]["daily_regimes"], events, start, end),
                "regime_distribution": build_regime_distribution_chart(classification_contract["summaries"]["regime_distribution"], selected_range),
            }
            tables = {"market_regime_table": build_market_regime_table(classification_contract["summaries"]["regime_statistics"])}
            widgets = {"source_status": build_source_status_widget(processing_contract, classification_contract)}
            parts = {"kpis": kpis, "charts": charts, "tables": tables, "widgets": widgets}
            context = {
                "asset": processing_contract["context"]["asset"],
                "symbol": processing_contract["context"]["symbol"],
                "exchange": processing_contract["context"]["exchange"],
                "base_interval": processing_contract["context"]["base_interval"],
                "data_mode": runtime_context["data_mode"],
                "is_demo": runtime_context["is_demo"],
                "generated_at": runtime_context["generated_at"],
                "updated_at": runtime_context["updated_at"],
                "data_as_of": anchor,
                "default_display_range": DEFAULT_DISPLAY_RANGE,
                "selected_display_range": selected_range,
                "range_window": {"start_timestamp": start, "end_timestamp": end},
                "history_policy": {"calculation": "upstream_full_history", "presentation": "selected_range_only"},
            }
            screen = {
                "family": FAMILY,
                "screen": FAMILY,
                "schema_version": SCREEN_SCHEMA_VERSION,
                "context": context,
                "badges": build_volatility_market_regimes_badges(runtime_context),
                "selectors": build_volatility_market_regimes_selectors(selected_range),
                **parts,
                "events": events,
                "technical_analysis": build_technical_analysis(processing_contract["features"].get("technical_analysis", {}), events),
                "quality": {},
            }
            screen["quality"] = _quality(processing_contract, classification_contract, parts)
            screen = align_volatility_market_regimes_to_sp_v1_2(
                screen, processing_contract, classification_contract,
                runtime_context=runtime_context, selected_range=selected_range,
            )
            screen = _json_copy(screen, "screen_contract")
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
