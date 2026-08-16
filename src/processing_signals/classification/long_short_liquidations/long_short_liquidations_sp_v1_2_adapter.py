from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

SP_VERSION = "1.2.0"
SP_TEMPLATE_PATH = Path(__file__).with_name("long_short_liquidations_screen_sp_v1_2.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return 0.0 if value == 0 else float(value)


def _iso(timestamp: Any) -> str | None:
    if type(timestamp) is not int or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _shape(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project runtime values onto the exact frozen SP key/nesting shape."""
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {key: _shape(value, source.get(key, _MISSING)) for key, value in reference.items()}
    if isinstance(reference, list):
        if candidate is _MISSING:
            return deepcopy(reference)
        if not isinstance(candidate, list):
            return deepcopy(reference)
        if not reference:
            return deepcopy(candidate)
        if all(not isinstance(item, (dict, list)) for item in reference):
            return deepcopy(candidate)
        identity_keys = ("id", "provider", "series_id", "exchange", "badge_id")

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


def _kpis(reference: list[Any], candidate: list[Any]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): deepcopy(item) for item in candidate if isinstance(item, Mapping)}
    output: list[dict[str, Any]] = []
    for ref in reference:
        identifier = str(ref.get("id"))
        item = deepcopy(by_id.get(identifier, {}))
        item.setdefault("id", identifier)
        item.setdefault("label", ref.get("label"))
        if identifier in {"total_liquidations_24h", "long_liquidations_24h", "short_liquidations_24h"}:
            value = _finite(item.get("value"))
            if value is not None:
                item["value"] = value / 1_000_000.0
                item["display_value"] = f"${item['value']:.1f}M"
                item["unit"] = "M USD"
        elif identifier == "current_price":
            value = _finite(item.get("value"))
            if value is not None:
                item["display_value"] = f"${value:,.0f}"
                item["unit"] = "USDT"
        elif identifier == "realized_imbalance_24h":
            value = _finite(item.get("value"))
            if value is not None:
                item["display_value"] = f"{value*100:+.1f}%"
                item["unit"] = "ratio"
        elif identifier == "realized_side_24h":
            classification = item.get("classification")
            side = {
                "realized_long_liquidations_dominant": "LONGS",
                "realized_short_liquidations_dominant": "SHORTS",
                "realized_balanced": "BALANCED",
            }.get(classification)
            item.update({"value": side, "display_value": side or "—", "unit": "side"})
        output.append(_shape(ref, item))
    return output


def _filter_aggregate_chart(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(candidate))
    allowed = {"binance", "okx", "bybit"}
    item["bar_series"] = [row for row in item.get("bar_series", []) if str(row.get("series_id", "")).lower() in allowed]
    buckets = []
    for row in item.get("buckets", []):
        if not isinstance(row, Mapping):
            continue
        copied = deepcopy(dict(row))
        copied["bars"] = {k: v for k, v in copied.get("bars", {}).items() if str(k).lower() in allowed}
        buckets.append(copied)
    item["buckets"] = buckets
    item["provider"] = "coinglass"
    item["unit"] = "provider_level"
    item.setdefault("axes", {}).setdefault("bar_axis", {})["unit"] = "provider_level"
    provenance = item.get("provenance") if isinstance(item.get("provenance"), Mapping) else {}
    item["provenance"] = {
        "provider": "coinglass",
        "endpoint_id": provenance.get("endpoint_id", "aggregated_liquidation_map"),
        "source_dataset": provenance.get("source_dataset", "coinglass.aggregated_map"),
        "source_snapshot_timestamp": provenance.get("source_snapshot_timestamp"),
        "side_assignment_method": provenance.get("side_assignment_method", "spatial_convention_v1"),
    }
    item.setdefault("visual_contract", {})["bucket_count"] = len(buckets)
    return _shape(reference, item)


def _exchange_chart(reference: Mapping[str, Any], candidate: Mapping[str, Any], *, exchange: str) -> dict[str, Any]:
    item = deepcopy(dict(candidate))
    item["unit"] = "provider_level"
    item.setdefault("axes", {}).setdefault("bar_axis", {})["unit"] = "provider_level"
    item.setdefault("visual_contract", {})["bucket_count"] = len(item.get("buckets", []))
    if exchange == "Hyperliquid":
        item["proxy"] = False
        item["provenance"] = {
            "provider": "coinglass",
            "source_dataset": "coinglass.pair_maps.Hyperliquid",
            "proxy": False,
        }
    else:
        provenance = item.get("provenance") if isinstance(item.get("provenance"), Mapping) else {}
        item["provenance"] = {
            "provider": "coinglass",
            "source_dataset": "coinglass.pair_maps.Binance",
            "side_assignment_method": provenance.get("side_assignment_method", "spatial_convention_v1"),
        }
    return _shape(reference, item)


def _exchange_distribution_table(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(candidate))
    rows = [deepcopy(dict(row)) for row in item.get("rows", []) if isinstance(row, Mapping)
            and str(row.get("exchange_key", "")).lower() not in {"all", "all_exchange", "aggregate"}]
    item["rows"] = rows
    return _shape(reference, item)


def _side_panel(reference: Mapping[str, Any], candidate: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    kpis = {str(item.get("id")): item for item in candidate.get("kpis", []) if isinstance(item, Mapping)}
    pressure = kpis.get("pressure_score", {})
    imbalance = kpis.get("realized_imbalance_24h", {})
    side = kpis.get("realized_side_24h", {})
    classification = pressure.get("classification")
    pressure_state = {
        "low_pressure": ("low", "Baja"),
        "moderate_pressure": ("moderate", "Moderada"),
        "high_pressure": ("high", "Alta"),
        "extreme_pressure": ("extreme", "Extrema"),
    }.get(classification, (None, "—"))
    realized_side = {
        "realized_long_liquidations_dominant": "LONGS",
        "realized_short_liquidations_dominant": "SHORTS",
        "realized_balanced": "BALANCED",
    }.get(side.get("classification"))

    rows = candidate.get("tables", {}).get("exchange_distribution", {}).get("rows", [])
    exchange_rows = [row for row in rows if isinstance(row, Mapping) and str(row.get("exchange_key", "")).lower() != "all"]
    total = sum(float(row.get("computed_total_usd", 0) or 0) for row in exchange_rows)
    top_share = max((float(row.get("computed_total_usd", 0) or 0) / total for row in exchange_rows), default=None) if total > 0 else None

    event24 = processing.get("events", {}).get("aggregate", {}).get("24h", {})
    event1h = processing.get("events", {}).get("aggregate", {}).get("1h", {})
    max_event = event24.get("max_event") if isinstance(event24.get("max_event"), Mapping) else None
    max_event_usd = _finite(max_event.get("usd_value")) if max_event else None
    spike_usd = _finite(event1h.get("event_usd_total"))

    agg = processing.get("maps", {}).get("aggregated", {})
    clusters = agg.get("clusters", {}) if isinstance(agg, Mapping) else {}
    long_clusters = clusters.get("estimated_long", []) if isinstance(clusters, Mapping) else []
    short_clusters = clusters.get("estimated_short", []) if isinstance(clusters, Mapping) else []

    def cluster_price(rows: Any) -> float | None:
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        if not isinstance(row, Mapping):
            return None
        for key in ("center_price", "price", "cluster_center_price"):
            value = _finite(row.get(key))
            if value is not None:
                return value
        return None

    long_price = cluster_price(long_clusters)
    short_price = cluster_price(short_clusters)
    score = _finite(pressure.get("value"))
    side_imbalance = _finite(imbalance.get("value"))

    dynamic = {
        "pressure_score": {"id": "pressure_score", "label": "Pressure Score", "value": score,
                           "display_value": f"{score:.2f}" if score is not None else "—", "unit": "score",
                           "status": pressure.get("status", "unavailable")},
        "pressure_label": {"id": "pressure_label", "label": "Pressure Label", "value": pressure_state[0],
                           "display_value": pressure_state[1], "unit": "state",
                           "status": pressure.get("status", "unavailable")},
        "dominant_side": {"id": "dominant_side", "label": "Dominant Side", "value": realized_side,
                          "display_value": realized_side or "—", "unit": "side", "status": side.get("status", "unavailable")},
        "side_imbalance": {"id": "side_imbalance", "label": "Side Imbalance", "value": side_imbalance,
                           "display_value": f"{side_imbalance:+.4f}" if side_imbalance is not None else "—", "unit": "ratio",
                           "status": imbalance.get("status", "unavailable")},
        "top_exchange_concentration": {"id": "top_exchange_concentration", "label": "Top Exchange Conc.", "value": top_share,
                           "display_value": f"{top_share:.4f}" if top_share is not None else "—", "unit": "ratio",
                           "status": "available" if top_share is not None else "unavailable"},
        "max_event_spike": {"id": "max_event_spike", "label": "Max Event Spike",
                           "value": spike_usd / 1_000_000.0 if spike_usd is not None else None,
                           "display_value": f"${spike_usd/1_000_000.0:.2f}M" if spike_usd is not None else "—", "unit": "M USD",
                           "status": event1h.get("status", "unavailable")},
        "max_single_liquidation": {"id": "max_single_liquidation", "label": "Max Single Liq.",
                           "value": max_event_usd / 1_000_000.0 if max_event_usd is not None else None,
                           "display_value": (f"{max_event.get('exchange')} ${max_event_usd/1_000_000.0:.2f}M" if max_event_usd is not None else "—"),
                           "unit": "M USD", "status": event24.get("status", "unavailable")},
        "nearest_long_cluster": {"id": "nearest_long_cluster", "label": "Nearest Long Cluster", "value": long_price,
                           "display_value": f"${long_price:,.0f}" if long_price is not None else "—", "unit": "USDT",
                           "status": "available" if long_price is not None else "unavailable"},
        "nearest_short_cluster": {"id": "nearest_short_cluster", "label": "Nearest Short Cluster", "value": short_price,
                           "display_value": f"${short_price:,.0f}" if short_price is not None else "—", "unit": "USDT",
                           "status": "available" if short_price is not None else "unavailable"},
    }
    items = [_shape(ref, dynamic.get(str(ref.get("id")), {})) for ref in reference.get("items", [])]
    return {"id": reference.get("id"), "title": reference.get("title"), "items": items}


def _selectors(reference: Mapping[str, Any], charts: Mapping[str, Any]) -> dict[str, Any]:
    hyper = charts.get("hyperliquid_map", {})
    binance = charts.get("binance_leverage_map", {})
    return {
        "exchange": {"selected": "aggregate", "options": ["aggregate", "Binance"]},
        "map": {"selected": "aggregate", "options": [
            {"id": "aggregate", "enabled": True},
            {"id": "hyperliquid", "enabled": hyper.get("status") in {"available", "partial"},
             "reason": None if hyper.get("status") in {"available", "partial"} else hyper.get("reason")},
            {"id": "binance", "enabled": binance.get("status") in {"available", "partial"},
             "reason": None if binance.get("status") in {"available", "partial"} else binance.get("reason")},
        ]},
    }


def _history(reference: Mapping[str, Any], processing: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    series = [row for row in processing.get("realized", {}).get("series", []) if isinstance(row, Mapping)]
    totals = [float(row.get("total_liquidation_usd", 0) or 0) for row in series]
    max_total = max(totals) if totals else 0.0
    current_price = _finite(processing.get("maps", {}).get("reference_price", {}).get("value"))
    agg = processing.get("maps", {}).get("aggregated", {})
    clusters = agg.get("clusters", {}) if isinstance(agg, Mapping) else {}

    def nearest_distance(side: str) -> float | None:
        if current_price is None or not isinstance(clusters, Mapping):
            return None
        rows = clusters.get(side, [])
        if not isinstance(rows, list) or not rows:
            return None
        values = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            price = _finite(row.get("center_price", row.get("price")))
            if price is not None:
                values.append(abs(price / current_price - 1.0) * 100.0)
        return min(values) if values else None

    long_distance = nearest_distance("estimated_long")
    short_distance = nearest_distance("estimated_short")
    records = []
    for row in series:
        long_v = _finite(row.get("long_liquidation_usd")) or 0.0
        short_v = _finite(row.get("short_liquidation_usd")) or 0.0
        total = long_v + short_v
        imbalance = 0.0 if total == 0 else (long_v - short_v) / total
        records.append({
            "timestamp": row.get("timestamp"), "price": current_price,
            "long_liquidation_intensity": 0.0 if max_total == 0 else long_v / max_total,
            "short_liquidation_intensity": 0.0 if max_total == 0 else short_v / max_total,
            "liquidation_imbalance": imbalance,
            "nearest_long_cluster_distance_percent": long_distance,
            "nearest_short_cluster_distance_percent": short_distance,
            "estimated_long_liquidation_usd": long_v,
            "estimated_short_liquidation_usd": short_v,
            "is_synthetic": bool(runtime_context.get("is_demo")),
        })
    calculation = {
        "record_count": len(records), "resolution": "1h", "records": records,
        "purpose": deepcopy(reference.get("calculation_history", {}).get("purpose", [])),
        "map_bucket_semantics": "Current maps are spatial price buckets; temporal selector intentionally absent.",
    }
    history = deepcopy(dict(reference.get("history_contract", {})))
    history.update({
        "calculation_records": len(records), "minimum_warmup_records": 200,
        "maximum_standard_indicator_period": 200,
        "all_visible_moving_averages_warm": len(records) >= 200,
        "technical_indicators_precomputed": True, "hmi_recalculation": False,
        "synthetic_fixture": bool(runtime_context.get("is_demo")),
        "fixture_seed": 20260807 if runtime_context.get("is_demo") else None,
        "resolution": "1h",
        "note": f"{len(records)} temporal liquidation-context snapshots; map buckets remain spatial price buckets.",
    })
    return _shape(reference.get("history_contract", {}), history), _shape(reference.get("calculation_history", {}), calculation)


def _quality(reference: Mapping[str, Any], candidate: Mapping[str, Any], charts: Mapping[str, Any], runtime_context: Mapping[str, Any], history_count: int) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    status = candidate.get("quality", {}).get("status", "unavailable")
    bucket_counts = {key: len(value.get("buckets", [])) for key, value in charts.items() if isinstance(value, Mapping)}
    missing = [name for name, value in charts.items() if value.get("status") not in {"available", "partial"}]
    out.update({
        "status": status,
        "synthetic_fixture": bool(runtime_context.get("is_demo")),
        "required_view_models": ["current_price", "aggregate_liquidation_map", "hyperliquid_map", "binance_leverage_map", "liquidation_target_summary"],
        "missing_view_models": missing,
        "warnings": deepcopy(candidate.get("quality", {}).get("warnings", [])),
        "errors": deepcopy(candidate.get("quality", {}).get("errors", [])),
        "visual_fixture": {"status": status, "synthetic": bool(runtime_context.get("is_demo")),
                           "bucket_count": max(bucket_counts.values(), default=0), "maps": list(charts), "hmi_calculation": False},
    })
    ext = deepcopy(out.get("extensions", {}))
    if "temporal_history_730_v1" in ext:
        ext["temporal_history_730_v1"].update({
            "status": "available" if history_count else "unavailable",
            "temporal_records": history_count,
            "map_bucket_count_per_map": bucket_counts,
            "buckets_are_spatial_not_temporal": True,
        })
    if "realism_v1" in ext:
        ext["realism_v1"].update({
            "status": "available" if status in {"available", "partial"} else status,
            "fixture_as_of_timestamp": candidate.get("reference_timestamp") if runtime_context.get("is_demo") else None,
            "fixture_as_of_iso": _iso(candidate.get("reference_timestamp")) if runtime_context.get("is_demo") else None,
            "deterministic_seed": 20260807 if runtime_context.get("is_demo") else None,
            "synthetic_not_live": bool(runtime_context.get("is_demo")),
            "bucket_count_per_map": max(bucket_counts.values(), default=0),
            "temporal_history_records": history_count,
            "temporal_selector": "NONE",
            "common_as_of_contract": _iso(candidate.get("reference_timestamp")),
        })
    out["extensions"] = ext
    shaped = _shape(reference, out)
    badge_state = {
        str(row.get("id")): bool(row.get("active"))
        for row in candidate.get("badges", [])
        if isinstance(row, Mapping)
    }
    shaped.setdefault("extensions", {})["event_badge_contract_v1"] = {
        "truncated_events": {
            "active": badge_state.get("truncated_events", False),
            "semantic": "true_when_event_stream_is_truncated",
            "processing_must_populate": True,
        },
        "lower_bound": {
            "active": badge_state.get("lower_bound", False),
            "semantic": "true_when_reported_liquidation_aggregate_is_only_a_lower_bound",
            "processing_must_populate": True,
        },
    }
    return shaped


def align_long_short_liquidations_to_sp_v1_2(
    candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    del classification
    ref = _template()
    is_demo = bool(runtime_context.get("is_demo"))
    context = deepcopy(dict(ref["context"]))
    context.update({
        "symbol": candidate.get("context", {}).get("symbol", "BTCUSDT"),
        "base_asset": candidate.get("context", {}).get("base_asset", "BTC"),
        "quote_asset": candidate.get("context", {}).get("quote_asset", "USDT"),
        "market": candidate.get("context", {}).get("market", "futures"),
        "price_precision": candidate.get("context", {}).get("price_precision", 2),
        "exchange_scope": "aggregate", "selected_interval": "1h",
        "available_intervals": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "fixture_as_of_timestamp": candidate.get("reference_timestamp") if is_demo else None,
        "fixture_as_of_iso": _iso(candidate.get("reference_timestamp")) if is_demo else None,
        "data_mode": runtime_context.get("data_mode"), "synthetic_fixture": is_demo,
        "realism_refactor_version": "runtime_emulator_v1" if is_demo else "runtime_provider_v1",
        "realism_note": "CoinGlass primary liquidation maps/history with Glassnode/CryptoQuant independent confirmation; HMI performs no liquidation calculations.",
    })

    charts = {
        "aggregate_map": _filter_aggregate_chart(ref["charts"]["aggregate_map"], candidate.get("charts", {}).get("aggregate_map", {})),
        "hyperliquid_map": _exchange_chart(ref["charts"]["hyperliquid_map"], candidate.get("charts", {}).get("hyperliquid_map", {}), exchange="Hyperliquid"),
        "binance_leverage_map": _exchange_chart(ref["charts"]["binance_leverage_map"], candidate.get("charts", {}).get("binance_leverage_map", {}), exchange="Binance"),
    }
    history_contract, calculation_history = _history(ref, processing, runtime_context)
    history_count = calculation_history.get("record_count", 0)

    source_selection = deepcopy(candidate.get("source_selection", {}))
    for name in ("exchange_maps", "realized_by_exchange", "events"):
        if name in source_selection and isinstance(source_selection[name], Mapping):
            status = source_selection[name].get("status")
            source_selection[name]["selected"] = status in {"available", "partial"}
    if "exchange_maps" in source_selection:
        source_selection["exchange_maps"]["role"] = "canonical"

    providers = deepcopy(candidate.get("providers", []))
    built = {
        "contract_version": SP_VERSION,
        "screen_id": "long_short_liquidations",
        "family": "long_short_liquidations",
        "stage": "screen_contract_final",
        "reference_timestamp": candidate.get("reference_timestamp"),
        "context": context,
        "timestamps": deepcopy(candidate.get("timestamps", {})),
        "mode": runtime_context.get("data_mode"),
        "header": deepcopy(candidate.get("header", {})),
        "kpis": _kpis(ref["kpis"], candidate.get("kpis", [])),
        "selectors": _selectors(ref["selectors"], charts),
        "charts": charts,
        "side_panel": _side_panel(ref["side_panel"], candidate, processing),
        "tables": {
            "exchange_distribution": _exchange_distribution_table(ref["tables"]["exchange_distribution"], candidate.get("tables", {}).get("exchange_distribution", {})),
            "provider_confirmations": _shape(ref["tables"]["provider_confirmations"], candidate.get("tables", {}).get("provider_confirmations", {})),
        },
        "badges": deepcopy(candidate.get("badges", [])),
        "providers": providers,
        "source_selection": _shape(ref["source_selection"], source_selection),
        "quality": {},
        "warnings": deepcopy(candidate.get("warnings", [])),
        "errors": deepcopy(candidate.get("errors", [])),
        "history_contract": history_contract,
        "calculation_history": calculation_history,
    }
    built["header"]["badges"] = deepcopy(built["badges"])
    built["header"]["status"] = candidate.get("quality", {}).get("status", "unavailable")
    built["quality"] = _quality(ref["quality"], candidate, charts, runtime_context, history_count)
    shaped = _shape(ref, built)
    shaped["quality"].setdefault("extensions", {})["event_badge_contract_v1"] = deepcopy(
        built["quality"]["extensions"]["event_badge_contract_v1"]
    )
    return shaped
