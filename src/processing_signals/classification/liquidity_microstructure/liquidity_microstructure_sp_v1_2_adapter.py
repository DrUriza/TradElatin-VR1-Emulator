from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

SP_VERSION = "1.2.0"
SP_TEMPLATE_PATH = Path(__file__).with_name("liquidity_microstructure_screen_sp_v1_2.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _shape(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project runtime values onto the exact frozen SP key/nesting topology."""
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {key: _shape(value, source.get(key, _MISSING)) for key, value in reference.items()}
    if isinstance(reference, list):
        if candidate is _MISSING:
            return deepcopy(reference)
        if not isinstance(candidate, list):
            return []
        if not reference:
            return deepcopy(candidate)
        if all(not isinstance(item, (dict, list)) for item in reference):
            return deepcopy(candidate)
        if not candidate:
            return []
        identity_keys = ("metric_id", "event_id", "id", "provider_id", "side", "panel_id")

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

        return [_shape(ref_for(item, index), item) for index, item in enumerate(candidate)]
    return deepcopy(reference if candidate is _MISSING else candidate)


def _iso(timestamp: Any) -> str | None:
    if type(timestamp) is not int or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _event_row(row: Mapping[str, Any], reference_timestamp: int | None) -> dict[str, Any]:
    timestamp = row.get("timestamp")
    age = row.get("age_seconds")
    if age is None and type(timestamp) is int and type(reference_timestamp) is int:
        age = max(0, reference_timestamp - timestamp)
    quantity = _finite(row.get("quantity_base"))
    price = _finite(row.get("price"))
    notional = _finite(row.get("notional_quote", row.get("volume_usd")))
    if quantity is None and price and notional is not None:
        quantity = notional / price
    return {
        "event_id": str(row.get("event_id", "")),
        "timestamp": timestamp,
        "age_seconds": age,
        "side": row.get("side"),
        "price": price,
        "quantity_base": quantity,
        "notional_quote": notional,
        "distance_percent": row.get("distance_percent"),
    }


def _whale_row(row: Mapping[str, Any], reference_timestamp: int | None) -> dict[str, Any]:
    event = _event_row(row, reference_timestamp)
    event.update({
        "first_seen_timestamp": row.get("first_seen_timestamp"),
        "last_seen_timestamp": row.get("last_seen_timestamp", row.get("timestamp")),
        "order_state": row.get("order_state", "unknown"),
    })
    return event


def _profile(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cumulative = {"buy": 0.0, "sell": 0.0}
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: abs(float(item.get("distance_percent") or 0.0))):
        side = str(row.get("side"))
        if side not in cumulative:
            continue
        cumulative[side] += float(row.get("quantity_base") or 0.0)
        output.append({**deepcopy(row), "cumulative_quantity_base": cumulative[side]})
    return output


def _availability(candidate: Mapping[str, Any], template: Mapping[str, Any]) -> dict[str, Any]:
    charts = candidate.get("charts", {})
    tables = candidate.get("tables", {})
    widgets = candidate.get("widgets", {})
    drilldowns = candidate.get("drilldowns", {})
    kpis = {row.get("metric_id"): row for row in candidate.get("kpis", {}).get("items", []) if isinstance(row, Mapping)}

    def component(path: str) -> Mapping[str, Any]:
        head, _, tail = path.partition(".")
        if head == "kpis": return kpis.get(tail, {})
        if head == "charts": return charts.get(tail, {})
        if head == "tables": return tables.get(tail, {})
        if head == "widgets": return widgets.get(tail, {})
        if head == "drilldowns": return drilldowns.get(tail, {})
        return {}

    result = {"required": {}, "optional": {}}
    for group in ("required", "optional"):
        for path, ref in template["availability"][group].items():
            node = component(path)
            status = node.get("status", "unavailable")
            reason = node.get("reason") if status != "available" else None
            result[group][path] = {
                "status": status,
                "reason": reason,
                "source_paths": deepcopy(node.get("source_paths", ref.get("source_paths", []))),
            }
    result["summary"] = {
        "required_available": sum(v["status"] == "available" for v in result["required"].values()),
        "required_total": len(result["required"]),
        "optional_available": sum(v["status"] == "available" for v in result["optional"].values()),
        "optional_total": len(result["optional"]),
    }
    return result


def align_liquidity_microstructure_to_sp_v1_2(
    candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    ref = _template()
    out = deepcopy(dict(candidate))
    reference_timestamp = processing.get("reference_timestamp")
    selected_market = out.get("context", {}).get("selected_market", "perpetual")
    selected_timeframe = out.get("context", {}).get("selected_timeframe", "1m")

    out["schema"] = {"id": ref["schema"]["id"], "version": SP_VERSION}
    out["screen"] = deepcopy(ref["screen"])
    out["stage"] = "screen_contract"

    context = deepcopy(out.get("context", {}))
    context.update({
        "fixture_as_of_timestamp": reference_timestamp if runtime_context.get("is_demo") else None,
        "fixture_as_of_iso": _iso(reference_timestamp) if runtime_context.get("is_demo") else None,
        "synthetic_fixture": bool(runtime_context.get("is_demo")),
        "realism_refactor_version": "runtime_v1_2",
        "realism_note": "CoinGlass orderbook + large-limit-order + footprint runtime vertical; no HMI reconstruction.",
        "limitations": [
            "coinglass_only", "no_glassnode", "no_cryptoquant", "range_10_is_not_full_book",
            "observed_conditions_not_global_absolute_liquidity", "provider_is_coinglass_exchange_is_binance",
            "whale_orders_from_large_limit_order_endpoint",
            "executed_liquidity_is_footprint_price_bin_aggregation_not_individual_trade_tape",
        ],
    })
    out["context"] = context
    out["selectors"] = {"market": deepcopy(out.get("selectors", {}).get("market", ref["selectors"]["market"]))}

    current_book = processing.get("markets", {}).get(selected_market, {}).get("orderbook", {}).get("timeframes", {}).get(selected_timeframe, {}).get("current") or {}
    bids = deepcopy(current_book.get("bid_levels", []))
    asks = deepcopy(current_book.get("ask_levels", []))
    order_chart = deepcopy(out.get("charts", {}).get("order_depth", {}))
    order_chart["subtitle"] = ref["charts"]["order_depth"]["subtitle"]
    order_chart["records"] = [{"side": "bid", **row} for row in bids] + [{"side": "ask", **row} for row in asks]
    order_chart.setdefault("metadata", {}).update({
        "semantic_sides": ["bid", "ask"], "legend_labels": {"bid": "Bids", "ask": "Asks"},
        "bid_level_count": len(bids), "ask_level_count": len(asks), "total_level_count": len(bids) + len(asks),
    })

    profiles = processing.get("features", {}).get("profiles", {}).get(selected_market, {}).get(selected_timeframe, {})
    whale_rows = [_whale_row(row, reference_timestamp) for row in profiles.get("whale_orders", [])]
    whale_profile = _profile(whale_rows)
    executed_rows = [_event_row(row, reference_timestamp) for row in profiles.get("executed_liquidity_profile", [])]
    executed_profile = _profile(executed_rows)

    charts = deepcopy(out.get("charts", {}))
    charts["order_depth"] = order_chart
    for key, rows, semantic, title in (
        ("whale_liquidity_profile", whale_profile, "large_limit_order_liquidity", "WHALE LIQUIDITY PROFILE"),
        ("executed_liquidity_profile", executed_profile, "executed_footprint_price_bins", "EXECUTED LIQUIDITY PROFILE"),
    ):
        chart = deepcopy(charts.get(key, {}))
        chart["title"] = title
        chart["subtitle"] = ref["charts"][key]["subtitle"]
        chart["status"] = "available" if rows else "unavailable"
        chart["reason"] = None if rows else "source_not_available"
        chart["records"] = rows
        chart["data_as_of"] = max((r.get("timestamp") for r in rows if type(r.get("timestamp")) is int), default=None)
        chart["metadata"] = {
            "scope": "single_exchange_runtime", "semantic_sides": ["buy", "sell"],
            "legend_labels": {"buy": "Buy", "sell": "Sell"}, "x_axis": {"field": "distance_percent"},
            "basis": "cumulative_quantity_base", "unit": "BTC", "cumulative_origin": "mid_price",
            "required_record_fields": list(ref["charts"][key]["metadata"]["required_record_fields"]),
            "note": semantic, "events_available": len(rows), "events_returned": len(rows), "event_count": len(rows),
        }
        if key == "executed_liquidity_profile":
            chart["metadata"].update({"selected_window": selected_timeframe, "profile_normalized": True})
        charts[key] = chart

    # The final SP keeps the historical key ``large_trades_flow``; runtime data
    # are CoinGlass footprint price-bin executions, not an invented individual tape.
    tape_rows = sorted([_event_row(row, reference_timestamp) for row in processing.get("markets", {}).get(selected_market, {}).get("large_trades", {}).get("large_trade_events", [])],
                       key=lambda row: row.get("timestamp") or 0, reverse=True)
    flow = deepcopy(charts.get("large_trades_flow", {}))
    flow["items"] = tape_rows
    flow["status"] = "available" if tape_rows else "unavailable"
    flow["reason"] = None if tape_rows else "source_not_available"
    flow["data_as_of"] = max((r.get("timestamp") for r in tape_rows if type(r.get("timestamp")) is int), default=None)
    flow["metadata"] = {
        "window_semantics": "footprint_price_bins", "selected_window": selected_timeframe,
        "observed_first_timestamp": min((r.get("timestamp") for r in tape_rows if type(r.get("timestamp")) is int), default=None),
        "observed_last_timestamp": max((r.get("timestamp") for r in tape_rows if type(r.get("timestamp")) is int), default=None),
        "observed_span_seconds": 0 if len(tape_rows) < 2 else max(r["timestamp"] for r in tape_rows) - min(r["timestamp"] for r in tape_rows),
        "coverage_complete": bool(tape_rows), "reason": None if tape_rows else "source_not_available",
        "records_available": len(tape_rows), "display_limit": len(tape_rows),
    }
    charts["large_trades_flow"] = flow

    # Preserve real provider temporal history explicitly.  Do not let the SP
    # fixture populate calculation-history records when an upstream series is
    # absent.  The display series may be truncated, while calculation history
    # retains up to the frozen 730-record analysis window.
    whale_source = (processing.get("whale_activity", {}).get("timeframes", {}).get(selected_timeframe, {}).get("records", []) or [])
    whale_calc_records = deepcopy(list(whale_source)[-730:])
    whale_chart = deepcopy(charts.get("whale_activity", {}))
    whale_chart["calculation_history"] = {"record_count": len(whale_calc_records), "records": whale_calc_records}
    whale_chart.setdefault("metadata", {}).update({
        "events_available": len(whale_source),
        "events_returned": len(whale_chart.get("records", [])),
    })
    charts["whale_activity"] = whale_chart

    market_source = processing.get("market_history", {}).get("records", []) or []
    market_calc_records = deepcopy(list(market_source)[-730:])
    market_chart = deepcopy(charts.get("market_history", {}))
    market_chart["calculation_history"] = {"record_count": len(market_calc_records), "records": market_calc_records}
    market_chart.setdefault("metadata", {}).update({
        "records_available": len(market_source),
        "records_returned": len(market_chart.get("records", [])),
        "history_truncated": len(market_source) > len(market_chart.get("records", [])),
    })
    charts["market_history"] = market_chart
    out["charts"] = charts

    tables = deepcopy(out.get("tables", {}))
    ob_table = deepcopy(tables.get("orderbook_snapshot", {}))
    ob_table["bids"] = bids; ob_table["asks"] = asks
    ob_table["subtitle"] = ref["tables"]["orderbook_snapshot"]["subtitle"]
    ob_table["display_columns"] = deepcopy(ref["tables"]["orderbook_snapshot"]["display_columns"])
    ob_table.setdefault("metadata", {}).update({
        "rows_available": len(bids) + len(asks), "rows_returned": len(bids) + len(asks), "rows_truncated": False,
        "display_limit": max(len(bids), len(asks)), "scroll_if_more_rows": True, "visual_rows_target": min(16, max(len(bids), len(asks))),
        "bid_level_count": len(bids), "ask_level_count": len(asks),
    })
    tables["orderbook_snapshot"] = ob_table

    whale_table = deepcopy(tables.get("whale_orders", {}))
    whale_table["rows"] = sorted(whale_rows, key=lambda row: row.get("timestamp") or 0, reverse=True)
    whale_table["status"] = "available" if whale_rows else "unavailable"
    whale_table["reason"] = None if whale_rows else "source_not_available"
    whale_table["display_columns"] = deepcopy(ref["tables"]["whale_orders"]["display_columns"])
    whale_table["summary"] = {
        "buy": sum(1 for r in whale_rows if r.get("side") == "buy"),
        "sell": sum(1 for r in whale_rows if r.get("side") == "sell"),
    }
    whale_table["metadata"] = {
        "events_available": len(whale_rows), "events_returned": len(whale_rows), "events_truncated": False,
        "display_limit": len(whale_rows), "age_semantics": "reference_timestamp_minus_last_seen",
        "side_semantics": "provider_order_side", "required_row_fields": deepcopy(ref["tables"]["whale_orders"]["metadata"]["required_row_fields"]),
        "note": "CoinGlass Large Orderbook active orders", "fixture_note": "runtime_provider_feed",
        "scroll_if_more_rows": True, "visual_rows_target": min(16, len(whale_rows)), "records_available": len(whale_rows),
    }
    tables["whale_orders"] = whale_table

    trade_table = deepcopy(tables.get("large_trades", {}))
    trade_table["rows"] = tape_rows
    trade_table["status"] = "available" if tape_rows else "unavailable"
    trade_table["reason"] = None if tape_rows else "source_not_available"
    trade_table["display_columns"] = deepcopy(ref["tables"]["large_trades"]["display_columns"])
    buy = sum(float(r.get("notional_quote") or 0) for r in tape_rows if r.get("side") == "buy")
    sell = sum(float(r.get("notional_quote") or 0) for r in tape_rows if r.get("side") == "sell")
    trade_table["summary"] = {"buy": buy, "sell": sell, "net_flow_usd": buy - sell, "window": selected_timeframe,
                              "status": trade_table["status"], "reason": trade_table["reason"]}
    trade_table["metadata"] = {
        "events_available": len(tape_rows), "events_returned": len(tape_rows), "events_truncated": False,
        "observed_first_timestamp": min((r.get("timestamp") for r in tape_rows if type(r.get("timestamp")) is int), default=None),
        "observed_last_timestamp": max((r.get("timestamp") for r in tape_rows if type(r.get("timestamp")) is int), default=None),
        "observed_span_seconds": 0 if len(tape_rows) < 2 else max(r["timestamp"] for r in tape_rows) - min(r["timestamp"] for r in tape_rows),
        "coverage_complete": bool(tape_rows), "reason": None if tape_rows else "source_not_available",
        "side_semantics": "taker_buy_sell_footprint", "required_row_fields": deepcopy(ref["tables"]["large_trades"]["metadata"]["required_row_fields"]),
        "fixture_note": "runtime_coinGlass_footprint_bins", "display_limit": len(tape_rows), "scroll_if_more_rows": True,
        "visual_rows_target": min(16, len(tape_rows)), "records_available": len(tape_rows),
    }
    tables["large_trades"] = trade_table
    out["tables"] = tables

    out["layout"] = deepcopy(ref["layout"])
    market_history_chart = out.get("charts", {}).get("market_history", {})
    whale_activity_chart = out.get("charts", {}).get("whale_activity", {})
    market_history_records = max(
        len(market_history_chart.get("records", [])),
        len(market_history_chart.get("calculation_history", {}).get("records", [])),
    )
    whale_activity_records = max(
        len(whale_activity_chart.get("records", [])),
        len(whale_activity_chart.get("calculation_history", {}).get("records", [])),
    )
    # Liquidity has two independent temporal contexts.  Visible chart records
    # may be presentation-truncated while calculation_history retains the full
    # provider history.  Report the longest real upstream calculation history;
    # quality/availability still exposes market_history as unavailable when it
    # is absent rather than synthesizing it.
    temporal_records = max(market_history_records, whale_activity_records)
    out["history_contract"] = {
        "calculation_records": temporal_records,
        "minimum_warmup_records": 200,
        "maximum_standard_indicator_period": 200,
        "all_visible_moving_averages_warm": temporal_records >= 200,
        "technical_indicators_precomputed": True,
        "hmi_recalculation": False,
        "synthetic_fixture": bool(runtime_context.get("is_demo")),
        "fixture_seed": 20260807 if runtime_context.get("is_demo") else None,
        "resolution": selected_timeframe,
        "note": "Temporal context is upstream-owned; orderbook/order-event snapshots remain family-specific.",
    }
    # Liquidity has no Screen B.  Publish the frozen negative capability
    # explicitly so HMI does not infer or render a technical-analysis view.
    out["technical_analysis"] = {
        "enabled": False,
        "applies": False,
        "screen_b": False,
        "reason": "not_applicable_by_vr1_contract",
        "hmi_must_not_render_analysis_button": True,
        "hmi_recalculation": False,
    }

    out["availability"] = _availability(out, ref)
    required_statuses = {k: v["status"] for k, v in out["availability"]["required"].items()}
    invalid = sorted(k for k, v in required_statuses.items() if v == "invalid")
    partial = sorted(k for k, v in required_statuses.items() if v == "partial")
    unavailable = sorted(k for k, v in required_statuses.items() if v == "unavailable")
    quality_status = "invalid" if invalid else "partial" if partial or unavailable else "ok"
    out["quality"] = {
        "status": quality_status, "contract_complete": True, "data_complete": not partial and not unavailable,
        "processing_status": processing.get("quality", {}).get("status"), "classification_status": classification.get("quality", {}).get("status"),
        "availability": deepcopy(out["availability"]["summary"]), "missing_required_components": unavailable,
        "partial_components": partial, "unavailable_components": unavailable, "invalid_components": invalid,
        "warnings": (["executed_liquidity_is_footprint_price_bin_aggregation"] if tape_rows else ["executed_liquidity_footprint_unavailable"]),
        "errors": [], "data_as_of": out.get("context", {}).get("data_as_of"),
        "extensions": {
            "visual_fixture_population": {"status": "available", "orderbook_levels_each_side": max(len(bids), len(asks)),
                "whale_order_events": len(whale_rows), "large_trade_events": len(tape_rows), "synthetic": bool(runtime_context.get("is_demo"))},
            "dense_demo_liquidity_fixture": {"status": "available", "orderbook_levels_per_side": max(len(bids), len(asks)),
                "whale_order_events": len(whale_rows), "large_trade_events": len(tape_rows), "synthetic": bool(runtime_context.get("is_demo")),
                "purpose": "visual_contract_and_hmi_density_validation"},
            "global_control_visibility_v1": {"view_selector_visible": False, "market_selector_visible": False, "state_preserved_internally": True},
            "liquidity_table_density_v2": {"status": "available", "orderbook_visible_rows_per_side": min(16, max(len(bids), len(asks))),
                "whale_orders_visible_rows": min(16, len(whale_rows)), "large_trades_visible_rows": min(16, len(tape_rows)),
                "scroll_if_more_rows": True, "hmi_truncation_source": "table.metadata.display_limit"},
            "large_trades_dense_tape_v2": {"events": len(tape_rows), "visible_rows": min(22, len(tape_rows)), "scroll_enabled": True,
                "synthetic": bool(runtime_context.get("is_demo"))},
            "canonical_590_scope": {"applies": False, "reason": "No technical-selector column is present in this family; its accepted family-specific geometry is preserved."},
            "temporal_history_730_v1": {"status": "available" if temporal_records else "partial", "temporal_snapshots": temporal_records,
                "temporal_fields": deepcopy(ref["quality"]["extensions"]["temporal_history_730_v1"]["temporal_fields"]),
                "snapshot_structures_not_forced_to_730": deepcopy(ref["quality"]["extensions"]["temporal_history_730_v1"]["snapshot_structures_not_forced_to_730"])},
            "selector_cleanup_v2": {"timeframe_selector_removed": True, "reason": "snapshot_and_temporal_context_are_contract_driven"},
            "temporal_selector_contract_v3": {"selector_type": "NONE", "timeframe_selector_visible": False, "range_selector_visible": False, "refresh_mode": "manual"},
            "realism_v1": {"status": "available", "fixture_as_of_timestamp": reference_timestamp, "fixture_as_of_iso": _iso(reference_timestamp),
                "deterministic_seed": 20260807 if runtime_context.get("is_demo") else None, "synthetic_not_live": bool(runtime_context.get("is_demo")),
                "orderbook_levels_per_side": max(len(bids), len(asks)), "whale_orders": len(whale_rows), "large_trades": len(tape_rows),
                "temporal_history_records": temporal_records, "common_as_of_contract": _iso(reference_timestamp)},
        },
    }

    shaped = _shape(ref, out)
    # This VR1-final negative capability postdates the SP 1.2 fixture used to
    # shape runtime fields, so append it after projection.
    shaped["technical_analysis"] = deepcopy(out["technical_analysis"])
    json.dumps(shaped, ensure_ascii=False, allow_nan=False)
    return shaped
