from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

SP_SCHEMA_VERSION = "1.11.0-oi-adx-crosses"
HMI_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
DISPLAY_WINDOW = 120
SP_TEMPLATE_PATH = Path(__file__).with_name("open_interest_and_funding_screen_sp_v1_11.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _finite(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return 0.0 if value == 0 else value


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _project(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project dynamic runtime data onto the exact frozen Screen-SP shape."""
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
            "metric_id", "kpi_id", "widget_id", "chart_id", "table_id", "badge_id",
            "id", "role", "event_group", "indicator_id", "market", "timeframe",
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


def _iso(timestamp: int | None) -> str | None:
    if type(timestamp) is not int:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _compact(value: int | float | None) -> str:
    if value is None:
        return "—"
    absolute = abs(float(value))
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if absolute >= divisor:
            return f"{value / divisor:.2f}{suffix}"
    return f"{value:.2f}"


def _context(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    pctx = processing["context"]
    out.update({
        "default_market": "all_exchanges",
        "available_markets": ["all_exchanges"],
        "default_timeframe": "1h",
        "available_timeframes": list(HMI_TIMEFRAMES),
        "asset": pctx.get("asset", "BTC"),
        "exchange_scope": pctx.get("exchange_scope", "all_exchanges"),
        "primary_provider": pctx.get("primary_provider", "coinglass"),
        "confirmation_providers": deepcopy(pctx.get("confirmation_providers", ["cryptoquant", "glassnode"])),
        "data_mode": pctx.get("data_mode"),
        "is_demo": pctx.get("is_demo"),
        "reference_timestamp": pctx.get("reference_timestamp"),
        "generated_at": pctx.get("generated_at"),
        "data_as_of": pctx.get("reference_timestamp"),
        "analysis_profile": "ohlc_without_volume",
        "volume_enabled": False,
    })
    is_demo = bool(pctx.get("is_demo"))
    if is_demo:
        out["fixture_as_of_timestamp"] = pctx.get("reference_timestamp")
        out["fixture_as_of_iso"] = _iso(pctx.get("reference_timestamp"))
        out["synthetic_fixture"] = True
        out["realism_refactor_version"] = "runtime_emulator_v1"
        out["realism_note"] = "deterministic provider-shaped emulator data; CoinGlass native OI/Funding OHLC with Glassnode confirmations"
    else:
        out["fixture_as_of_timestamp"] = None
        out["fixture_as_of_iso"] = None
        out["synthetic_fixture"] = False
        out["realism_refactor_version"] = "runtime_provider_v1"
        out["realism_note"] = "runtime provider data; CoinGlass native OI/Funding OHLC with Glassnode/CryptoQuant confirmations"
    return out


def _elr_payload(processing: Mapping[str, Any]) -> Mapping[str, Any]:
    return processing.get("confirmations", {}).get("estimated_leverage_ratio", {}).get("glassnode", {})


def _latest_confirmation(payload: Mapping[str, Any]) -> tuple[int | None, float | None]:
    records = payload.get("incoming_records") if isinstance(payload, Mapping) else None
    if not isinstance(records, list) or not records:
        return None, None
    record = records[-1] if isinstance(records[-1], Mapping) else {}
    return record.get("timestamp") if type(record.get("timestamp")) is int else None, _finite(record.get("value"))


def _kpis(candidate: Mapping[str, Any], processing: Mapping[str, Any], selected_timeframe: str) -> dict[str, Any]:
    base = deepcopy(candidate.get("kpis", {}))
    items = [deepcopy(x) for x in base.get("items", []) if isinstance(x, Mapping)]
    by_id = {str(x.get("metric_id")): x for x in items}
    elr = _elr_payload(processing)
    ts, value = _latest_confirmation(elr)
    status = str(elr.get("status", "unavailable")) if isinstance(elr, Mapping) else "unavailable"
    reason = elr.get("reason") if status != "available" else None
    by_id["estimated_leverage_ratio"] = {
        "metric_id": "estimated_leverage_ratio",
        "label": "Estimated Leverage Ratio",
        "value": value,
        "display_value": f"{value:.3f}" if value is not None else "—",
        "unit": "ratio",
        "status": status,
        "reason": reason,
        "source": {"provider": "glassnode", "metric": "futures_estimated_leverage_ratio", "role": "classification_and_widget"},
        "quality": {"data_mode": processing["context"].get("data_mode"), "contract_ready": status == "available"},
        "provenance": {"provider": "glassnode", "metric": "futures_estimated_leverage_ratio", "integration_state": "runtime_feed"},
    }
    order = ("open_interest_usd", "oi_change_24h", "oi_funding_state", "provider_availability", "funding_rate", "estimated_leverage_ratio")
    # Normalize display formatting for the ordinary builder's scalar KPIs.
    if "open_interest_usd" in by_id:
        by_id["open_interest_usd"]["display_value"] = _compact(_finite(by_id["open_interest_usd"].get("value")))
    if "oi_change_24h" in by_id:
        pct = _finite(by_id["oi_change_24h"].get("secondary_value"))
        by_id["oi_change_24h"]["secondary_display_value"] = f"{pct:.3f}%" if pct is not None else "—"
    if "funding_rate" in by_id:
        val = _finite(by_id["funding_rate"].get("value"))
        by_id["funding_rate"]["display_value"] = f"{val:.4f}%" if val is not None else "—"
        state = by_id["funding_rate"].get("classification")
        by_id["funding_rate"]["secondary_display_value"] = str(state).upper() if state else "—"
    return {"selected_market": "all_exchanges", "selected_timeframe": selected_timeframe,
            "items": [by_id[item] for item in order if item in by_id]}


def _records(frame: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in frame.get("records", []):
        if not isinstance(row, Mapping):
            continue
        result.append({
            "timestamp": row.get("timestamp"), "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"), "market_type": "all_exchanges",
        })
    return result


def _slice_series(series: Mapping[str, Any], count: int) -> dict[str, list[Any]]:
    return {str(key): deepcopy(value[-count:] if isinstance(value, list) else []) for key, value in series.items()}


def _candle_tf(processing: Mapping[str, Any], timeframe: str) -> dict[str, Any]:
    frame = processing["series"]["open_interest_ohlc"]["timeframes"][timeframe]
    full_records = _records(frame)
    visible = full_records[-DISPLAY_WINDOW:]
    indicators = processing["indicators"]["open_interest"]["timeframes"][timeframe]
    moving = indicators["moving_averages"]
    bb = indicators["bollinger_bands"]
    reg = indicators["regression_channel"]
    count = len(visible)
    overlays = {
        "moving_averages": {"status": moving.get("status"), "series": _slice_series(moving.get("series", {}), count)},
        "bollinger_bands": {"status": bb.get("status"), "series": _slice_series(bb.get("series", {}), count)},
        "regression_channel": {"status": reg.get("status"), "series": _slice_series(reg.get("series", {}), count)},
    }
    return {
        "status": frame.get("status"), "records": visible, "overlays": overlays,
        "display_window": count,
        "calculation_history": {
            "record_count": len(full_records), "records": full_records,
            "history_contract": {"calculation": "full_available_history", "presentation": "tail_window", "display_window": count,
                                 "recalculate_in_hmi": False},
        },
    }


_INDICATOR_MAP = {
    "macd": ("macd", {"macd": "macd", "signal": "signal", "histogram": "histogram"}),
    "rsi": ("rsi", {"rsi": "rsi"}),
    "tsi": ("tsi", {"tsi": "tsi", "signal": "signal"}),
    "adx": ("adx", {"adx": "adx", "di_plus": "di_plus", "di_minus": "di_minus"}),
    "stochastic": ("stochastic", {"k": "k", "d": "d"}),
    "williams_r": ("williams_r", {"williams_r": "williams_r"}),
    "cci": ("cci", {"cci": "cci"}),
    "atr": ("atr", {"atr": "atr"}),
    "wasserstein_distance": ("wasserstein_distance", {"wasserstein_distance": "distance"}),
    "bollinger_band_width": ("bollinger_band_width", {"bollinger_band_width": "bandwidth"}),
    "oi_roc": ("oi_roc", {"oi_roc": "roc"}),
}


def _indicator_tf(processing: Mapping[str, Any], chart_id: str, timeframe: str, reference_lines: list[Any]) -> dict[str, Any]:
    package_name, fields = _INDICATOR_MAP[chart_id]
    package = processing["indicators"]["open_interest"]["timeframes"][timeframe][package_name]
    timestamps = package.get("timestamps", [])
    count = min(DISPLAY_WINDOW, len(timestamps))
    visible_ts = deepcopy(timestamps[-count:])
    source_series = package.get("series", {})
    series = {out_name: deepcopy(source_series.get(source_name, [])[-count:]) for out_name, source_name in fields.items()}
    current = {name: (values[-1] if values else None) for name, values in series.items()}
    return {
        "status": package.get("status"), "timestamps": visible_ts, "series": series, "current": current,
        "reference_lines": deepcopy(reference_lines),
        "history": {"closed_candles_available": len(timestamps), "display_records": count,
                    "uses_closed_candles_only": True, "all_indicator_periods_warm": len(timestamps) >= 200},
    }


def _funding_tf(processing: Mapping[str, Any], timeframe: str, reference_lines: list[Any]) -> dict[str, Any]:
    frame = processing["series"]["funding_rate_ohlc"]["timeframes"][timeframe]
    records = [row for row in frame.get("records", []) if isinstance(row, Mapping)]
    timestamps = [row.get("timestamp") for row in records]
    values = [row.get("close") for row in records]
    count = min(DISPLAY_WINDOW, len(records))
    visible_ts, visible_values = deepcopy(timestamps[-count:]), deepcopy(values[-count:])
    return {
        "status": frame.get("status"), "timestamps": visible_ts,
        "series": {"funding_rate": visible_values}, "current": {"funding_rate": visible_values[-1] if visible_values else None},
        "reference_lines": deepcopy(reference_lines),
        "history": {"closed_candles_available": len(records), "display_records": count, "uses_closed_candles_only": True},
        "calculation_history": {"record_count": len(records), "timestamps": deepcopy(timestamps),
                                "series": {"funding_rate": deepcopy(values)}, "construction": "provider_native_ohlc_close",
                                "recalculate_in_hmi": False},
    }


def _charts(reference: Mapping[str, Any], processing: Mapping[str, Any], selected_timeframe: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for chart_id, ref_chart in reference.items():
        chart = deepcopy(ref_chart)
        if "selected_market" in chart:
            chart["selected_market"] = "all_exchanges"
        if "selected_timeframe" in chart:
            chart["selected_timeframe"] = selected_timeframe
        if chart_id in {"open_interest_candlestick", "open_interest_ohlc"}:
            tf_map = {tf: _candle_tf(processing, tf) for tf in HMI_TIMEFRAMES}
            chart["markets"] = {"all_exchanges": {"timeframes": tf_map}}
            chart["status"] = tf_map[selected_timeframe]["status"]
        elif chart_id == "funding_rate":
            ref_lines = ref_chart["markets"]["all_exchanges"][selected_timeframe].get("reference_lines", [])
            chart["markets"] = {"all_exchanges": {tf: _funding_tf(processing, tf,
                ref_chart["markets"]["all_exchanges"][tf].get("reference_lines", ref_lines)) for tf in HMI_TIMEFRAMES}}
            chart["status"] = chart["markets"]["all_exchanges"][selected_timeframe]["status"]
        else:
            chart["markets"] = {"all_exchanges": {tf: _indicator_tf(processing, chart_id, tf,
                ref_chart["markets"]["all_exchanges"][tf].get("reference_lines", [])) for tf in HMI_TIMEFRAMES}}
            chart["status"] = chart["markets"]["all_exchanges"][selected_timeframe]["status"]
        output[chart_id] = chart
    return output


def _event_candidates(processing: Mapping[str, Any]) -> dict[str, Any]:
    by_id: dict[str, Any] = {}
    allowed = {"moving_average_cross", "channel_cross", "macd_signal_cross", "directional_indicator_cross", "stochastic_cross"}
    for event in processing.get("events", {}).get("by_id", {}).values():
        if not isinstance(event, Mapping) or event.get("event_type") not in allowed or event.get("timeframe") not in HMI_TIMEFRAMES:
            continue
        direction = event.get("direction_numeric")
        if direction not in {-1, 1}:
            continue
        # Screen-B Stochastic arrows are gated at the extreme zones by the frozen SP.
        if event.get("event_type") == "stochastic_cross":
            values = event.get("values", {}) if isinstance(event.get("values"), Mapping) else {}
            if direction == 1 and not bool(values.get("below_20")):
                continue
            if direction == -1 and not bool(values.get("above_80")):
                continue
        relation = "above" if direction == 1 else "below"
        first, second = str(event.get("first_series")), str(event.get("second_series"))
        event_type = event["event_type"]
        indicator_id = None
        if event_type == "moving_average_cross":
            group, eid = "moving_average_cross", f"{first}_{relation}_{second}"
            screen_a = True
        elif event_type == "channel_cross":
            group, eid = "channel_cross", f"regression_middle_{relation}_bollinger_middle"
            screen_a = True
        elif event_type == "macd_signal_cross":
            group, indicator_id, eid = "macd_cross", "macd", f"macd_macd_{relation}_signal"
            screen_a = False
        elif event_type == "directional_indicator_cross":
            group, indicator_id, eid = "adx_cross", "adx", f"adx_di_plus_{relation}_di_minus"
            screen_a = False
        else:
            group, indicator_id, eid = "stochastic_cross", "stochastic", f"stochastic_k_{relation}_d"
            screen_a = False
        timestamp = event.get("timestamp")
        tf = event.get("timeframe")
        uid = f"all_exchanges:{tf}:{timestamp}:technical_cross:{eid}"
        item = {
            "event_uid": uid, "timestamp": timestamp, "event_id": eid, "event_type": "technical_cross",
            "event_group": group, "signal": "bullish" if direction == 1 else "bearish",
            "label": eid.replace("_", " ").upper(), "marker": "arrow_up" if direction == 1 else "arrow_down",
            "source": {"market": "all_exchanges", "timeframe": tf}, "display": {"screen_a": screen_a, "screen_b": True},
        }
        if indicator_id:
            item["indicator_id"] = indicator_id
        by_id[uid] = item
    return dict(sorted(by_id.items(), key=lambda item: (item[1]["timestamp"], item[1]["event_group"], item[0])))


def _events(reference: Mapping[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    by_id = _event_candidates(processing)
    out["by_id"] = by_id
    out["technical_cross_ids"] = list(by_id)
    return out


def _table(candidate: Mapping[str, Any], processing: Mapping[str, Any], selected_timeframe: str) -> dict[str, Any]:
    out = deepcopy(candidate.get("tables", {}).get("indicators_metrics", {}))
    if not out:
        return {}
    source_rows = candidate.get("tables", {}).get("indicators_metrics", {}).get("indicator_package", {}).get("markets", {}).get("all_exchanges", {})
    # Ordinary builder rows are lists keyed by timeframe.
    final_markets: dict[str, list[dict[str, Any]]] = {}
    labels = {
        "macd":"MACD", "rsi":"RSI", "tsi":"TSI", "adx":"ADX", "stochastic":"Stochastic",
        "williams_r":"Williams %R", "cci":"CCI", "atr":"ATR", "wasserstein_distance":"Wasserstein Distance",
        "bollinger_band_width":"Bollinger Band Width", "oi_roc":"OI ROC", "funding_rate":"Funding Rate",
    }
    desired = tuple(labels)
    for tf in HMI_TIMEFRAMES:
        raw = source_rows.get(tf, []) if isinstance(source_rows, Mapping) else []
        by_id = {str(row.get("id")): row for row in raw if isinstance(row, Mapping)}
        # Add funding directly because ordinary table excludes it.
        fund = processing["series"]["funding_rate_ohlc"]["timeframes"][tf]
        fcur = fund.get("current") if isinstance(fund.get("current"), Mapping) else {}
        by_id["funding_rate"] = {"id":"funding_rate", "status":fund.get("status"), "value":fcur.get("close"),
                                 "unit":"percent_points", "timestamp":fcur.get("timestamp"), "secondary_values":{},
                                 "classification_state": None}
        rows = []
        for mid in desired:
            src = by_id.get(mid, {})
            value = _finite(src.get("value"))
            state = src.get("classification_state")
            signal = "bullish" if state and any(token in str(state) for token in ("bull", "positive", "strong", "above")) else \
                     "bearish" if state and any(token in str(state) for token in ("bear", "negative", "below")) else "neutral"
            rows.append({"metric_id": mid, "label": labels[mid], "value": value, "unit": src.get("unit"),
                         "timestamp": src.get("timestamp"), "status": src.get("status", "unavailable"),
                         "signal": signal, "state": state or ("available" if value is not None else "unavailable"),
                         "confidence": 0.6 if value is not None else 0.0,
                         "secondary_values": deepcopy(src.get("secondary_values", {}))})
        final_markets[tf] = rows
    out["indicator_package"] = {"selected_market":"all_exchanges", "selected_timeframe":selected_timeframe,
                                "markets":{"all_exchanges":final_markets}}
    return out


def _quality(reference: Mapping[str, Any], classification: Mapping[str, Any], processing: Mapping[str, Any], events: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(reference))
    q = classification.get("quality", {})
    status = q.get("status", "partial")
    out.update({"status": "ok" if status == "available" else status, "contract_complete": True,
                "data_complete": bool(q.get("data_complete", status == "available")), "volume_enabled": False,
                "warnings": deepcopy(q.get("warnings", [])), "errors": deepcopy(q.get("errors", []))})
    ext = out.get("extensions", {})
    counts: dict[str, int] = {"macd":0,"adx":0,"stochastic":0}
    for e in events.get("by_id", {}).values():
        if e.get("event_group") == "macd_cross": counts["macd"] += 1
        if e.get("event_group") == "adx_cross": counts["adx"] += 1
        if e.get("event_group") == "stochastic_cross": counts["stochastic"] += 1
    if "screen_b_arrow_audit_v2" in ext:
        ext["screen_b_arrow_audit_v2"]["event_counts"] = counts
    if "realistic_oi_730_v2" in ext:
        full = len(processing["series"]["open_interest_ohlc"]["timeframes"]["1h"].get("records", []))
        ext["realistic_oi_730_v2"]["history_records_per_timeframe"] = full
        ext["realistic_oi_730_v2"]["visible_records"] = min(DISPLAY_WINDOW, full)
        ext["realistic_oi_730_v2"]["screen_a_cross_events"] = sum(bool(e.get("display",{}).get("screen_a")) for e in events.get("by_id",{}).values())
        ext["realistic_oi_730_v2"]["screen_b_indicator_cross_events"] = sum(not bool(e.get("display",{}).get("screen_a")) for e in events.get("by_id",{}).values())
    if "realism_v1" in ext:
        ref = processing["context"].get("reference_timestamp")
        ext["realism_v1"]["fixture_as_of_timestamp"] = ref if processing["context"].get("is_demo") else None
        ext["realism_v1"]["fixture_as_of_iso"] = _iso(ref) if processing["context"].get("is_demo") else None
        ext["realism_v1"]["synthetic_not_live"] = bool(processing["context"].get("is_demo"))
    out["extensions"] = ext
    return out


def align_open_interest_and_funding_to_sp_v1_11(
    candidate: Mapping[str, Any], processing: Mapping[str, Any], classification: Mapping[str, Any], *, selected_timeframe: str,
) -> dict[str, Any]:
    reference = _template()
    dynamic: dict[str, Any] = {
        "family": "open_interest_and_funding", "screen": "open_interest_and_funding", "schema_version": SP_SCHEMA_VERSION,
        "context": _context(reference["context"], processing),
        "badges": deepcopy(reference["badges"] if processing["context"].get("is_demo") else []),
        "kpis": _kpis(candidate, processing, selected_timeframe),
        "widgets": deepcopy(candidate.get("widgets", {})),
        "selectors": deepcopy(reference["selectors"]),
        "charts": _charts(reference["charts"], processing, selected_timeframe),
        "tables": {"indicators_metrics": _table(candidate, processing, selected_timeframe)},
        "events": _events(reference["events"], processing),
        "screen_layout": deepcopy(reference["screen_layout"]),
        "history_contract": deepcopy(reference["history_contract"]),
        "technical_analysis": deepcopy(reference["technical_analysis"]),
    }
    dynamic["selectors"]["timeframe"]["selected"] = selected_timeframe
    # Ensure widgets contain no visual-fixture values; ordinary builder already owns these runtime values.
    dynamic["widgets"] = deepcopy(candidate.get("widgets", {}))
    history_count = min(len(processing["series"]["open_interest_ohlc"]["timeframes"][tf].get("records", [])) for tf in HMI_TIMEFRAMES)
    dynamic["history_contract"].update({
        "calculation_records": history_count,
        "all_visible_moving_averages_warm": history_count >= 200,
        "technical_indicators_precomputed": True,
        "hmi_recalculation": False,
        "synthetic_fixture": bool(processing["context"].get("is_demo")),
        "fixture_seed": 20260807 if processing["context"].get("is_demo") else None,
        "note": "runtime full-history indicator calculation; HMI renders precomputed values only",
    })
    dynamic["quality"] = _quality(reference["quality"], classification, processing, dynamic["events"])
    aligned = _project(reference, dynamic)
    # The exact SP widget shape is preserved by projection; selected timeframe is runtime-owned.
    aligned["kpis"]["selected_timeframe"] = selected_timeframe
    aligned["schema_version"] = SP_SCHEMA_VERSION
    # CoinGlass is the canonical provider for OI/Funding in this family and supplies native OHLC.
    for _chart_id in ("open_interest_candlestick", "open_interest_ohlc"):
        if isinstance(aligned.get("charts", {}).get(_chart_id, {}).get("ohlc_contract"), dict):
            aligned["charts"][_chart_id]["ohlc_contract"]["native_provider_ohlc"] = True
            aligned["charts"][_chart_id]["ohlc_contract"]["construction_stage"] = "input_provider_native"
    # Promote the precomputed Screen B package to its VR1-final canonical root;
    # both chart-local copies remain compatibility mirrors for older HMI builds.
    chart_analysis = aligned.get("charts", {}).get("open_interest_ohlc", {}).get("technical_fundamental_analysis")
    if isinstance(chart_analysis, Mapping):
        root_analysis = deepcopy(dict(chart_analysis))
        root_analysis.update({
            "canonical_location": "technical_analysis",
            "compatibility_mirror": False,
            "legacy_mirror_paths": [
                "charts.open_interest_candlestick.technical_fundamental_analysis",
                "charts.open_interest_ohlc.technical_fundamental_analysis",
            ],
        })
        aligned["technical_analysis"] = root_analysis
        for _chart_id in ("open_interest_candlestick", "open_interest_ohlc"):
            mirror = aligned.get("charts", {}).get(_chart_id, {}).get("technical_fundamental_analysis")
            if isinstance(mirror, dict):
                mirror.update({"canonical_location": "technical_analysis", "compatibility_mirror": True})
    json.dumps(aligned, ensure_ascii=False, allow_nan=False)
    return aligned
