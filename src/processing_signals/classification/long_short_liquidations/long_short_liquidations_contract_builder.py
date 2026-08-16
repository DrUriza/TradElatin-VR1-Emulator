"""Screen contract builder for long/short liquidations v0.1."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from .long_short_liquidations_sp_v1_2_adapter import align_long_short_liquidations_to_sp_v1_2

VALID_STATUS = {"available", "partial", "unavailable", "invalid"}
INTERVALS = {
    "1m": (None, "1m", None), "5m": (None, "5m", None), "15m": (None, "15m", "classifications.events.15m"),
    "1h": ("1h", "1h", None), "4h": ("4h", "4h", None), "1d": ("24h", "24h", None),
}
DEFAULT_SELECTION = {"interval": "1h", "exchange": "aggregate", "map": "aggregate"}
MAP_OPTIONS = {"aggregate", "hyperliquid", "binance"}
REQUIRED_VIEWS = ["current_price", "total_liquidations_24h", "long_liquidations_24h",
                  "short_liquidations_24h", "pressure_score", "realized_side_24h",
                  "aggregate_liquidation_map", "event_activity_15m", "exchange_concentration"]
OPTIONAL_VIEWS = ["selected_realized_side", "selected_realized_imbalance", "estimated_side",
                  "estimated_imbalance", "map_concentrations", "clusters", "provider_confirmations",
                  "max_pain", "hyperliquid_map", "binance_leverage_map", "event_1h_classification"]
STATUS_RANK = {"available": 0, "partial": 1, "unavailable": 2, "invalid": 3}
CLASS_TOKENS = {
    "low_pressure": "pressure_low", "moderate_pressure": "pressure_moderate",
    "high_pressure": "pressure_high", "extreme_pressure": "pressure_extreme",
    "realized_long_liquidations_dominant": "realized_long_dominant",
    "realized_short_liquidations_dominant": "realized_short_dominant", "realized_balanced": "realized_balanced",
    "estimated_long_exposure_dominant": "estimated_long_dominant",
    "estimated_short_exposure_dominant": "estimated_short_dominant", "estimated_exposure_balanced": "estimated_balanced",
    "subdued_event_activity": "event_subdued", "normal_event_activity": "event_normal",
    "elevated_event_activity": "event_elevated", "high_event_activity": "event_high",
    "extreme_event_activity": "event_extreme", "dispersed": "concentration_dispersed",
    "moderately_concentrated": "concentration_moderate", "concentrated": "concentration_high",
    "highly_concentrated": "concentration_extreme", "provider_aligned": "confirmation_aligned",
    "provider_mixed": "confirmation_mixed", "provider_divergent": "confirmation_divergent",
}
CLASS_LABELS = {
    "realized_long_liquidations_dominant": "Realized Long Liquidations Dominant",
    "realized_short_liquidations_dominant": "Realized Short Liquidations Dominant",
    "realized_balanced": "Realized Balanced", "estimated_long_exposure_dominant": "Estimated Long Exposure Dominant",
    "estimated_short_exposure_dominant": "Estimated Short Exposure Dominant",
    "estimated_exposure_balanced": "Estimated Exposure Balanced",
}


def _error(path: str) -> ValueError:
    return ValueError(f"invalid_contract_input:{path}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _error(path)
    return value


def _json_safe(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error(path)
        for key, item in value.items():
            _json_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _json_safe(item, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise _error(path)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise _error(path)


def _at(root: Mapping[str, Any], path: str, default: Any = ...):
    value: Any = root
    walked = []
    for part in path.split("."):
        walked.append(part)
        if not isinstance(value, Mapping) or part not in value:
            if default is not ...:
                return default
            raise _error(".".join(walked))
        value = value[part]
    return value


def _status(value: Any, path: str) -> str:
    if value not in VALID_STATUS:
        raise _error(path)
    return value


def _number(value: Any, path: str, *, integer: bool = False, positive: bool = False) -> float | int:
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected) or not math.isfinite(value):
        raise _error(path)
    if positive and value <= 0:
        raise _error(path)
    return value


def _combined_status(*statuses: str) -> str:
    return max(statuses, key=STATUS_RANK.get)


def _usable(status: str) -> bool:
    return status in {"available", "partial"}


def _sanitize_optional_payload(status: str, payload: Any, empty_value: Any) -> Any:
    return deepcopy(payload) if _usable(status) else deepcopy(empty_value)


def _sanitize_components(status: str, components: Any) -> dict[str, Any] | list[Any]:
    if not _usable(status):
        return []
    clean = {}
    for name, component in _mapping(components, "pressure.components").items():
        component = _mapping(component, f"pressure.components.{name}")
        component_status = _status(component.get("status"), f"pressure.components.{name}.status")
        clean[name] = _sanitize_optional_payload(component_status, component, {
            "value": None, "status": component_status, "reason": deepcopy(component.get("reason"))})
    return clean


def _sanitize_cluster_source(status: str, source: Mapping[str, Any]) -> dict[str, Any]:
    if not _usable(status):
        return {"estimated_long": [], "estimated_short": []}
    clean = {}
    for side in ("estimated_long", "estimated_short"):
        branch = source.get(side, [])
        if isinstance(branch, Mapping) and branch.get("status") in VALID_STATUS:
            branch_status = _status(branch.get("status"), f"clusters.{side}.status")
            branch = branch.get("items", branch.get("value", []))
            clean[side] = _sanitize_optional_payload(branch_status, branch, [])
        else:
            clean[side] = deepcopy(branch)
    return clean


def _validate_timestamp_anchor(value: Any, *, path: str, generated_at: int) -> tuple[int | None, str | None]:
    if type(value) is not int:
        return None, "invalid_type"
    if value <= 0:
        return None, "non_positive"
    if value > generated_at:
        return None, "future"
    return value, None


def _with_view_id(view_model: Any, *, view_id: str, label: str | None = None) -> dict[str, Any]:
    source = deepcopy(dict(view_model)) if isinstance(view_model, Mapping) else {}
    source["id"] = view_id
    if label is not None:
        source.setdefault("label", label)
    source.setdefault("status", "unavailable")
    source.setdefault("reason", "view_payload_not_available" if not isinstance(view_model, Mapping) else None)
    return source


def _token(status: str, classification: str | None = None) -> str:
    return CLASS_TOKENS.get(classification, f"status_{status}")


def _format_price(value: float, precision: int) -> str:
    return f"{value:,.{precision}f}"


def _format_usd(value: float) -> str:
    magnitude = abs(value)
    divisor, suffix = ((1e9, "B") if magnitude >= 1e9 else (1e6, "M") if magnitude >= 1e6 else
                       (1e3, "K") if magnitude >= 1e3 else (1., ""))
    return f"${value/divisor:,.2f}{suffix}"


def _format_ratio(value: float) -> str:
    return f"{value*100:+.1f}%"


def _format_score(value: float) -> str:
    return f"{value:.1f}"


def _format_bps(value: float) -> str:
    return f"{value:+.1f} bps"


def _format_level(value: float) -> str:
    return f"{value:.4f}" if abs(value) < 1 else f"{value:.2f}"


def _classification_model(atom: Mapping[str, Any]) -> dict[str, Any]:
    atom = _mapping(atom, "classification_atom")
    status = _status(atom.get("status"), "classification_atom.status")
    classification = atom.get("classification") if _usable(status) else None
    return {"classification": deepcopy(classification), "label": CLASS_LABELS.get(classification,
            str(classification).replace("_", " ").title() if classification else "—"),
            "strength": deepcopy(atom.get("strength")) if _usable(status) else None,
            "confidence": deepcopy(atom.get("confidence", 0.)) if _usable(status) else 0., "status": status,
            "reason": deepcopy(atom.get("reason")), "color_token": _token(status, classification),
            "evidence": deepcopy(atom.get("evidence", {})), "provenance": deepcopy(atom.get("provenance", {}))}


def _scalar(identifier: str, label: str, value: Any, *, unit: str | None, status: str, reason: Any,
            formatter: Any, timestamp: Any = None, classification: Mapping[str, Any] | None = None,
            provenance: Any = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status = _status(status, f"{identifier}.status")
    class_model = _classification_model(classification) if classification is not None else None
    usable = _usable(status)
    if usable and value is not None:
        _number(value, f"{identifier}.value")
    raw = deepcopy(value) if usable else None
    display = formatter(value) if usable and value is not None else "—"
    output = {"id": identifier, "label": label, "value": raw, "display_value": display, "unit": unit,
              "status": status, "reason": deepcopy(reason),
              "classification": class_model["classification"] if class_model and usable else None,
              "confidence": class_model["confidence"] if class_model and usable else 0.,
              "color_token": class_model["color_token"] if class_model and usable else _token(status),
              "timestamp": deepcopy(timestamp), "provenance": deepcopy(provenance or {})}
    output.update(deepcopy(dict(extra or {})))
    return output


def _badge(identifier: str, label: str, active: bool, reason: Any, source_path: str) -> dict[str, Any]:
    return {"id": identifier, "label": label, "status": "active" if active else "inactive",
            "reason": deepcopy(reason), "source_path": source_path}


def _validate_contract(contract: Any, stage: str, name: str) -> Mapping[str, Any]:
    contract = _mapping(contract, name)
    if contract.get("family") != "long_short_liquidations":
        raise _error(f"{name}.family")
    if contract.get("stage") != stage:
        raise _error(f"{name}.stage")
    _number(contract.get("reference_timestamp"), f"{name}.reference_timestamp", integer=True, positive=True)
    quality = _mapping(contract.get("quality"), f"{name}.quality")
    _status(quality.get("status"), f"{name}.quality.status")
    _json_safe(contract, name)
    return contract


def _validate_context(context: Any) -> dict[str, Any]:
    context = _mapping(context, "context")
    for name in ("symbol", "base_asset", "quote_asset"):
        if not isinstance(context.get(name), str) or not context[name]:
            raise _error(f"context.{name}")
    if context.get("market") != "futures":
        raise _error("context.market")
    precision = _number(context.get("price_precision"), "context.price_precision", integer=True)
    if not 0 <= precision <= 12:
        raise _error("context.price_precision")
    _json_safe(context, "context")
    return deepcopy(dict(context))


def _validate_runtime(runtime: Any) -> dict[str, Any]:
    runtime = _mapping(runtime, "runtime_context")
    generated = _number(runtime.get("generated_at"), "runtime_context.generated_at", integer=True, positive=True)
    updated = _number(runtime.get("updated_at"), "runtime_context.updated_at", integer=True, positive=True)
    if updated > generated:
        raise _error("runtime_context.updated_at")
    mode, demo, cache = runtime.get("data_mode"), runtime.get("is_demo"), runtime.get("cache_status")
    if mode not in {"synthetic", "live"}:
        raise _error("runtime_context.data_mode")
    if not isinstance(demo, bool) or demo != (mode == "synthetic"):
        raise _error("runtime_context.is_demo")
    if cache not in {"disabled", "fresh", "stale", "unknown"}:
        raise _error("runtime_context.cache_status")
    _json_safe(runtime, "runtime_context")
    return deepcopy(dict(runtime))


def _validate_selection(selection: Any) -> dict[str, str]:
    selected = {**DEFAULT_SELECTION, **dict(_mapping(selection, "selection"))} if selection is not None else dict(DEFAULT_SELECTION)
    if selected.get("interval") not in INTERVALS:
        raise ValueError("invalid_selection:interval")
    if not isinstance(selected.get("exchange"), str) or not selected["exchange"]:
        raise ValueError("invalid_selection:exchange")
    if selected.get("map") not in MAP_OPTIONS:
        raise ValueError("invalid_selection:map")
    if set(selected) != set(DEFAULT_SELECTION):
        raise ValueError("invalid_selection:keys")
    return selected


def _view_status(source: Mapping[str, Any], path: str) -> tuple[str, Any]:
    return _status(source.get("status"), f"{path}.status"), source.get("reason")


def _kpis(processing: Mapping[str, Any], classification: Mapping[str, Any], context: Mapping[str, Any],
          generated_at: int) -> list[dict[str, Any]]:
    reference = _mapping(_at(processing, "maps.reference_price"), "maps.reference_price")
    ref_status, ref_reason = _view_status(reference, "maps.reference_price")
    current = _scalar("current_price", "Current Price", reference.get("value"), unit=context["quote_asset"],
        status=ref_status, reason=ref_reason, formatter=lambda value: _format_price(value, context["price_precision"]),
        timestamp=_validate_timestamp_anchor(reference.get("timestamp"), path="maps.reference_price.timestamp",
                                             generated_at=generated_at)[0], provenance=reference.get("provenance", {}))
    window = _mapping(_at(processing, "realized.windows.24h"), "realized.windows.24h")
    win_status, win_reason = _view_status(window, "realized.windows.24h")
    common = {"status": win_status, "reason": win_reason, "formatter": _format_usd,
              "timestamp": _validate_timestamp_anchor(window.get("window_end"), path="realized.windows.24h.window_end",
                                                       generated_at=generated_at)[0],
              "provenance": _at(processing, "realized.provenance", {})}
    totals = [_scalar("total_liquidations_24h", "Total Liquidations 24H", window.get("total_usd"), unit="USD", **common,
                     extra={"coverage_ratio": deepcopy(window.get("coverage_ratio")), "window_start": window.get("window_start"), "window_end": window.get("window_end")}),
              _scalar("long_liquidations_24h", "Long Liquidations 24H", window.get("long_total_usd"), unit="USD", **common),
              _scalar("short_liquidations_24h", "Short Liquidations 24H", window.get("short_total_usd"), unit="USD", **common)]
    imbalance = _mapping(window.get("imbalance"), "realized.windows.24h.imbalance")
    atom24 = _mapping(_at(classification, "classifications.realized_side.24h"), "classifications.realized_side.24h")
    imb_status = _combined_status(_status(imbalance.get("status"), "imbalance.status"), _status(atom24.get("status"), "atom24.status"))
    realized_imbalance = _scalar("realized_imbalance_24h", "Realized Imbalance 24H", imbalance.get("value"), unit="ratio",
        status=imb_status, reason=atom24.get("reason") or imbalance.get("reason"), formatter=_format_ratio,
        timestamp=window.get("window_end"), classification=atom24, provenance=atom24.get("provenance"))
    realized_side = {"id": "realized_side_24h", "label": "Realized Dominant Side 24H",
                     **_classification_model(atom24), "timestamp": common["timestamp"]}
    pressure = _mapping(_at(processing, "pressure"), "pressure")
    pressure_atom = _mapping(_at(classification, "classifications.pressure"), "classifications.pressure")
    pressure_status = _combined_status(_status(pressure.get("status"), "pressure.status"), _status(pressure_atom.get("status"), "pressure_atom.status"))
    pressure_kpi = _scalar("pressure_score", "Pressure Score", pressure.get("score"), unit="score", status=pressure_status,
        reason=pressure_atom.get("reason") or pressure.get("reason"), formatter=_format_score,
        timestamp=_validate_timestamp_anchor(processing["reference_timestamp"], path="processing.reference_timestamp",
                                             generated_at=generated_at)[0],
        classification=pressure_atom, provenance=pressure.get("provenance"),
        extra={"components": _sanitize_components(pressure_status, pressure.get("components", {}))})
    return [current, *totals, realized_imbalance, realized_side, pressure_kpi]


def _selectors(selected: Mapping[str, str], processing: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    options = []
    for identifier, (realized, event, class_path) in INTERVALS.items():
        options.append({"id": identifier, "enabled": True, "realized_window": realized, "event_window": event,
                        "map_mode": "snapshot", "classification_paths": [class_path] if class_path else []})
    binance_key = config.get("binance_exchange_key", "Binance")
    by_exchange = _at(processing, "maps.by_exchange", {})
    binance_enabled = isinstance(by_exchange, Mapping) and binance_key in by_exchange
    return {"interval": {"selected": selected["interval"], "options": options},
            "exchange": {"selected": selected["exchange"], "options": ["aggregate", *list(_at(processing, "maps.by_exchange", {}).keys())]},
            "map": {"selected": selected["map"], "options": [
                {"id": "aggregate", "enabled": True},
                {"id": "hyperliquid", "enabled": False, "reason": "source_not_contractually_available"},
                {"id": "binance", "enabled": binance_enabled,
                 "reason": None if binance_enabled else "exchange_map_not_available"}]}}


def _events(processing: Mapping[str, Any], classification: Mapping[str, Any], selected: Mapping[str, str]) -> dict[str, Any]:
    event_window = INTERVALS[selected["interval"]][1]
    source = _mapping(_at(processing, f"events.aggregate.{event_window}"), f"events.aggregate.{event_window}")
    status, reason = _view_status(source, f"events.aggregate.{event_window}")
    atom = _at(classification, "classifications.events.15m") if selected["interval"] == "15m" else None
    return {"id": "selected_event_statistics", "label": f"Observed Liquidation Events {selected['interval'].upper()}",
            "window": event_window, "status": status, "reason": deepcopy(reason),
            "window_start": deepcopy(source.get("window_start")) if _usable(status) else None,
            "window_end": deepcopy(source.get("window_end")) if _usable(status) else None,
            "event_count": deepcopy(source.get("event_count")) if _usable(status) else None,
            "event_usd_total": deepcopy(source.get("event_usd_total")) if _usable(status) else None,
            "event_usd_mean": deepcopy(source.get("event_usd_mean")) if _usable(status) else None,
            "event_usd_median": deepcopy(source.get("event_usd_median")) if _usable(status) else None,
            "event_usd_max": deepcopy(source.get("event_usd_max")) if _usable(status) else None,
            "max_event": deepcopy(source.get("max_event")) if _usable(status) else None,
            "is_lower_bound": bool(source.get("is_lower_bound")) if _usable(status) else False,
            "classification": _classification_model(atom) if atom is not None else None,
            "provenance": deepcopy(_at(processing, "events.provenance", {}))}


def _exchange_table(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_at(processing, "exchange_distribution"), "exchange_distribution")
    status, reason = _view_status(source, "exchange_distribution")
    rows = []
    if _usable(status):
        for item in source.get("exchanges", []):
            item = _mapping(item, "exchange_distribution.exchanges[]")
            rows.append({"exchange": deepcopy(item.get("exchange")), "exchange_key": deepcopy(item.get("exchange_key")),
                "long_usd": deepcopy(item.get("long_liquidation_usd")), "short_usd": deepcopy(item.get("short_liquidation_usd")),
                "computed_total_usd": deepcopy(item.get("computed_total_usd")), "provider_total_usd": deepcopy(item.get("provider_total_usd")),
                "provider_difference_usd": deepcopy(item.get("provider_total_difference_usd")),
                "provider_difference_ratio": deepcopy(item.get("provider_total_difference_ratio")),
                "exchange_share": deepcopy(item.get("exchange_share")), "status": status})
    return {"id": "exchange_distribution", "status": status, "reason": deepcopy(reason), "rows": rows,
            "concentration": _classification_model(_at(classification, "classifications.concentration.exchanges")),
            "provenance": deepcopy(source.get("provenance", {}))}


def _cluster_payload(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_at(processing, "maps.aggregated.clusters"), "maps.aggregated.clusters")
    atom = _classification_model(_at(classification, "classifications.clusters"))
    map_source = _mapping(_at(processing, "maps.aggregated"), "maps.aggregated")
    status = _combined_status(_status(map_source.get("status"), "maps.aggregated.status"), atom["status"])
    if not _usable(status):
        atom.update(classification=None, strength=None, confidence=0., status=status,
                    reason=map_source.get("reason") or atom["reason"], color_token=_token(status), evidence={})
    clean_source = _sanitize_cluster_source(status, source)
    return {"source": clean_source, "classification": atom}


def _aggregate_map(processing: Mapping[str, Any], classification: Mapping[str, Any], context: Mapping[str, Any], *,
                   generated_at: int) -> dict[str, Any]:
    source = _mapping(_at(processing, "maps.aggregated"), "maps.aggregated")
    aligned = _mapping(_at(processing, "maps.aligned_exchanges"), "maps.aligned_exchanges")
    reference = _mapping(_at(processing, "maps.reference_price"), "maps.reference_price")
    status, reason = _view_status(source, "maps.aggregated")
    ref_status, ref_reason = _view_status(reference, "maps.reference_price")
    ref_timestamp = _validate_timestamp_anchor(reference.get("timestamp"), path="maps.reference_price.timestamp",
                                               generated_at=generated_at)[0]
    reference_view = {"value": deepcopy(reference.get("value")) if _usable(ref_status) else None,
        "display_value": str(reference.get("value")) if _usable(ref_status) and reference.get("value") is not None else "â€”",
        "unit": context["quote_asset"], "classification": None, "confidence": 0., "status": ref_status, "reason": deepcopy(ref_reason),
        "color_token": _token(ref_status), "timestamp": ref_timestamp,
        "provenance": {"source_family": reference.get("source_family"), "source_market": reference.get("source_market"),
            "source_timeframe": reference.get("source_timeframe"), "price_field": reference.get("price_field")}}
    buckets = deepcopy(source.get("buckets", {"status": "unavailable", "reason": "missing_buckets", "items": []}))
    series = []
    items = aligned.get("buckets", {}).get("items", {}) if _usable(_status(aligned.get("status"), "maps.aligned_exchanges.status")) else {}
    for exchange, points in items.items():
        series.append({"exchange": exchange, "status": aligned["status"], "reason": deepcopy(aligned.get("reason")),
                       "unit": "provider_level", "points": deepcopy(points)})
    bucket_items = buckets.get("items", []) if isinstance(buckets, Mapping) else []
    central = [deepcopy(item) for item in bucket_items if isinstance(item, Mapping) and item.get("region") == "central"]
    exchange_points = {item["exchange"]: {point["bucket_index"]: point for point in item["points"]} for item in series}
    long_curve = {item["price"]: item["cumulative_share"] for item in source.get("curves", {}).get("estimated_long", [])}
    short_curve = {item["price"]: item["cumulative_share"] for item in source.get("curves", {}).get("estimated_short", [])}
    visual_buckets = [{"bucket_index": item["bucket_index"], "price_low": item["lower_price"],
        "price_center": item["center_price"], "price_high": item["upper_price"],
        "bars": {exchange.lower(): exchange_points.get(exchange, {}).get(item["bucket_index"], {}).get("level_total", 0)
            for exchange in exchange_points}, "cumulative_long": long_curve.get(item["center_price"], 0),
        "cumulative_short": short_curve.get(item["center_price"], 0)} for item in bucket_items]
    axes = {"x": {"field": "price_center", "unit": context["quote_asset"], "type": "linear"},
        "bar_axis": {"unit": "provider_level", "side": "left"},
        "cumulative_axis": {"unit": "normalized_level", "side": "right", "range": [0, 1]}}
    return {"id": "aggregate_liquidation_map", "chart_id": "aggregate_liquidation_map", "title": f"{context['base_asset']} Exchange Liquidation Map",
        "map_semantics": "estimated", "map_time_semantics": "snapshot", "provider": "coinglass",
        "current_price": reference_view["value"], "reference_price": reference_view, "axes": axes,
        "bar_series": [{"series_id": exchange.lower(), "label": exchange} for exchange in exchange_points],
        "buckets": visual_buckets if _usable(status) else [], "series_by_exchange": series,
        "provider_levels": deepcopy(source.get("provider_levels", [])) if _usable(status) else [],
        "estimated_long_curve": deepcopy(source.get("curves", {}).get("estimated_long", [])) if _usable(status) else [],
        "estimated_short_curve": deepcopy(source.get("curves", {}).get("estimated_short", [])) if _usable(status) else [],
        "estimated_side": _classification_model(_at(classification, "classifications.estimated_side")),
        "central_region": {"items": central if _usable(ref_status) else []}, "clusters": _cluster_payload(processing, classification),
        "concentration": {"source": deepcopy(source.get("concentration", {})),
            "aggregate": _classification_model(_at(classification, "classifications.concentration.aggregate_map")),
            "estimated_long": _classification_model(_at(classification, "classifications.concentration.estimated_long")),
            "estimated_short": _classification_model(_at(classification, "classifications.concentration.estimated_short"))},
        "status": status, "reason": deepcopy(reason),
        "provenance": deepcopy(source.get("provenance", {})), "unit": "provider_level",
        "visual_contract": {"renderer": "stacked_bars_plus_dual_cumulative_curves", "reference_line": {"field": "current_price", "label": "CURRENT PRICE"},
            "barmode": "stack", "x_axis": "linear_price", "hmi_calculation": False, "bucket_count": len(visual_buckets)}}


def _exchange_visual(processing: Mapping[str, Any], context: Mapping[str, Any], key: str, *, leverage: bool) -> dict[str, Any]:
    raw_source = _at(processing, f"maps.by_exchange.{key}", None)
    if not isinstance(raw_source, Mapping):
        return {"id": "binance_leverage_map" if leverage else "hyperliquid_map",
            "chart_id": "binance_leverage_map" if leverage else "hyperliquid_map",
            "title": f"{key} Liquidation Map", "status": "unavailable", "reason": "exchange_map_not_available",
            "proxy": False, "current_price": None, "reference_price": None, "axes": {}, "bar_series": [], "buckets": [],
            "provenance": {}, "unit": "provider_level", "visual_contract": {"hmi_calculation": False, "bucket_count": 0}}
    source = _mapping(raw_source, f"maps.by_exchange.{key}")
    reference = _mapping(_at(processing, "maps.reference_price"), "maps.reference_price")
    status, reason = _view_status(source, f"maps.by_exchange.{key}")
    raw = source.get("buckets", {}).get("items", []) if _usable(status) else []
    long_curve = {item["price"]: item["cumulative_share"] for item in source.get("curves", {}).get("estimated_long", [])}
    short_curve = {item["price"]: item["cumulative_share"] for item in source.get("curves", {}).get("estimated_short", [])}
    leverage_ids = sorted({str(value).removesuffix(".0") + "x" for item in raw for value in item.get("leverage_breakdown", {})}, key=lambda value: float(value[:-1]))
    series_ids = leverage_ids if leverage else ["long", "short"]
    buckets = []
    for item in raw:
        if leverage:
            bars = {str(value).removesuffix(".0") + "x": amount for value, amount in item.get("leverage_breakdown", {}).items()}
        else:
            bars = {"long": item["level_total"] if item.get("region") == "estimated_long" else 0,
                    "short": item["level_total"] if item.get("region") == "estimated_short" else 0}
        buckets.append({"bucket_index": item["bucket_index"], "price_low": item["lower_price"],
            "price_center": item["center_price"], "price_high": item["upper_price"], "bars": bars,
            "cumulative_long": long_curve.get(item["center_price"], 0), "cumulative_short": short_curve.get(item["center_price"], 0)})
    quote = context["quote_asset"]
    return {"id": "binance_leverage_map" if leverage else "hyperliquid_map",
        "chart_id": "binance_leverage_map" if leverage else "hyperliquid_map",
        "title": f"{key} {context['base_asset']}/{quote} Liquidation Map", "status": status, "reason": reason,
        "proxy": False, "current_price": reference.get("value"),
        "reference_price": {"value": reference.get("value"), "unit": quote, "timestamp": reference.get("timestamp"),
            "status": reference.get("status"), "reason": reference.get("reason"),
            "provenance": {"source_family": reference.get("source_family"), "source_market": reference.get("source_market"),
                "source_timeframe": reference.get("source_timeframe"), "price_field": reference.get("price_field")}},
        "axes": {"x": {"field": "price_center", "unit": quote, "type": "linear"},
            "bar_axis": {"unit": "provider_level", "side": "left"},
            "cumulative_axis": {"unit": "normalized_level", "side": "right", "range": [0, 1]}},
        "bar_series": [{"series_id": item, "label": item} for item in series_ids], "buckets": buckets,
        "provenance": deepcopy(source.get("provenance", {})), "unit": "provider_level",
        "visual_contract": {"renderer": "stacked_bars_plus_dual_cumulative_curves", "reference_line": {"field": "current_price", "label": "CURRENT PRICE"},
            "barmode": "stack", "x_axis": "linear_price", "hmi_calculation": False, "bucket_count": len(buckets)}}


def _hyperliquid(processing: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return _exchange_visual(processing, context, "Hyperliquid", leverage=False)


def _binance(processing: Mapping[str, Any], config: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    key = config.get("binance_exchange_key", "Binance")
    display_pair = f"{context['base_asset']}/{context['quote_asset']}"
    title = f"Binance {display_pair} Liquidation Map"
    leverage_title = f"Binance {display_pair} Liquidation Map by Leverage"
    if not isinstance(key, str) or not key:
        raise _error("config.binance_exchange_key")
    maps = _mapping(_at(processing, "maps.by_exchange", {}), "maps.by_exchange")
    if key not in maps:
        return {"id": "binance_leverage_map", "title": title, "leverage_title": leverage_title, "exchange_key": key,
                "status": "unavailable", "reason": "exchange_map_not_available", "provider_levels": [], "buckets": [],
                "estimated_long_curve": [], "estimated_short_curve": [], "stacked_buckets": [], "leverage_curves": [], "badges": []}
    visual = _exchange_visual(processing, context, key, leverage=True)
    visual.update(leverage_title=leverage_title, exchange_key=key)
    return visual


def _confirmations(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    processing_items = _mapping(_at(processing, "realized.confirmations"), "realized.confirmations")
    class_items = _mapping(_at(classification, "classifications.confirmations"), "classifications.confirmations")
    rows = []
    for provider, atom in class_items.items():
        metrics = _mapping(processing_items.get(provider), f"realized.confirmations.{provider}")
        model = _classification_model(atom)
        def value(name):
            metric = _mapping(metrics.get(name), f"realized.confirmations.{provider}.{name}")
            return deepcopy(metric.get("value")) if _usable(_status(metric.get("status"), f"{name}.status")) else None
        endpoint = "aggregate/hour" if provider == "cryptoquant" else "futures_liquidated_volume_{long,short}_sum"
        rows.append({"provider": provider, "endpoint": endpoint,
            "confirmation": {"classification": model["classification"], "confidence": model["confidence"],
                "status": model["status"], "evidence": model["evidence"]},
            "classification": model["classification"], "confidence": model["confidence"],
            "aligned_point_count": value("aligned_point_count"), "coverage_ratio": value("coverage_ratio"),
            "pearson_correlation": value("pearson_correlation"), "mape": value("median_absolute_percentage_error"),
            "status": model["status"], "reason": model["reason"], "evidence": model["evidence"], "provenance": model["provenance"]})
    status = "available" if not rows else _combined_status(*(row["status"] for row in rows))
    return {"id": "provider_confirmations", "status": status, "reason": None, "rows": rows}


def _max_pain(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_at(processing, "maps.max_pain"), "maps.max_pain")
    status, reason = _view_status(source, "maps.max_pain")
    model = _classification_model(_at(classification, "classifications.max_pain"))
    fields = ("provider_price", "provider_price_difference_bps", "long_max_pain_price", "short_max_pain_price",
              "long_max_pain_level", "short_max_pain_level", "long_distance_bps", "short_distance_bps")
    return {"id": "max_pain", **{name: deepcopy(source.get(name)) if _usable(status) else None for name in fields},
            "classification": model["classification"], "confidence": model["confidence"], "status": status,
            "reason": deepcopy(reason or model["reason"]), "provenance": deepcopy(source.get("provenance", {}))}


def _providers(processing: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selection = deepcopy(dict(_mapping(_at(processing, "source_selection"), "source_selection")))
    grouped: dict[str, dict[str, Any]] = {}
    for role, item in selection.items():
        item = _mapping(item, f"source_selection.{role}")
        provider = item.get("provider")
        if not isinstance(provider, str):
            raise _error(f"source_selection.{role}.provider")
        entry = grouped.setdefault(provider, {"provider": provider, "roles": [], "status": "unavailable", "selected": False})
        entry["roles"].append(role)
        entry["selected"] = entry["selected"] or item.get("selected") is True
        status = _status(item.get("status"), f"source_selection.{role}.status")
        if STATUS_RANK[status] < STATUS_RANK[entry["status"]] or len(entry["roles"]) == 1:
            entry["status"] = status
    reference = _mapping(_at(processing, "maps.reference_price"), "maps.reference_price")
    if reference.get("source_family") == "prices_ohlcv":
        grouped["prices_ohlcv"] = {"provider": "prices_ohlcv", "roles": ["reference_price"],
            "status": _status(reference.get("status"), "maps.reference_price.status"), "selected": True}
    return list(grouped.values()), selection


def _quality(processing: Mapping[str, Any], classification: Mapping[str, Any], views: Mapping[str, Any],
             anchors: Mapping[str, tuple[Any, str]], generated_at: int) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    missing = [name for name in REQUIRED_VIEWS if name not in views]
    statuses = {name: views[name]["status"] for name in views if isinstance(views[name], Mapping) and views[name].get("status") in VALID_STATUS}
    invalid = [name for name, status in statuses.items() if status == "invalid"]
    partial = [name for name, status in statuses.items() if status == "partial"]
    unavailable = [name for name, status in statuses.items() if status == "unavailable"]
    required_statuses = [statuses.get(name, "invalid") for name in REQUIRED_VIEWS]
    p_quality = _at(processing, "quality.status")
    c_quality = _at(classification, "quality.status")
    if p_quality == "invalid" or c_quality == "invalid" or missing or any(status == "invalid" for status in required_statuses):
        status = "invalid"
    elif p_quality == "unavailable" or c_quality == "unavailable":
        status = "unavailable"
    elif any(value == "unavailable" for value in required_statuses):
        status = "partial" if any(_usable(value) for value in required_statuses) else "unavailable"
    elif p_quality == "partial" or c_quality == "partial" or any(value == "partial" for value in required_statuses):
        status = "partial"
    else:
        status = "available"
    sanitized, own_warnings, seen = {}, [], set()
    for name in REQUIRED_VIEWS:
        if not _usable(statuses.get(name, "invalid")):
            sanitized[name] = None
            continue
        value, path = anchors[name]
        valid, cause = _validate_timestamp_anchor(value, path=path, generated_at=generated_at)
        sanitized[name] = valid
        issue = (path, cause)
        if cause is not None and issue not in seen:
            if value is None:
                own_warnings.append(f"missing_required_timestamp:{name}")
            elif cause == "future":
                own_warnings.append(f"future_required_timestamp:{name}")
            else:
                own_warnings.append(f"invalid_required_timestamp:{name}:{cause}")
            seen.add(issue)
    missing_timestamps = [name for name in REQUIRED_VIEWS if _usable(statuses.get(name, "invalid")) and sanitized[name] is None]
    if missing_timestamps and status == "available":
        status = "partial"
    valid_anchors = [sanitized[name] for name in REQUIRED_VIEWS if _usable(statuses.get(name, "invalid"))]
    data_as_of = min(valid_anchors) if valid_anchors and not missing_timestamps else None
    return {"status": status, "required_view_models": list(REQUIRED_VIEWS), "optional_view_models": list(OPTIONAL_VIEWS),
        "missing_view_models": missing, "partial_view_models": partial, "unavailable_view_models": unavailable,
        "invalid_view_models": invalid, "warnings": own_warnings,
        "errors": ["missing_required_view_model"] if missing else []}, data_as_of, sanitized


def build_long_short_liquidations_contract(processing_contract: Mapping[str, Any], classification_contract: Mapping[str, Any], *,
                                            context: Mapping[str, Any], runtime_context: Mapping[str, Any],
                                            selection: Mapping[str, Any] | None = None,
                                            config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    processing = _validate_contract(processing_contract, "processing", "processing")
    classification = _validate_contract(classification_contract, "classification", "classification")
    if processing["reference_timestamp"] != classification["reference_timestamp"]:
        raise _error("reference_timestamp")
    screen_context, runtime, selected = _validate_context(context), _validate_runtime(runtime_context), _validate_selection(selection)
    config = deepcopy(dict(_mapping(config, "config"))) if config is not None else {}
    _json_safe(config, "config")
    kpis = _kpis(processing, classification, screen_context, runtime["generated_at"])
    by_id = {item["id"]: item for item in kpis}
    events = _events(processing, classification, selected)
    selected_event_timestamp = events.get("window_end")
    events["window_end"] = _validate_timestamp_anchor(events.get("window_end"),
        path=f"events.aggregate.{events['window']}.window_end", generated_at=runtime["generated_at"])[0]
    event15_source = _mapping(_at(processing, "events.aggregate.15m"), "events.aggregate.15m")
    event15_atom = _mapping(_at(classification, "classifications.events.15m"), "classifications.events.15m")
    event15_status = _combined_status(_status(event15_source.get("status"), "events.aggregate.15m.status"),
                                      _status(event15_atom.get("status"), "classifications.events.15m.status"))
    event15_view = {"id": "event_activity_15m", "status": event15_status, "reason": event15_atom.get("reason") or event15_source.get("reason"),
                    "classification": _classification_model(event15_atom)}
    exchange = _exchange_table(processing, classification)
    aggregate = _aggregate_map(processing, classification, screen_context, generated_at=runtime["generated_at"])
    hyperliquid, binance = _hyperliquid(processing, screen_context), _binance(processing, config, screen_context)
    confirmations, max_pain = _confirmations(processing, classification), _max_pain(processing, classification)
    interval_window = INTERVALS[selected["interval"]][0]
    if interval_window is None:
        selected_side = {"id": "selected_realized_side", "label": "Selected Realized Side", **_classification_model(
            {"classification": None, "strength": None, "confidence": 0., "status": "unavailable",
             "reason": "realized_window_not_available_for_selection", "evidence": {}, "provenance": {}})}
        selected_imbalance = _scalar("selected_realized_imbalance", "Selected Realized Imbalance", None, unit="ratio",
            status="unavailable", reason="realized_window_not_available_for_selection", formatter=_format_ratio)
    else:
        window = _at(processing, f"realized.windows.{interval_window}")
        atom = _at(classification, f"classifications.realized_side.{interval_window}")
        selected_side = {"id": "selected_realized_side", "label": "Selected Realized Side", **_classification_model(atom),
                         "timestamp": _validate_timestamp_anchor(window.get("window_end"),
                            path=f"realized.windows.{interval_window}.window_end", generated_at=runtime["generated_at"])[0]}
        imb = window["imbalance"]
        selected_imbalance = _scalar("selected_realized_imbalance", "Selected Realized Imbalance", imb.get("value"), unit="ratio",
            status=_combined_status(imb["status"], atom["status"]), reason=atom.get("reason") or imb.get("reason"), formatter=_format_ratio,
            timestamp=_validate_timestamp_anchor(window.get("window_end"), path=f"realized.windows.{interval_window}.window_end",
                                                 generated_at=runtime["generated_at"])[0],
            classification=atom, provenance=atom.get("provenance"))
    estimated_atom = _at(classification, "classifications.estimated_side")
    estimated_source = _at(processing, "maps.aggregated.estimated_side_imbalance")
    estimated_side = {"id": "estimated_side", "label": "Estimated Exposure Side", **_classification_model(estimated_atom)}
    estimated_imbalance = _scalar("estimated_imbalance", "Estimated Exposure Imbalance", estimated_source.get("value"), unit="ratio",
        status=_combined_status(estimated_source["status"], estimated_atom["status"]), reason=estimated_atom.get("reason") or estimated_source.get("reason"),
        formatter=_format_ratio, classification=estimated_atom, provenance=estimated_atom.get("provenance"))
    cluster_payload = _cluster_payload(processing, classification)
    clusters_atom = cluster_payload["classification"]
    clusters_source = cluster_payload["source"]
    clusters = {"id": "clusters", "status": clusters_atom["status"], "reason": clusters_atom["reason"],
        "nearest_estimated_long_cluster": deepcopy((clusters_source.get("estimated_long") or [None])[0]),
        "nearest_estimated_short_cluster": deepcopy((clusters_source.get("estimated_short") or [None])[0]),
        "cluster_regime": clusters_atom, "side_strengths": deepcopy(clusters_atom["evidence"].get("side_strengths", {}))}
    concentration = _classification_model(_at(classification, "classifications.concentration.exchanges"))
    map_concentration = _classification_model(_at(classification, "classifications.concentration.aggregate_map"))
    views = {**by_id, "aggregate_liquidation_map": aggregate, "event_activity_15m": event15_view,
             "exchange_concentration": concentration, "selected_realized_side": selected_side,
             "selected_realized_imbalance": selected_imbalance, "estimated_side": estimated_side,
             "estimated_imbalance": estimated_imbalance, "map_concentrations": map_concentration, "clusters": clusters,
             "provider_confirmations": confirmations, "max_pain": max_pain, "hyperliquid_map": hyperliquid,
             "binance_leverage_map": binance, "event_1h_classification": {"status": "unavailable"}}
    distribution_provenance = _at(processing, "exchange_distribution.provenance", {})
    realized_anchor = (_at(processing, "realized.windows.24h.window_end", None), "realized.windows.24h.window_end")
    distribution_anchor = distribution_provenance.get("source_data_as_of")
    distribution_path = "exchange_distribution.provenance.source_data_as_of"
    if distribution_anchor is None:
        distribution_anchor = distribution_provenance.get("snapshot_observed_at")
        distribution_path = "exchange_distribution.provenance.snapshot_observed_at"
    anchors = {"current_price": (_at(processing, "maps.reference_price.timestamp", None), "maps.reference_price.timestamp"),
        "total_liquidations_24h": realized_anchor, "long_liquidations_24h": realized_anchor,
        "short_liquidations_24h": realized_anchor,
        "pressure_score": (processing["reference_timestamp"], "processing.reference_timestamp"),
        "realized_side_24h": realized_anchor,
        "aggregate_liquidation_map": (_at(processing, "maps.aggregated.provenance.source_snapshot_timestamp", None),
                                      "maps.aggregated.provenance.source_snapshot_timestamp"),
        "event_activity_15m": (event15_source.get("window_end"), "events.aggregate.15m.window_end"),
        "exchange_concentration": (distribution_anchor, distribution_path)}
    quality, data_as_of, clean_anchors = _quality(processing, classification, views, anchors, runtime["generated_at"])
    optional_warnings = []
    if _usable(events["status"]):
        _, optional_cause = _validate_timestamp_anchor(selected_event_timestamp,
            path=f"events.aggregate.{events['window']}.window_end", generated_at=runtime["generated_at"])
        if optional_cause is not None and events["window"] != "15m":
            prefix = "missing_optional_timestamp" if selected_event_timestamp is None else "invalid_optional_timestamp"
            optional_warnings.append(f"{prefix}:selected_window_largest_event" +
                                     (f":{optional_cause}" if prefix.startswith("invalid") else ""))
    warnings = list(dict.fromkeys([*deepcopy(_at(processing, "quality.warnings", [])),
        *deepcopy(_at(classification, "quality.warnings", [])), *quality["warnings"], *optional_warnings]))
    errors = list(dict.fromkeys([*deepcopy(_at(processing, "quality.errors", [])),
        *deepcopy(_at(classification, "quality.errors", [])), *quality["errors"]]))
    event_provenance = _at(processing, "events.provenance", {})
    divergent = any(row["classification"] == "provider_divergent" for row in confirmations["rows"])
    badges = [_badge("demo", "Demo", runtime["is_demo"], None, "context.synthetic_fixture"),
        _badge("synthetic", "Synthetic", runtime["data_mode"] == "synthetic", None, "context.data_mode"),
        _badge("estimated", "Estimated", True, None, "maps.aggregated"),
        _badge("interpolated", "Interpolated", False, None, "maps.aggregated.provenance.interpolation_enabled"),
        _badge("proxy", "Proxy", False, None, "charts.hyperliquid_map.proxy"),
        _badge("partial", "Partial", quality["status"] == "partial", None, "quality.status"),
        _badge("stale_reference", "Stale Reference", _at(processing, "maps.reference_price.reason", None) == "stale_reference_price",
               _at(processing, "maps.reference_price.reason", None), "maps.reference_price.reason"),
        _badge("truncated_events", "Truncated Events", event_provenance.get("truncation_detected") is True, None,
               "events.provenance.truncation_detected"),
        _badge("lower_bound", "Lower Bound", events["is_lower_bound"], None, f"events.aggregate.{events['window']}.is_lower_bound"),
        _badge("provider_divergence", "Provider Divergence", divergent, None, "classifications.confirmations")]
    providers, source_selection = _providers(processing)
    side_items = [by_id["pressure_score"], selected_side, selected_imbalance, by_id["realized_side_24h"],
        by_id["realized_imbalance_24h"], estimated_side, estimated_imbalance,
        _with_view_id(concentration, view_id="exchange_concentration"),
        _with_view_id(map_concentration, view_id="aggregate_map_concentration"),
        _with_view_id(_classification_model(event15_atom), view_id="event_activity_15m"),
        _with_view_id({"status": events["status"], "reason": events["reason"],
                       "value": deepcopy(events.get("max_event")) if _usable(events["status"]) else None},
                      view_id="selected_window_largest_event"),
        _with_view_id({"status": clusters["status"], "reason": clusters["reason"],
                       "value": clusters["nearest_estimated_long_cluster"]}, view_id="nearest_estimated_long_cluster"),
        _with_view_id({"status": clusters["status"], "reason": clusters["reason"],
                       "value": clusters["nearest_estimated_short_cluster"]}, view_id="nearest_estimated_short_cluster"),
        _with_view_id({**confirmations, "items": confirmations["rows"]}, view_id="provider_confirmations"), max_pain,
        _with_view_id({"status": quality["status"], "reason": None, "warnings": quality["warnings"],
                       "errors": quality["errors"]}, view_id="screen_quality_summary")]
    result = {"contract_version": "1.2.0", "screen_id": "long_short_liquidations", "family": "long_short_liquidations",
        "stage": "screen_contract_final", "reference_timestamp": processing["reference_timestamp"],
        "context": {**screen_context, "exchange_scope": selected["exchange"], "selected_interval": selected["interval"],
                    "available_intervals": list(INTERVALS)},
        "timestamps": {"generated_at": runtime["generated_at"], "updated_at": runtime["updated_at"], "data_as_of": data_as_of,
            "reference_price_as_of": clean_anchors["current_price"],
            "map_snapshot_as_of": clean_anchors["aggregate_liquidation_map"],
            "events_coverage_as_of": events.get("window_end"),
            "realized_data_as_of": clean_anchors["total_liquidations_24h"],
            "exchange_distribution_as_of": clean_anchors["exchange_concentration"]},
        "mode": runtime["data_mode"],
        "header": {"title": "LONG / SHORT LIQUIDATIONS", "symbol": screen_context["symbol"], "market": screen_context["market"],
            "exchange_scope": selected["exchange"], "selected_interval": selected["interval"], "data_as_of": data_as_of,
            "updated_at": runtime["updated_at"], "badges": deepcopy(badges), "status": quality["status"]},
        "kpis": kpis, "selectors": _selectors(selected, processing, config),
        "charts": {"aggregate_map": aggregate, "hyperliquid_map": hyperliquid, "binance_leverage_map": binance},
        "side_panel": {"id": "liquidation_target_summary", "title": "LIQUIDATION TARGET SUMMARY", "items": side_items},
        "tables": {"exchange_distribution": exchange, "provider_confirmations": confirmations}, "badges": badges,
        "providers": providers, "source_selection": source_selection,
        "calculation_history": {"source_interval": _at(processing, "realized.provenance.source_interval", "1h"),
            "records_available": len(_at(processing, "realized.series", [])), "fabricated_records": 0,
            "warmup_records": 0, "owner": "Processing"},
        "history_contract": {"source_resolution": "1h", "map_time_semantics": "snapshot",
            "hmi_calculation": False, "fabricated_records": 0},
        "quality": quality, "warnings": warnings, "errors": errors}
    result = align_long_short_liquidations_to_sp_v1_2(result, processing, classification, runtime)
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


class LongShortLiquidationsContractBuilder:
    def __init__(self, *, context: Mapping[str, Any], runtime_context: Mapping[str, Any], config: Mapping[str, Any] | None = None):
        self._context = _validate_context(context)
        self._runtime = _validate_runtime(runtime_context)
        self._config = deepcopy(dict(_mapping(config, "config"))) if config is not None else None
        _json_safe(self._config, "config")

    def build(self, processing_contract: Mapping[str, Any], classification_contract: Mapping[str, Any], *,
              selection: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return build_long_short_liquidations_contract(processing_contract, classification_contract, context=self._context,
            runtime_context=self._runtime, selection=selection, config=self._config)


def export_long_short_liquidations_contract(contract: Mapping[str, Any], path: str | Path =
                                             "runtime/contracts/hmi_contract/long_short_liquidations_screen.json") -> Path:
    contract = _mapping(contract, "contract")
    _json_safe(contract, "contract")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp", prefix=f".{destination.name}.",
                                         dir=destination.parent, delete=False, newline="\n") as handle:
            temporary = Path(handle.name)
            json.dump(contract, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise
