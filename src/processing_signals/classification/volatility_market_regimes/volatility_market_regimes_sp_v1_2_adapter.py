from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

SP_SCHEMA_VERSION = "1.2.0-no-regime-timeline"
DISPLAY_RANGES = ("7d", "30d", "90d", "360d")
DEFAULT_RANGE = "30d"
SP_TEMPLATE_PATH = Path(__file__).with_name("volatility_market_regimes_screen_sp_v1_2.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return 0.0 if value == 0 else float(value)


def _iso(timestamp: int | None) -> str | None:
    if type(timestamp) is not int:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _project(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project runtime values onto the exact frozen Screen-SP shape."""
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {key: _project(value, source.get(key, _MISSING)) for key, value in reference.items()}
    if isinstance(reference, list):
        if candidate is _MISSING:
            return deepcopy(reference)
        if not isinstance(candidate, list):
            return deepcopy(reference)
        if not reference or all(_is_scalar(item) for item in reference):
            return deepcopy(candidate)
        if not candidate:
            return []
        identity_keys = (
            "metric_id", "widget_id", "chart_id", "table_id", "badge_id", "id",
            "role", "event_group", "indicator_id", "regime", "row_id",
        )
        def ref_for(item: Any, index: int) -> Any:
            if isinstance(item, Mapping):
                for key in identity_keys:
                    value = item.get(key, _MISSING)
                    if value is _MISSING:
                        continue
                    for ref_item in reference:
                        if isinstance(ref_item, Mapping) and ref_item.get(key, _MISSING) == value:
                            return ref_item
            return reference[index] if index < len(reference) else reference[0]
        return [_project(ref_for(item, index), item) for index, item in enumerate(candidate)]
    return deepcopy(reference if candidate is _MISSING else candidate)


def _context(reference: Mapping[str, Any], processing: Mapping[str, Any], runtime_context: Mapping[str, Any], selected_range: str) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    pctx = processing.get("context", {})
    anchor = pctx.get("reference_timestamp")
    range_seconds = {"7d": 7*86400, "30d": 30*86400, "90d": 90*86400, "360d": 360*86400}
    out.update({
        "symbol": pctx.get("symbol", "BTCUSDT"),
        "asset": pctx.get("asset", "BTC"),
        "exchange": pctx.get("exchange", "Binance"),
        "base_interval": pctx.get("base_interval", "1h"),
        "default_display_range": DEFAULT_RANGE,
        "selected_display_range": selected_range,
        "available_display_ranges": list(DISPLAY_RANGES),
        "data_mode": runtime_context.get("data_mode"),
        "is_demo": runtime_context.get("is_demo"),
        "generated_at": runtime_context.get("generated_at"),
        "updated_at": runtime_context.get("updated_at"),
        "reference_timestamp": anchor,
        "input_execution_timestamp": pctx.get("input_execution_timestamp"),
        "data_as_of": anchor,
    })
    out["history_policy"] = {
        "calculation": "full_available_history",
        "presentation": "tail_window",
        "max_hourly_display_seconds": 30*86400,
        "max_daily_display_days": 30,
    }
    out["range_windows"] = {
        name: {
            "start_timestamp": int(anchor) - seconds if type(anchor) is int else None,
            "end_timestamp": anchor,
            "expected_points": int(name[:-1]),
        }
        for name, seconds in range_seconds.items()
    }
    if runtime_context.get("is_demo"):
        out.update({
            "fixture_as_of_timestamp": anchor,
            "fixture_as_of_iso": _iso(anchor),
            "synthetic_fixture": True,
            "realism_refactor_version": "runtime_emulator_v1",
            "realism_note": "deterministic provider-shaped emulator: Glassnode RV/DVOL and CoinGlass positioning",
        })
    else:
        out.update({
            "fixture_as_of_timestamp": None,
            "fixture_as_of_iso": None,
            "synthetic_fixture": False,
            "realism_refactor_version": "runtime_provider_v1",
            "realism_note": "runtime provider data: Glassnode realized volatility/DVOL and CoinGlass positioning",
        })
    return out


def _kpis(reference: Mapping[str, Any], candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    current = {str(item.get("metric_id")): deepcopy(item) for item in candidate.get("kpis", {}).get("items", []) if isinstance(item, Mapping)}
    daily = classification.get("classifications", {}).get("daily_regimes", {})
    regime = daily.get("current") if isinstance(daily.get("current"), Mapping) else {}
    context = classification.get("classifications", {}).get("volatility_context", {})
    vctx = context.get("current") if isinstance(context.get("current"), Mapping) else {}
    dvol_feature = processing.get("features", {}).get("dvol", {})
    dvol_current = dvol_feature.get("current") if isinstance(dvol_feature.get("current"), Mapping) else {}
    spread_feature = processing.get("features", {}).get("volatility_spread", {})
    spread_current = spread_feature.get("current") if isinstance(spread_feature.get("current"), Mapping) else {}

    regime_value = regime.get("regime")
    confidence = _finite(regime.get("confidence_score"))
    persistence = regime.get("persistence_days") if type(regime.get("persistence_days")) is int else None
    spread = _finite(spread_current.get("spread_7d"))
    dvol = _finite(dvol_current.get("close"))
    records_used = spread_current.get("records_used_7d") if type(spread_current.get("records_used_7d")) is int else 0
    end_ts = spread_current.get("timestamp") if type(spread_current.get("timestamp")) is int else None
    start_ts = end_ts - max(0, records_used-1)*3600 if end_ts is not None else None

    current["current_regime"] = {
        "metric_id": "current_regime", "label": "Current Regime", "value": regime_value,
        "display_value": {"low_vol":"Low Vol","normal":"Normal","high_vol":"High Vol"}.get(regime_value, "—"),
        "unit": "state", "status": daily.get("status", "unavailable"), "reason": daily.get("reason"),
        "color_token": f"regime_{regime_value}" if regime_value else "regime_unavailable",
    }
    current["confidence"] = {
        "metric_id": "confidence", "label": "Confidence", "value": confidence,
        "display_value": f"{confidence*100:.0f}%" if confidence is not None else "—", "unit": "decimal",
        "status": "available" if confidence is not None else "unavailable", "reason": None if confidence is not None else "confidence_unavailable",
        "color_token": "confidence_high" if confidence is not None and confidence >= .75 else "confidence_medium" if confidence is not None and confidence >= .4 else "confidence_low",
    }
    current["spread_7d"] = {
        "metric_id": "spread_7d", "label": "Spread (7D)", "value": spread,
        "display_value": f"{spread:.1f} vol pts" if spread is not None else "—", "unit": "volatility_points",
        "status": spread_feature.get("status", "unavailable"), "reason": spread_feature.get("reason"),
        "color_token": "spread_positive" if spread is not None and spread > 0 else "spread_negative" if spread is not None and spread < 0 else "spread_neutral",
        "metadata": {"basis": "realized_minus_implied", "records_used": records_used,
                     "coverage": min(1.0, records_used/(7*24)) if records_used else 0.0,
                     "window_start_timestamp": start_ts, "window_end_timestamp": end_ts},
    }
    current["persistence"] = {
        "metric_id": "persistence", "label": "Persistence", "value": persistence,
        "display_value": f"{persistence} days" if persistence is not None else "—", "unit": "days",
        "status": "available" if persistence is not None else "unavailable", "reason": None if persistence is not None else "persistence_unavailable",
        "color_token": f"regime_{regime_value}" if regime_value else "regime_unavailable",
    }
    current["dvol"] = {
        "metric_id": "dvol", "label": "DVOL", "value": dvol,
        "display_value": f"{dvol:.1f}" if dvol is not None else "—", "unit": "volatility_index",
        "status": dvol_feature.get("status", "unavailable"), "reason": dvol_feature.get("reason"),
        "source": {"provider": "glassnode", "metric": "dvol_ohlc", "role": "classification_and_widget"},
        "quality": {"data_mode": runtime_context.get("data_mode"), "contract_ready": dvol is not None},
        "provenance": {"provider": "glassnode", "metric": "dvol_ohlc", "integration_state": "runtime_feed"},
    }
    order = ("current_regime", "confidence", "spread_7d", "persistence", "dvol")
    return {"items": [current[item] for item in order]}


def _daily_last(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[int, Mapping[str, Any]] = {}
    for row in records:
        if not isinstance(row, Mapping) or type(row.get("timestamp")) is not int:
            continue
        day = int(row["timestamp"]) - int(row["timestamp"]) % 86400
        by_day[day] = row
    return [deepcopy(dict(by_day[key])) for key in sorted(by_day)]


def _positioning_chart(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    cls = classification.get("classifications", {}).get("positioning", {})
    rows = _daily_last([row for row in cls.get("records", []) if isinstance(row, Mapping) and row.get("status") == "available"])
    records = []
    for row in rows:
        state = row.get("positioning_state")
        records.append({
            "timestamp": row.get("timestamp"), "long_short_ratio": row.get("long_short_ratio"),
            "long_percent": row.get("long_percent"), "short_percent": row.get("short_percent"),
            "net_long_percentage_points": row.get("net_long_percentage_points"),
            "positioning_state": state, "crowding_state": row.get("crowding_state"),
            "color_token": "positioning_long" if row.get("net_long_percentage_points", 0) >= 0 else "positioning_short",
        })
    first = records[0]["timestamp"] if records else None
    last = records[-1]["timestamp"] if records else None
    dynamic = {
        "chart_id": "long_short_positioning_ratio", "title": "Long / Short Positioning Ratio", "chart_type": "line",
        "status": cls.get("status", "unavailable"), "reason": cls.get("reason"), "unit": "ratio",
        "reference_lines": [{"value": 1.0, "label": "Balanced"}],
        "source": {"provider": "coinglass", "endpoint_id": "top_position_long_short_ratio", "exchange": "Binance", "symbol": "BTCUSDT"},
        "selector_behavior": "timestamp_window_filter", "records": records, "warnings": [],
        "records_available": len(records), "records_returned": len(records), "history_truncated": False,
        "first_available_timestamp": first, "last_available_timestamp": last,
        "first_returned_timestamp": first, "last_returned_timestamp": last,
    }
    return _project(reference, dynamic)


def _volatility_chart(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    ta = processing.get("features", {}).get("technical_analysis", {})
    candles = []
    for row in ta.get("candles", []):
        if not isinstance(row, Mapping):
            continue
        candles.append({"timestamp": row.get("timestamp"), "open": row.get("open"), "high": row.get("high"),
                        "low": row.get("low"), "close": row.get("close"), "unit": "percent"})
    first = candles[0]["timestamp"] if candles else None
    last = candles[-1]["timestamp"] if candles else None
    dynamic = {
        "chart_id": "realized_volatility_7d", "title": "Realized Volatility (7D)",
        "subtitle": "7D realized volatility · annualized", "chart_type": "candlestick", "unit": "percent",
        "status": ta.get("status", "unavailable"), "reason": ta.get("reason"),
        "selector_behavior": "timestamp_window_filter", "candles": candles,
        "records_available": len(candles), "records_returned": len(candles), "history_truncated": False,
        "first_available_timestamp": first, "last_available_timestamp": last,
        "first_returned_timestamp": first, "last_returned_timestamp": last,
        "source": {"provider": "glassnode", "endpoint_id": "realized_volatility_1_week"},
        "supporting_reference": {"implied_volatility_provider": "glassnode", "implied_volatility_endpoint_id": "dvol_ohlc",
                                 "purpose": "regime_and_spread_classification"},
        "ohlc_contract": {"required_fields": ["timestamp","open","high","low","close"],
                          "native_provider_ohlc": False,
                          "construction": "processing_endpoint_ohlc_from_realized_volatility_samples",
                          "hmi_must_reconstruct_ohlc": False, "volume_allowed": False,
                          "series_container": "candles", "line_fallback_allowed": False},
    }
    return _project(reference, dynamic)


def _source_status(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    features = processing.get("features", {})
    daily = classification.get("classifications", {}).get("daily_regimes", {})
    rows = [
        {"provider_id": "coinglass", "label": "CoinGlass", "status": features.get("positioning",{}).get("status","unavailable"),
         "reason": features.get("positioning",{}).get("reason"), "data_as_of": features.get("positioning",{}).get("source_data_as_of")},
        {"provider_id": "glassnode", "label": "Glassnode RV", "status": features.get("realized_volatility",{}).get("status","unavailable"),
         "reason": features.get("realized_volatility",{}).get("reason"), "data_as_of": features.get("realized_volatility",{}).get("source_data_as_of")},
        {"provider_id": "glassnode_dvol", "label": "Glassnode DVOL", "status": features.get("dvol",{}).get("status","unavailable"),
         "reason": features.get("dvol",{}).get("reason"), "data_as_of": features.get("dvol",{}).get("source_data_as_of")},
        {"provider_id": "internal", "label": "Internal", "status": daily.get("status","unavailable"),
         "reason": daily.get("reason"), "data_as_of": daily.get("source_data_as_of")},
    ]
    statuses = [row["status"] for row in rows]
    status = "available" if all(x == "available" for x in statuses) else "partial" if any(x in {"available","partial"} for x in statuses) else "unavailable"
    dynamic = {"widget_id": "source_status", "status": status,
               "reason": None if status == "available" else "one_or_more_sources_degraded", "items": rows}
    return _project(reference, dynamic)


def _event_reference(reference_by_id: Mapping[str, Any], event: Mapping[str, Any]) -> Mapping[str, Any]:
    if event.get("event_type") == "regime_transition":
        return next((v for v in reference_by_id.values() if isinstance(v, Mapping) and v.get("event_type") == "regime_transition"), {})
    group = event.get("event_group")
    return next((v for v in reference_by_id.values() if isinstance(v, Mapping) and v.get("event_group") == group),
                next((v for v in reference_by_id.values() if isinstance(v, Mapping) and v.get("event_type") == "technical_cross"), {}))


def _events(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    ref_by_id = reference.get("by_id", {})
    cand_by_id = candidate.get("by_id", {}) if isinstance(candidate.get("by_id"), Mapping) else {}
    by_id: dict[str, Any] = {}
    for event_id, event in cand_by_id.items():
        if not isinstance(event, Mapping):
            continue
        ref = _event_reference(ref_by_id, event)
        by_id[str(event_id)] = _project(ref, event) if ref else deepcopy(dict(event))
    return {
        "by_id": by_id,
        "regime_transition_ids": [eid for eid in candidate.get("regime_transition_ids", []) if eid in by_id],
        "technical_cross_ids": [eid for eid in candidate.get("technical_cross_ids", []) if eid in by_id],
        "technical_cross_policy": deepcopy(reference.get("technical_cross_policy", {})),
    }


def _technical_analysis(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    dynamic = deepcopy(dict(candidate))
    dynamic.update({
        "analysis_id": "realized_volatility_technical_analysis",
        "source_chart_id": "realized_volatility_7d",
        "source_path": "charts.volatility_comparison.candles",
        "source_market": "general",
        "source_timeframe": "1d",
        "recalculate_in_hmi": False,
    })
    aligned = _project(reference, dynamic)
    # Keep runtime TA events; project each against matching Screen-SP variants.
    ref_events = reference.get("events", [])
    runtime_events = candidate.get("events", []) if isinstance(candidate.get("events"), list) else []
    projected = []
    for event in runtime_events:
        if not isinstance(event, Mapping):
            continue
        desired = next((r for r in ref_events if isinstance(r, Mapping) and r.get("event_group") == event.get("event_group") and ("indicator_id" in r) == ("indicator_id" in event)), None)
        desired = desired or (ref_events[0] if ref_events else {})
        projected.append(_project(desired, event) if desired else deepcopy(dict(event)))
    aligned["events"] = projected
    aligned["recalculate_in_hmi"] = False
    return aligned


def _quality(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any], dynamic: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    pq = processing.get("quality", {})
    cq = classification.get("quality", {})
    status = "available" if pq.get("status") == "ok" and cq.get("status") == "ok" else "partial" if pq.get("status") != "invalid" and cq.get("status") != "invalid" else "invalid"
    availability = {
        "kpis.current_regime": dynamic["kpis"]["items"][0].get("status"),
        "kpis.confidence": dynamic["kpis"]["items"][1].get("status"),
        "kpis.spread_7d": dynamic["kpis"]["items"][2].get("status"),
        "kpis.persistence": dynamic["kpis"]["items"][3].get("status"),
        "charts.positioning_ratio": dynamic["charts"]["positioning_ratio"].get("status"),
        "charts.volatility_comparison": dynamic["charts"]["volatility_comparison"].get("status"),
        "charts.regime_timeline": "available",
        "charts.regime_distribution": dynamic["charts"]["regime_distribution"].get("status"),
        "tables.market_regime_table": dynamic["tables"]["market_regime_table"].get("status"),
        "widgets.source_status": dynamic["widgets"]["source_status"].get("status"),
        "technical_analysis": dynamic["technical_analysis"].get("indicators", {}).get("rsi", {}).get("status", "unavailable"),
    }
    out.update({
        "status": status, "contract_complete": status != "invalid", "data_complete": status == "available",
        "availability": availability, "missing_fields": [],
        "warnings": deepcopy(pq.get("warnings", [])) + deepcopy(cq.get("warnings", [])),
        "errors": deepcopy(pq.get("errors", [])) + deepcopy(cq.get("errors", [])),
    })
    ext = out.get("extensions", {})
    history_count = len(processing.get("features", {}).get("technical_analysis", {}).get("candles", []))
    if "calculation_history_730_v1" in ext:
        ext["calculation_history_730_v1"].update({
            "realized_volatility_records": history_count,
            "positioning_ratio_records": len(dynamic["charts"]["positioning_ratio"].get("records", [])),
            "all_ma_series_warm": history_count >= 200,
            "recalculate_in_hmi": False,
        })
    if "realism_v1" in ext:
        anchor = processing.get("context", {}).get("reference_timestamp")
        ext["realism_v1"].update({
            "fixture_as_of_timestamp": anchor if processing.get("context", {}).get("data_mode") != "live" else None,
            "fixture_as_of_iso": _iso(anchor) if processing.get("context", {}).get("data_mode") != "live" else None,
            "synthetic_not_live": processing.get("context", {}).get("data_mode") != "live",
            "realized_volatility_records": history_count,
            "positioning_records": len(dynamic["charts"]["positioning_ratio"].get("records", [])),
            "common_as_of_contract": True,
        })
    out["extensions"] = ext
    return out


def align_volatility_market_regimes_to_sp_v1_2(
    candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any],
    *, runtime_context: Mapping[str, Any], selected_range: str,
) -> dict[str, Any]:
    reference = _template()
    selected = selected_range.lower()
    if selected not in DISPLAY_RANGES:
        raise ValueError("selected_range:invalid")

    # Candidate charts/tables already contain Classification-derived regime semantics.
    regime_distribution = deepcopy(candidate.get("charts", {}).get("regime_distribution", {}))
    market_table = deepcopy(candidate.get("tables", {}).get("market_regime_table", {}))
    events = _events(reference["events"], candidate.get("events", {}))
    technical_analysis = _technical_analysis(reference["technical_analysis"], candidate.get("technical_analysis", {}))
    dynamic: dict[str, Any] = {
        "family": "volatility_market_regimes",
        "screen": "volatility_market_regimes",
        "schema_version": SP_SCHEMA_VERSION,
        "context": _context(reference["context"], processing, runtime_context, selected),
        "badges": deepcopy(reference["badges"] if runtime_context.get("is_demo") else []),
        "selectors": deepcopy(reference["selectors"]),
        "kpis": _kpis(reference["kpis"], candidate, processing, classification, runtime_context),
        "charts": {
            "positioning_ratio": _positioning_chart(reference["charts"]["positioning_ratio"], processing, classification),
            "volatility_comparison": _volatility_chart(reference["charts"]["volatility_comparison"], processing),
            "regime_distribution": _project(reference["charts"]["regime_distribution"], regime_distribution),
        },
        "tables": {"market_regime_table": _project(reference["tables"]["market_regime_table"], market_table)},
        "widgets": {"source_status": _source_status(reference["widgets"]["source_status"], processing, classification)},
        "events": events,
        "technical_analysis": technical_analysis,
        "screen_layout": deepcopy(reference["screen_layout"]),
        "history_contract": deepcopy(reference["history_contract"]),
    }
    dynamic["selectors"]["display_range"]["selected"] = selected.upper()
    dynamic["selectors"]["display_range"]["options"] = [item.upper() for item in DISPLAY_RANGES]
    history_count = len(processing.get("features", {}).get("technical_analysis", {}).get("candles", []))
    dynamic["history_contract"].update({
        "calculation_records": history_count,
        "all_visible_moving_averages_warm": history_count >= 200,
        "technical_indicators_precomputed": True,
        "hmi_recalculation": False,
        "synthetic_fixture": bool(runtime_context.get("is_demo")),
        "fixture_seed": 20260807 if runtime_context.get("is_demo") else None,
        "resolution": "1h",
        "note": f"{history_count} realized-volatility candles and {len(dynamic['charts']['positioning_ratio'].get('records', []))} positioning-ratio observations.",
    })
    dynamic["quality"] = _quality(reference["quality"], processing, classification, dynamic)
    aligned = _project(reference, dynamic)
    # Preserve runtime event dictionaries/arrays after projection; event IDs are data, not SP constants.
    aligned["events"] = events
    aligned["technical_analysis"] = technical_analysis
    aligned["schema_version"] = SP_SCHEMA_VERSION
    json.dumps(aligned, ensure_ascii=False, allow_nan=False)
    return aligned
