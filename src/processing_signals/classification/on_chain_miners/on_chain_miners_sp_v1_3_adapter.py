from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

SP_TEMPLATE_PATH = Path(__file__).with_name("on_chain_miners_screen_sp_v1_3.json")
SP_VERSION = "1.3.0"
RANGES = {"30D": 30, "90D": 90, "360D": 360}
PRIMARY = {
    "miner_reserve": "miner_reserve_btc",
    "sopr_7d": "sopr_7d",
    "hashrate": "hashrate_eh_s",
    "difficulty": "difficulty_t",
}
_MISSING = object()

PROVIDERS = {
    # These are display-source identities, not the complete confirmation set.
    # Glassnode is canonical for the four primary On-Chain/Miners charts;
    # CryptoQuant remains complementary in upstream provenance/classification.
    "miner_reserve": "glassnode",
    "sopr_7d": "glassnode",
    "hashrate": "glassnode",
    "difficulty": "glassnode",
}


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _finite(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return 0.0 if value == 0 else value


def _iso(timestamp: Any) -> str | None:
    if type(timestamp) is not int or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _display(value: Any, chart_id: str) -> str:
    value = _finite(value)
    if value is None:
        return "—"
    if chart_id == "miner_reserve":
        return f"{value / 1_000_000:.3f}M"
    if chart_id == "sopr_7d":
        return f"{value:.3f}"
    if chart_id == "hashrate":
        return f"{value:.1f} EH/s"
    if chart_id == "difficulty":
        return f"{value:.2f} T"
    if chart_id == "miner_net_position_change":
        return f"{value:+,.0f}"
    return str(value)


def _context(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    pctx = processing.get("context", {})
    out = deepcopy(dict(reference))
    data_as_of = min(
        x for x in (processing.get("quality", {}).get("data_as_of"), classification.get("quality", {}).get("data_as_of"))
        if type(x) is int
    ) if any(type(x) is int for x in (processing.get("quality", {}).get("data_as_of"), classification.get("quality", {}).get("data_as_of"))) else None
    calc_days = min(len(processing.get("series", {}).get(series_id, {}).get("daily_candles", [])) for series_id in PRIMARY.values())
    is_demo = bool(pctx.get("is_demo"))
    out.update({
        "asset": pctx.get("asset", "BTC"),
        "data_mode": pctx.get("data_mode"),
        "is_demo": is_demo,
        "reference_timestamp": pctx.get("reference_timestamp"),
        "execution_timestamp": pctx.get("execution_timestamp"),
        "generated_at": pctx.get("generated_at"),
        "processing_data_as_of": processing.get("quality", {}).get("data_as_of"),
        "classification_data_as_of": classification.get("quality", {}).get("data_as_of"),
        "data_as_of": data_as_of,
        "calculation_history": "full_available_history",
        "presentation_default_range": "30D",
        "calculation_history_days": calc_days,
        "fixture_seed": 20260807 if is_demo else None,
        "fixture_as_of_timestamp": pctx.get("reference_timestamp") if is_demo else None,
        "fixture_as_of_iso": _iso(pctx.get("reference_timestamp")) if is_demo else None,
        "synthetic_fixture": is_demo,
        "realism_refactor_version": "runtime_emulator_v1" if is_demo else "runtime_provider_v1",
        "realism_note": (
            "Glassnode-primary miner/on-chain runtime; 1h provider observations are aggregated to daily OHLC in Processing."
        ),
    })
    return out


def _range_selector(reference: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    out["options"] = [{"id": key, "label": key, "days": days} for key, days in RANGES.items()]
    out.update({"default": "30D", "source_resolution": "1D", "provider_intraday_resolution_used_for_ohlc": "1h", "intraday_available": True})
    return out


def _candle(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": row.get("timestamp"), "open": _finite(row.get("open")), "high": _finite(row.get("high")),
        "low": _finite(row.get("low")), "close": _finite(row.get("close")), "is_closed": bool(row.get("is_closed", True)),
    }


def _candle_range(candles: list[dict[str, Any]], range_id: str) -> dict[str, Any]:
    days = RANGES[range_id]
    rows = candles[-days:]
    actual = len(rows)
    status = "available" if actual == days else "partial" if actual else "unavailable"
    return {
        "range_id": range_id, "days": days, "status": status,
        "from_timestamp": rows[0]["timestamp"] if rows else None, "to_timestamp": rows[-1]["timestamp"] if rows else None,
        "expected_points": days, "actual_points": actual, "coverage_ratio": min(actual / days, 1.0),
        "reason": None if status == "available" else "range_history_partial" if actual else "range_history_unavailable",
        "warnings": [], "errors": [], "representation": "candlestick", "candles": deepcopy(rows), "candle_count": actual,
        "warmup_context": {"history_days_used": len(candles), "minimum_ma_period": 9, "maximum_ma_period": 200,
                           "ma_values_available_from_first_visible_candle": len(candles) >= days + 199},
    }


def _primary_chart(ref: Mapping[str, Any], chart_id: str, processing: Mapping[str, Any], is_demo: bool) -> dict[str, Any]:
    series = processing["series"][PRIMARY[chart_id]]
    candles = [_candle(row) for row in series.get("daily_candles", []) if isinstance(row, Mapping)]
    last = candles[-1] if candles else None
    out = deepcopy(dict(ref))
    out["provider"] = PROVIDERS[chart_id]
    out["status"] = series.get("status")
    out["current"] = {
        "status": series.get("status"), "timestamp": last.get("timestamp") if last else None,
        "value": last.get("close") if last else None, "unit": series.get("unit"),
        "display_value": _display(last.get("close") if last else None, chart_id),
    }
    out["series_by_range"] = {rid: _candle_range(candles, rid) for rid in RANGES}
    out["warnings"] = deepcopy(series.get("warnings", [])); out["errors"] = deepcopy(series.get("errors", []))
    out["preferred_representation"] = "candlestick"
    out["reason"] = None if candles else "provider_history_unavailable"
    ohlc = deepcopy(ref.get("ohlc_contract", {}))
    ohlc.update({"construction_stage": "processing", "native_provider_ohlc": False, "hmi_must_reconstruct_ohlc": False,
                 "line_fallback_allowed": False, "volume_allowed": False})
    ohlc["construction_rule"] = {"open": "first_real_value_in_bucket", "high": "max_real_value_in_bucket",
                                 "low": "min_real_value_in_bucket", "close": "last_real_value_in_bucket"}
    out["ohlc_contract"] = ohlc
    out["history_contract"] = {
        "history_days": len(candles), "history_points": len(candles),
        "from_timestamp": candles[0]["timestamp"] if candles else None, "to_timestamp": candles[-1]["timestamp"] if candles else None,
        "calculation_resolution": "1D", "provider_source_resolution": "1h",
        "daily_ohlc_construction": "processing_from_intraday_provider_samples", "minimum_warmup_required_days": 200,
        "sma_200_fully_formed_in_all_visible_ranges": len(candles) >= 559, "synthetic_fixture": is_demo,
        "warmup_available_days_before_360D_view": max(0, len(candles) - 360),
    }
    out["calculation_history"] = {"resolution": "1d", "point_count": len(candles), "candles": deepcopy(candles)}
    return out


def _point_range(records: list[dict[str, Any]], range_id: str) -> dict[str, Any]:
    days = RANGES[range_id]; rows = records[-days:]; actual = len(rows)
    status = "available" if actual == days else "partial" if actual else "unavailable"
    return {"range_id": range_id, "days": days, "status": status,
            "from_timestamp": rows[0]["timestamp"] if rows else None, "to_timestamp": rows[-1]["timestamp"] if rows else None,
            "expected_points": days, "actual_points": actual, "coverage_ratio": min(actual / days, 1.0), "points": deepcopy(rows),
            "reason": None if status == "available" else "range_history_partial" if actual else "range_history_unavailable",
            "warnings": [], "errors": []}


def _net_position_chart(ref: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    series = processing["series"]["miner_net_position_change"]
    records = [{"timestamp": row.get("timestamp"), "value": _finite(row.get("value")), "unit": "BTC/day"}
               for row in series.get("records", []) if isinstance(row, Mapping) and _finite(row.get("value")) is not None]
    last = records[-1] if records else None
    out = deepcopy(dict(ref))
    out.update({"provider": "glassnode", "source_provider": "glassnode", "calculation_source": "processing.series.miner_net_position_change.records",
                "status": series.get("status"), "reason": None if records else "provider_history_unavailable"})
    out["subtitle"] = "Direct Glassnode Miner Net Position Change"
    out["current"] = {"status": series.get("status"), "timestamp": last.get("timestamp") if last else None,
                      "value": last.get("value") if last else None, "unit": "BTC/day",
                      "display_value": _display(last.get("value") if last else None, "miner_net_position_change")}
    out["series_by_range"] = {rid: _point_range(records, rid) for rid in RANGES}
    out["warnings"] = deepcopy(series.get("warnings", [])); out["errors"] = deepcopy(series.get("errors", []))
    out["calculation_history"] = {"resolution": "1d", "point_count": len(records), "points": deepcopy(records)}
    return out


def _charts(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    is_demo = bool(processing.get("context", {}).get("is_demo"))
    out = {cid: _primary_chart(reference[cid], cid, processing, is_demo) for cid in PRIMARY}
    out["miner_net_position_change"] = _net_position_chart(reference["miner_net_position_change"], processing)
    return out


def _widgets(reference: Mapping[str, Any], candidate: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for wid, ref in reference.items():
        item = deepcopy(candidate.get(wid, {})) if isinstance(candidate.get(wid), Mapping) else {}
        if wid == "net_position" and isinstance(item.get("source"), Mapping):
            item["source"]["feature_id"] = "miner_net_position_change"
        out[wid] = _shape(ref, item)
    return out


def _ranges_from_records(records: list[dict[str, Any]], *, include_time_bounds: bool = False) -> dict[str, Any]:
    out = {}
    for rid, days in RANGES.items():
        rows = records[-days:]; actual = len(rows); status = "available" if actual == days else "partial" if actual else "unavailable"
        item = {"range_id": rid, "days": days, "status": status, "actual_points": actual, "coverage_ratio": min(actual / days, 1.0), "records": deepcopy(rows)}
        if include_time_bounds:
            item.update({"from_timestamp": rows[0].get("timestamp") if rows else None, "to_timestamp": rows[-1].get("timestamp") if rows else None,
                         "expected_points": days, "reason": None if status == "available" else "range_history_partial" if actual else "range_history_unavailable",
                         "warnings": []})
        out[rid] = item
    return out


def _age_buckets(record: Mapping[str, Any]) -> dict[str, float | None]:
    bands = record.get("bands", {}) if isinstance(record, Mapping) else {}
    def share(name: str) -> float:
        item = bands.get(name, {}) if isinstance(bands, Mapping) else {}
        value = _finite(item.get("derived_share_ratio")) if isinstance(item, Mapping) else None
        return float(value or 0.0)
    known = {
        "0_7d": share("0d_1d") + share("1d_1w"), "7_30d": share("1w_1m"), "30_90d": share("1m_3m"),
        "90_180d": share("3m_6m"), "180_365d": share("6m_12m"),
    }
    known["365d_plus"] = max(0.0, 1.0 - sum(known.values()))
    return known


def _nupl_state(value: float, thresholds: Mapping[str, Any]) -> str:
    if value < float(thresholds.get("capitulation_max", 0.0)): return "capitulation"
    if value < float(thresholds.get("hope_fear_max", 0.25)): return "hope_fear"
    if value < float(thresholds.get("optimism_anxiety_max", 0.5)): return "optimism_anxiety"
    if value < float(thresholds.get("belief_denial_max", 0.75)): return "belief_denial"
    return "euphoria_greed"


def _drilldowns(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    data_as_of = processing.get("quality", {}).get("data_as_of")

    # Pool distribution is real CryptoQuant entity-level flow; Glassnode direct outflow drives the primary pressure feature separately.
    pool = deepcopy(processing.get("features", {}).get("miner_outflow_distribution", {}))
    records = [deepcopy(r) for r in pool.get("records", []) if isinstance(r, Mapping)]
    current = deepcopy(records[-1]) if records else {}
    if current:
        current.update({"value": current.get("aggregate_outflow_total_btc"), "display_value": f"{float(current.get('aggregate_outflow_total_btc', 0)):+,.1f}"})
    item = deepcopy(dict(reference["miner_outflow_distribution"]))
    item.update({"status": pool.get("status"), "enabled": bool(records), "current": current,
                 "active_symbols": deepcopy(pool.get("active_symbols", [])), "inactive_symbols": deepcopy(pool.get("inactive_symbols", [])),
                 "series_by_range": _ranges_from_records(records, include_time_bounds=True),
                 "metadata": {"data_as_of": data_as_of, "core_fixture_data_as_of": data_as_of, "history_records": len(records),
                              "construction": "cryptoquant_entity_pool_outflow_distribution"},
                 "warnings": deepcopy(pool.get("warnings", [])), "errors": deepcopy(pool.get("errors", [])),
                 "calculation_history": {"record_count": len(records), "records": deepcopy(records)}})
    out["miner_outflow_distribution"] = item

    age = processing.get("features", {}).get("reserve_age_context", {}).get("network_context", {})
    age_records = [{"timestamp": r.get("timestamp"), "buckets": _age_buckets(r)} for r in age.get("records", []) if isinstance(r, Mapping)]
    age_current_raw = age.get("current", {}) if isinstance(age, Mapping) else {}
    age_current = {"timestamp": age_current_raw.get("timestamp"), "buckets": _age_buckets(age_current_raw)} if age_current_raw else (age_records[-1] if age_records else {})
    item = deepcopy(dict(reference["reserve_aging"]))
    status = processing.get("features", {}).get("reserve_age_context", {}).get("status", "unavailable")
    item.update({"status": status, "enabled": bool(age_records), "current": age_current,
                 "series_by_range": _ranges_from_records(age_records),
                 "metadata": {"data_as_of": data_as_of, "core_fixture_data_as_of": data_as_of, "history_records": len(age_records), "shares_sum_to_one": True},
                 "calculation_history": {"record_count": len(age_records), "records": deepcopy(age_records)}})
    out["reserve_aging"] = item

    rev = deepcopy(processing.get("features", {}).get("miner_revenue_breakdown", {}))
    rev_records = []
    for r in rev.get("records", []):
        if not isinstance(r, Mapping): continue
        rev_records.append({k: deepcopy(r.get(k)) for k in ("timestamp", "total_revenue_usd", "block_reward_revenue_usd", "fee_revenue_usd",
                            "derived_fee_share_ratio", "derived_fee_share_percent", "unit", "status")})
    rev_current = deepcopy(rev_records[-1]) if rev_records else {}
    if rev_current:
        rev_current["display"] = {
            "total_revenue": f"${float(rev_current.get('total_revenue_usd') or 0)/1e6:.2f}M",
            "block_reward_revenue": f"${float(rev_current.get('block_reward_revenue_usd') or 0)/1e6:.2f}M",
            "fee_revenue": f"${float(rev_current.get('fee_revenue_usd') or 0)/1e6:.2f}M",
            "fee_share": f"{float(rev_current.get('derived_fee_share_percent') or 0):.2f}%",
        }
    item = deepcopy(dict(reference["revenue_breakdown"]))
    item.update({"status": rev.get("status"), "enabled": bool(rev_records), "current": rev_current,
                 "series_by_range": _ranges_from_records(rev_records),
                 "metadata": {"data_as_of": data_as_of, "core_fixture_data_as_of": data_as_of, "history_records": len(rev_records),
                              "construction": "glassnode_total_block_reward_and_fee_revenue_alignment"},
                 "warnings": deepcopy(rev.get("warnings", [])), "errors": deepcopy(rev.get("errors", [])),
                 "calculation_history": {"record_count": len(rev_records), "records": deepcopy(rev_records)}})
    out["revenue_breakdown"] = item

    nupl_series = processing.get("series", {}).get("nupl", {})
    nupl_cls = classification.get("classifications", {}).get("nupl_phase", {})
    thresholds = nupl_cls.get("thresholds", {}) if isinstance(nupl_cls, Mapping) else {}
    nupl_records = []
    for r in nupl_series.get("records", []):
        if not isinstance(r, Mapping) or _finite(r.get("value")) is None: continue
        value = float(r["value"])
        nupl_records.append({"timestamp": r.get("timestamp"), "value": value, "phase": _nupl_state(value, thresholds),
                             "status": r.get("status", "available"), "scope": "network_context_not_miner_specific"})
    src = nupl_cls.get("source", {}) if isinstance(nupl_cls, Mapping) else {}
    current = {"status": nupl_cls.get("status"), "timestamp": src.get("timestamp"), "value": src.get("value"), "price_usd": src.get("price_usd"),
               "display_value": f"{float(src.get('value')):.3f}" if _finite(src.get("value")) is not None else "—",
               "display_price": f"${float(src.get('price_usd')):,.0f}" if _finite(src.get("price_usd")) is not None else "--"}
    item = deepcopy(dict(reference["nupl_phases"]))
    item.update({"status": nupl_cls.get("status"), "enabled": bool(nupl_records), "current": current, "phase": nupl_cls.get("state"),
                 "series_by_range": _ranges_from_records(nupl_records),
                 "metadata": {"data_as_of": data_as_of, "core_fixture_data_as_of": data_as_of, "history_records": len(nupl_records),
                              "semantic_scope": "network_wide_context_not_miner_specific"},
                 "warnings": deepcopy(nupl_cls.get("warnings", [])), "errors": deepcopy(nupl_cls.get("errors", [])),
                 "calculation_history": {"record_count": len(nupl_records), "records": deepcopy(nupl_records)}})
    out["nupl_phases"] = item
    return out


def _slice(values: Any, count: int) -> list[Any]:
    return deepcopy(values[-count:]) if isinstance(values, list) else []


def _summary(ref: Mapping[str, Any], indicator_id: str, package: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(ref))
    current = package.get("current", {}) if isinstance(package.get("current"), Mapping) else {}
    primary = {"macd":"histogram","rsi":"rsi","tsi":"tsi","adx":"adx","stochastic":"k","williams_r":"williams_r","cci":"cci",
               "atr":"atr","wasserstein_distance":"wasserstein_distance","bollinger_band_width":"bandwidth"}.get(indicator_id)
    value = _finite(current.get(primary)) if primary else None
    out.update({"indicator_id": indicator_id, "value": value, "display_value": f"{value:.3f}" if value is not None else "—",
                "status": package.get("status"), "recalculate_in_hmi": False})
    # Contract Builder does not invent a new state; signal is a neutral presentation fallback unless Processing already supplied one.
    out["signal"] = package.get("signal", out.get("signal", "neutral"))
    return out


def _indicator(ref: Mapping[str, Any], indicator_id: str, package: Mapping[str, Any], count: int, history_count: int, range_id: str) -> dict[str, Any]:
    out = deepcopy(dict(ref))
    timestamps = _slice(package.get("timestamps", []), count)
    src_series = package.get("series", {}) if isinstance(package.get("series"), Mapping) else {}
    key_map = {"bollinger_band_width": {"bollinger_band_width":"bandwidth"}}
    series = {}
    for out_key in ref.get("series", {}):
        src_key = key_map.get(indicator_id, {}).get(out_key, out_key)
        series[out_key] = _slice(src_series.get(src_key, []), count)
    current = {k: (v[-1] if v else None) for k, v in series.items()}
    out.update({"status": package.get("status"), "timestamps": timestamps, "series": series, "current": current,
                "recalculate_in_hmi": False})
    out["summary"] = _summary(ref.get("summary", {}), indicator_id, {**package, "current": current})
    cc = deepcopy(ref.get("calculation_contract", {}))
    cc.update({"source": f"processing.technical_analysis.targets.*.indicators.{indicator_id}", "history_points": history_count,
               "visible_range": range_id, "implementation_owner": "processing", "recalculate_in_hmi": False})
    out["calculation_contract"] = cc
    return out


def _overlay(ref: Mapping[str, Any], package: Mapping[str, Any], count: int, history_count: int) -> dict[str, Any]:
    out = deepcopy(dict(ref)); src_series = package.get("series", {}) if isinstance(package.get("series"), Mapping) else {}
    out["status"] = package.get("status")
    out["series"] = {key: _slice(src_series.get(key, []), count) for key in ref.get("series", {})}
    out["recalculate_in_hmi"] = False
    if "history_points_used" in out: out["history_points_used"] = history_count
    if "all_visible_points_warm" in out: out["all_visible_points_warm"] = history_count >= 200
    if "parameters" in out:
        params = package.get("parameters", {}) if isinstance(package.get("parameters"), Mapping) else {}
        # SP uses slightly different parameter labels; preserve its keys while filling compatible runtime values.
        for key in list(out["parameters"]):
            aliases = {"std_dev":"standard_deviations", "period":"period", "residual_std_multiplier":"deviation_multiplier"}
            src = aliases.get(key, key)
            if src in params: out["parameters"][key] = params[src]
    return out


def _technical_analysis(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference)); out["recalculate_in_hmi"] = False
    runtime = processing.get("technical_analysis", {})
    targets = runtime.get("targets", {}) if isinstance(runtime, Mapping) else {}
    built = {}
    for chart_id, ref_target in reference.get("targets_by_chart", {}).items():
        pkg = targets.get(chart_id, {}) if isinstance(targets, Mapping) else {}
        candles = pkg.get("candles", []) if isinstance(pkg, Mapping) else []
        history_count = len(candles)
        target = deepcopy(dict(ref_target)); target["unit"] = pkg.get("unit", target.get("unit"))
        ranges = {}
        for rid, ref_range in ref_target.get("ranges", {}).items():
            count = min(RANGES[rid], history_count)
            rr = deepcopy(dict(ref_range)); rr["status"] = pkg.get("status"); rr["reason"] = None if pkg.get("status") == "available" else pkg.get("reason")
            rr["source_path"] = f"charts.{chart_id}.series_by_range.{rid}.candles"; rr["history_source_path"] = f"charts.{chart_id}.calculation_history.candles"
            indicators = pkg.get("indicators", {}) if isinstance(pkg.get("indicators"), Mapping) else {}
            rr["overlays"] = {
                "moving_averages": _overlay(ref_range["overlays"]["moving_averages"], indicators.get("moving_averages", {}), count, history_count),
                "bollinger_bands": _overlay(ref_range["overlays"]["bollinger_bands"], indicators.get("bollinger_bands", {}), count, history_count),
                "regression_channel": _overlay(ref_range["overlays"]["regression_channel"], indicators.get("regression_channel", {}), count, history_count),
            }
            rr["indicators"] = {iid: _indicator(iref, iid, indicators.get(iid, {}), count, history_count, rid)
                                for iid, iref in ref_range.get("indicators", {}).items()}
            start_ts = candles[-count].get("timestamp") if count and isinstance(candles[-count], Mapping) else None
            events = []
            for event in pkg.get("events", []):
                if not isinstance(event, Mapping) or (start_ts is not None and (type(event.get("timestamp")) is not int or event.get("timestamp") < start_ts)):
                    continue
                item = deepcopy(dict(event)); source = deepcopy(item.get("source", {})); source["range"] = rid; item["source"] = source
                item["event_uid"] = f"{chart_id}:{item.get('timestamp')}:{rid}:technical_cross:{item.get('event_id')}"
                refs = [x for x in ref_range.get("events", []) if isinstance(x, Mapping)
                        and x.get("event_group") == item.get("event_group")
                        and bool(x.get("indicator_id")) == bool(item.get("indicator_id"))
                        and bool(isinstance(x.get("calculation"), Mapping) and "gate" in x.get("calculation", {}))
                            == bool(isinstance(item.get("calculation"), Mapping) and "gate" in item.get("calculation", {}))]
                events.append(_shape(refs[0], item) if refs else item)
            rr["events"] = events; rr["event_count"] = len(events)
            ranges[rid] = rr
        target["ranges"] = ranges; built[chart_id] = target
    out["targets_by_chart"] = built
    out["history_contract"] = deepcopy(reference.get("history_contract", {}))
    if isinstance(out["history_contract"], dict):
        out["history_contract"]["recalculate_in_hmi"] = False
    return out


def _quality(reference: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any], charts: Mapping[str, Any], drilldowns: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference)); pq = processing.get("quality", {}); cq = classification.get("quality", {})
    statuses = [pq.get("status"), cq.get("status"), *[x.get("status") for x in charts.values() if isinstance(x, Mapping)]]
    status = "invalid" if "invalid" in statuses else "partial" if "partial" in statuses else "unavailable" if all(x == "unavailable" for x in statuses if x) else "available"
    warnings = deepcopy(pq.get("warnings", []))
    if pq.get("data_as_of") is None:
        warnings.append("processing_data_as_of_unavailable")
    if cq.get("data_as_of") is None:
        warnings.append("classification_data_as_of_unavailable")
    out.update({"status": status, "data_as_of": pq.get("data_as_of"), "processing_status": pq.get("status"), "classification_status": cq.get("status"),
                "missing_fields": deepcopy(pq.get("missing_fields", [])), "warnings": list(dict.fromkeys(warnings)), "errors": deepcopy(pq.get("errors", []))})
    ext = deepcopy(out.get("extensions", {}))
    # Preserve exact SP extension shape but remove retired synthetic/derived claims where present.
    for key in list(ext):
        low = key.lower()
        if "net_position_derived" in low: ext[key] = False
    return out | {"extensions": ext}


def _history(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    counts = [len(processing.get("series", {}).get(series_id, {}).get("daily_candles", [])) for series_id in PRIMARY.values()]
    calc = min(counts) if counts else 0; demo = bool(processing.get("context", {}).get("is_demo"))
    out.update({"calculation_records": calc, "minimum_warmup_records": 200, "maximum_standard_indicator_period": 200,
                "all_visible_moving_averages_warm": calc >= 559, "technical_indicators_precomputed": True, "hmi_recalculation": False,
                "synthetic_fixture": demo, "fixture_seed": 20260807 if demo else None, "resolution": "1d",
                "note": f"{calc} daily calculation records available across all primary on-chain metrics."})
    return out


def _shape(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project onto exact SP keys while preserving explicit runtime nulls."""
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {key: _shape(value, source.get(key, _MISSING)) for key, value in reference.items()}
    if isinstance(reference, list):
        if candidate is _MISSING:
            return deepcopy(reference)
        if not isinstance(candidate, list):
            return deepcopy(candidate)
        if not reference:
            return deepcopy(candidate)
        if all(not isinstance(x, (dict, list)) for x in reference):
            return deepcopy(candidate)
        identity_keys = ("metric_id", "widget_id", "chart_id", "drilldown_id", "indicator_id", "event_group", "event_id", "id", "state", "range_id")
        def ref_for(item: Any, index: int) -> Any:
            if isinstance(item, Mapping):
                if item.get("event_group") is not None:
                    has_gate = isinstance(item.get("calculation"), Mapping) and "gate" in item.get("calculation", {})
                    for ref_item in reference:
                        if not isinstance(ref_item, Mapping) or ref_item.get("event_group") != item.get("event_group"):
                            continue
                        ref_gate = isinstance(ref_item.get("calculation"), Mapping) and "gate" in ref_item.get("calculation", {})
                        if ref_gate == has_gate and bool(ref_item.get("indicator_id")) == bool(item.get("indicator_id")):
                            return ref_item
                for key in identity_keys:
                    value = item.get(key)
                    if value is None:
                        continue
                    for ref_item in reference:
                        if isinstance(ref_item, Mapping) and ref_item.get(key) == value:
                            return ref_item
            return reference[index] if index < len(reference) else reference[0]
        return [_shape(ref_for(item, index), item) for index, item in enumerate(candidate)]
    return deepcopy(reference if candidate is _MISSING else candidate)


def align_on_chain_miners_to_sp_v1_3(candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    ref = _template()
    context = _context(ref["context"], processing, classification)
    charts = _charts(ref["charts"], processing)
    drilldowns = _drilldowns(ref["drilldowns"], processing, classification, candidate.get("drilldowns", {}))
    built = {
        "schema": {"id": ref["schema"]["id"], "version": SP_VERSION},
        "screen": deepcopy(ref["screen"]), "stage": "screen_contract", "mode": processing.get("mode"), "context": context,
        "range_selector": _range_selector(ref["range_selector"]),
        "operational_status": {**deepcopy(ref["operational_status"]), "data_mode": context.get("data_mode"), "is_demo": context.get("is_demo"),
                               "quality_status": processing.get("quality", {}).get("status"), "generated_at": context.get("generated_at"), "data_as_of": context.get("data_as_of")},
        "charts": charts,
        "widgets": _widgets(ref["widgets"], candidate.get("widgets", {}), classification),
        "drilldowns": drilldowns,
        "quality": {},
        "technical_analysis": _technical_analysis(ref["technical_analysis"], processing),
        "history_contract": _history(ref["history_contract"], processing),
    }
    built["quality"] = _quality(ref["quality"], processing, classification, charts, drilldowns)
    built["operational_status"]["quality_status"] = built["quality"]["status"]
    # Final shape projection makes root/nesting contractually identical to the frozen SP.
    return _shape(ref, built)
