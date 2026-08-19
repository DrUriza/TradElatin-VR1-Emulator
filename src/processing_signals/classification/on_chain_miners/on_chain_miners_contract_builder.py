from __future__ import annotations

from .on_chain_miners_sp_v2_0_adapter import align_on_chain_miners_to_sp_v2_0

import copy
import json
import math
from collections.abc import Mapping, Sequence
from typing          import Any, Callable


ON_CHAIN_MINERS_SCREEN_ID        = "on_chain_miners"
ON_CHAIN_MINERS_ROUTE            = "/on-chain-miners"
ON_CHAIN_MINERS_TITLE            = "ON-CHAIN & MINERS METRICS"
ON_CHAIN_MINERS_CONTRACT_SCHEMA  = "trad_elatin.on_chain_miners.screen.v1"
ON_CHAIN_MINERS_CONTRACT_VERSION = "1.0.0"

RANGE_OPTIONS = ("1D", "7D", "30D", "90D", "360D")
DEFAULT_RANGE = "30D"
RANGE_DAYS    = {"1D": 1, "7D": 7, "30D": 30, "90D": 90, "360D": 360}
SECONDS_PER_DAY = 86_400

VALID_MODES            = {"bootstrap", "incremental", "recovery"}
VALID_STATUSES         = {"available", "partial", "unavailable", "invalid"}
VALID_QUALITY_STATUSES = {"ok", "partial", "invalid"}
VALID_COLOR_TOKENS     = {"positive", "negative", "warning", "neutral", "unavailable", "invalid"}
STATUS_PRIORITY        = {"available": 0, "partial": 1, "unavailable": 2, "invalid": 3}
CHART_IDS              = ("miner_reserve", "sopr_7d", "hashrate", "difficulty", "miner_net_position_change")
WIDGET_IDS             = ("miner_pressure", "reserve_trend", "net_position", "sopr_regime")
DRILLDOWN_IDS         = ("miner_outflow_distribution", "reserve_aging", "revenue_breakdown", "nupl_phases")
OPTIONAL_UNAVAILABLE  = ()

SERIES_CONFIG = {
    "miner_reserve":             ("miner_reserve_btc", "Miner Reserve (BTC)", "Total miner-held BTC", "area", "BTC", "Glassnode"),
    "sopr_7d":                   ("sopr_7d", "SOPR (7D)", "Spent Output Profit Ratio", "line", "ratio", "CryptoQuant"),
    "hashrate":                  ("hashrate_eh_s", "Hashrate (EH/s)", "Network hash rate", "area", "EH/s", "Glassnode"),
    "difficulty":                ("difficulty_t", "Difficulty (T)", "Network difficulty", "line", "T", "CryptoQuant"),
    "miner_net_position_change": ("miner_net_position_change", "Miner Net Position Change (BTC)", "Daily miner reserve delta", "bar", "BTC/day", "Derived"),
}
WIDGET_TITLES = {"miner_pressure": "MINER PRESSURE", "reserve_trend": "RESERVE TREND", "net_position": "NET POSITION", "sopr_regime": "SOPR REGIME"}


def _stable_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _timestamp(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def copy_json_safe_value(value: Any, *, path: str) -> tuple[Any, list[str]]:
    if value is None or isinstance(value, (str, bool, int)):
        return value, []
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, [f"non_finite_contract_value:{path}"]
        return (0.0 if value == 0.0 else value), []
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        errors: list[str] = []
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"non_string_contract_key:{path}")
                continue
            copied_child, child_errors = copy_json_safe_value(child, path=f"{path}.{key}")
            copied[key] = copied_child
            errors.extend(child_errors)
        return copied, errors
    if isinstance(value, (list, tuple)):
        copied_list: list[Any] = []
        errors: list[str] = []
        for index, child in enumerate(value):
            copied_child, child_errors = copy_json_safe_value(child, path=f"{path}[{index}]")
            copied_list.append(copied_child)
            errors.extend(child_errors)
        return copied_list, errors
    return None, [f"non_json_contract_value:{path}:{type(value).__name__}"]


def _messages(value: Any, path: str) -> tuple[list[str], list[str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return [], [f"invalid_upstream_messages:{path}"]
    messages: list[str] = []
    errors: list[str] = []
    for index, message in enumerate(value):
        if not isinstance(message, str):
            errors.append(f"invalid_upstream_message:{path}[{index}]")
        elif message not in messages:
            messages.append(message)
    return messages, errors


def _trim_decimal(value: float, decimals: int) -> str:
    text = f"{value:,.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_miner_reserve(value: Any) -> str:
    if not _finite(value):
        return "--"
    return f"{value / 1_000_000:.2f}M" if abs(value) >= 1_000_000 else _trim_decimal(value, 2)


def format_sopr(value: Any) -> str:
    return f"{value:.3f}" if _finite(value) else "--"


def format_one_decimal(value: Any) -> str:
    return f"{value:.1f}" if _finite(value) else "--"


def format_net_position(value: Any) -> str:
    if not _finite(value):
        return "--"
    if value == 0:
        return "0"
    formatted = _trim_decimal(abs(value), 2)
    return f"+{formatted}" if value > 0 else f"-{formatted}"


def format_currency(value: Any) -> str:
    if not _finite(value):
        return "--"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def format_percent(value: Any) -> str:
    return f"{value * 100:.2f}%" if _finite(value) else "--"


def format_price(value: Any) -> str:
    return f"${value:,.2f}" if _finite(value) else "--"


DISPLAY_FORMATTERS: dict[str, Callable[[Any], str]] = {
    "miner_reserve": format_miner_reserve, "sopr_7d": format_sopr, "hashrate": format_one_decimal,
    "difficulty": format_one_decimal, "miner_net_position_change": format_net_position,
}


def _validate_upstreams(processing: Any, classification: Any) -> list[str]:
    errors: list[str] = []
    for name, contract, stage in (("processing", processing, "processing"), ("classification", classification, "classification")):
        if not isinstance(contract, Mapping):
            errors.append(f"{name}_contract_must_be_mapping")
            continue
        if contract.get("family") != "on_chain_miners":
            errors.append(f"{name}_family_must_be_on_chain_miners")
        if contract.get("stage") != stage:
            errors.append(f"{name}_stage_must_be_{stage}")
        if contract.get("mode") not in VALID_MODES:
            errors.append(f"{name}_mode_invalid")
        for field in (("context", "series", "features", "quality") if name == "processing" else ("context", "classifications", "quality")):
            if not isinstance(contract.get(field), Mapping):
                errors.append(f"{name}_{field}_must_be_mapping")
    if errors:
        return errors
    for chart_id, (series_id, _, _, _, expected_unit, _) in SERIES_CONFIG.items():
        payload = processing["series"].get(series_id)
        if not isinstance(payload, Mapping):
            errors.append(f"missing_processing_series:{series_id}")
            continue
        for field in ("status", "unit", "records", "current", "warnings", "errors", "metadata"):
            if field not in payload:
                errors.append(f"missing_processing_series_field:{series_id}:{field}")
        if payload.get("unit") != expected_unit:
            errors.append(f"incompatible_processing_unit:{series_id}")
        if payload.get("status") not in VALID_STATUSES:
            errors.append(f"invalid_processing_series_status:{series_id}")
    for classification_id in WIDGET_IDS:
        payload = classification["classifications"].get(classification_id)
        if not isinstance(payload, Mapping):
            errors.append(f"missing_classification:{classification_id}")
            continue
        for field in ("classification_id", "status", "state", "signal", "display_label", "display_color_token", "source", "thresholds", "reason", "warnings", "errors"):
            if field not in payload:
                errors.append(f"missing_classification_field:{classification_id}:{field}")
        if payload.get("classification_id") != classification_id:
            errors.append(f"classification_id_mismatch:{classification_id}")
        if payload.get("status") not in VALID_STATUSES:
            errors.append(f"invalid_classification_status:{classification_id}")
        if payload.get("display_color_token") not in VALID_COLOR_TOKENS:
            errors.append(f"invalid_classification_color_token:{classification_id}")
    for series_id in ("miner_outflow_total_btc", "miners_unspent_supply_btc", "miner_revenue_total_usd", "miner_block_reward_revenue_usd",
                      "miner_fee_revenue_usd", "miner_fee_share_ratio", "nupl"):
        if not isinstance(processing["series"].get(series_id), Mapping):
            errors.append(f"missing_processing_series:{series_id}")
    for feature_id in ("miner_outflow_distribution", "reserve_age_context", "miner_revenue_breakdown", "nupl_phase_basis"):
        if not isinstance(processing["features"].get(feature_id), Mapping):
            errors.append(f"missing_processing_feature:{feature_id}")
    nupl_phase = classification["classifications"].get("nupl_phase")
    if not isinstance(nupl_phase, Mapping):
        errors.append("missing_classification:nupl_phase")
    elif nupl_phase.get("classification_id") != "nupl_phase" or nupl_phase.get("status") not in VALID_STATUSES:
        errors.append("invalid_classification:nupl_phase")
    for name, contract in (("processing", processing), ("classification", classification)):
        quality = contract["quality"]
        if quality.get("status") not in VALID_QUALITY_STATUSES:
            errors.append(f"invalid_{name}_quality_status")
        for field in ("warnings", "errors"):
            _, message_errors = _messages(quality.get(field), f"{name}.quality.{field}")
            errors.extend(message_errors)
    p_context, c_context = processing["context"], classification["context"]
    for field in ("asset", "data_mode", "is_demo", "reference_timestamp", "execution_timestamp", "generated_at"):
        if p_context.get(field) != c_context.get(field):
            errors.append(f"upstream_context_mismatch:{field}")
    if processing.get("mode") != classification.get("mode"):
        errors.append("upstream_context_mismatch:mode")
    return _stable_unique(errors)


def _data_as_of(processing: Mapping[str, Any], classification: Mapping[str, Any]) -> int | None:
    if processing.get("quality", {}).get("status") == "invalid" or classification.get("quality", {}).get("status") == "invalid":
        return None
    processing_value     = processing.get("quality", {}).get("data_as_of")
    classification_value = classification.get("quality", {}).get("data_as_of")
    return min(processing_value, classification_value) if _timestamp(processing_value) and _timestamp(classification_value) else None


def build_range_selector() -> dict[str, Any]:
    return {"options": [{"id": range_id, "days": RANGE_DAYS[range_id]} for range_id in RANGE_OPTIONS], "default": DEFAULT_RANGE,
            "source_resolution": "1D", "intraday_available": False}


def _empty_range(range_id: str, status: str, reason: str) -> dict[str, Any]:
    return {"range_id": range_id, "days": RANGE_DAYS[range_id], "status": status, "from_timestamp": None, "to_timestamp": None,
            "expected_points": RANGE_DAYS[range_id], "actual_points": 0, "coverage_ratio": 0.0, "points": [],
            "reason": reason, "warnings": [], "errors": []}


def _point(record: Mapping[str, Any], *, bar: bool) -> dict[str, Any]:
    point = {"timestamp": record["timestamp"], "value": record["value"]}
    if bar:
        point["bar_token"] = "positive" if record["value"] > 0 else "negative" if record["value"] < 0 else "neutral"
        point["unit"] = "BTC/day"
    return point


def build_series_ranges(series: Mapping[str, Any], *, data_as_of: int | None, bar: bool = False) -> tuple[dict[str, Any], list[str]]:
    if data_as_of is None:
        return {range_id: _empty_range(range_id, "unavailable", "screen_data_as_of_unavailable") for range_id in RANGE_OPTIONS}, []
    if series.get("status") == "invalid":
        return {range_id: _empty_range(range_id, "invalid", "source_series_invalid") for range_id in RANGE_OPTIONS}, []
    records = series.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return {range_id: _empty_range(range_id, "invalid", "source_records_invalid") for range_id in RANGE_OPTIONS}, ["source_records_invalid"]
    valid_records: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not _timestamp(record.get("timestamp")) or not _finite(record.get("value")):
            errors.append(f"invalid_chart_point:{index}")
        else:
            valid_records.append(record)
    if errors:
        return {range_id: _empty_range(range_id, "invalid", "source_points_invalid") for range_id in RANGE_OPTIONS}, errors
    output: dict[str, Any] = {}
    for range_id in RANGE_OPTIONS:
        days = RANGE_DAYS[range_id]
        start = data_as_of - (days - 1) * SECONDS_PER_DAY
        points = [_point(record, bar=bar) for record in valid_records if start <= record["timestamp"] <= data_as_of]
        actual = len(points)
        status = "available" if actual == days else "partial" if actual else "unavailable"
        output[range_id] = {"range_id": range_id, "days": days, "status": status, "from_timestamp": start, "to_timestamp": data_as_of,
                            "expected_points": days, "actual_points": actual, "coverage_ratio": min(actual / days, 1.0), "points": points,
                            "reason": None if status == "available" else "range_history_partial" if status == "partial" else "range_history_unavailable",
                            "warnings": [], "errors": []}
    return output, []


def _current(series: Mapping[str, Any], chart_id: str) -> tuple[dict[str, Any], list[str]]:
    current = series.get("current") if isinstance(series.get("current"), Mapping) else {}
    status  = str(current.get("status", series.get("status", "invalid")))
    if status not in VALID_STATUSES:
        status = "invalid"
    value = current.get("value")
    timestamp = current.get("timestamp")
    errors: list[str] = []
    if status in {"available", "partial"} and (not _finite(value) or not _timestamp(timestamp)):
        errors.append("current_value_or_timestamp_invalid")
        status, value, timestamp = "invalid", None, None
    elif status in {"unavailable", "invalid"}:
        value, timestamp = None, None
    payload = {"status": status, "timestamp": timestamp, "value": 0.0 if isinstance(value, float) and value == 0 else value,
               "unit": series.get("unit"), "display_value": DISPLAY_FORMATTERS[chart_id](value)}
    return payload, errors


def build_chart(chart_id: str, processing: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    series_id, title, subtitle, chart_type, unit, provider = SERIES_CONFIG[chart_id]
    series = processing["series"][series_id]
    ranges, range_errors = build_series_ranges(series, data_as_of=data_as_of, bar=chart_type == "bar")
    current, current_errors = _current(series, chart_id)
    warnings, warning_errors = _messages(series.get("warnings"), f"processing.series.{series_id}.warnings")
    errors, error_errors = _messages(series.get("errors"), f"processing.series.{series_id}.errors")
    if range_errors or current_errors or warning_errors or error_errors or series.get("status") == "invalid" or current["status"] == "invalid":
        status = "invalid"
    elif series.get("status") == "unavailable" or current["status"] == "unavailable":
        status = "unavailable"
    elif series.get("status") == "partial" or current["status"] == "partial":
        status = "partial"
    else:
        status = "available"
    chart = {"chart_id": chart_id, "title": title, "subtitle": subtitle, "chart_type": chart_type, "unit": unit, "provider": provider,
             "status": status, "current": current, "series_by_range": ranges, "warnings": warnings, "errors": errors}
    available_records = len(series.get("records", [])) if isinstance(series.get("records"), Sequence) else 0
    chart["calculation_history"] = {"records_available": available_records,
        "first_timestamp": series.get("records", [{}])[0].get("timestamp") if available_records else None,
        "last_timestamp": series.get("records", [{}])[-1].get("timestamp") if available_records else None,
        "source_resolution": "1d", "fabricated_records": 0}
    chart["history_contract"] = {"requested_days": 360, "available_days": available_records,
        "status": "available" if available_records >= 360 else "blocked_upstream",
        "reason": None if available_records >= 360 else "provider_history_beyond_available_daily_records_unproven"}
    if chart_id != "miner_net_position_change":
        chart["preferred_representation"] = chart_type
        chart["ohlc_contract"] = {"status": "blocked_upstream", "reason": "daily_scalar_source_has_no_intra_bucket_observations",
            "source_resolution": "1d_scalar", "required_capability": "multiple_temporal_observations_per_daily_bucket",
            "fabricated_ohlc": False}
    if chart_id == "sopr_7d":
        chart["reference_lines"] = [{"value": 1.0, "label": "Breakeven", "token": "neutral"}]
    if chart_id == "miner_net_position_change":
        chart.update({"source_provider": "Glassnode", "calculation_source": "miner_reserve_btc"})
    return chart, [*range_errors, *current_errors, *warning_errors, *error_errors]


def build_widget(widget_id: str, classification: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    item = classification["classifications"][widget_id]
    source, source_errors = copy_json_safe_value(item.get("source"), path=f"widgets.{widget_id}.source")
    thresholds, threshold_errors = copy_json_safe_value(item.get("thresholds"), path=f"widgets.{widget_id}.thresholds")
    warnings, warning_errors = _messages(item.get("warnings"), f"classification.{widget_id}.warnings")
    errors, error_errors = _messages(item.get("errors"), f"classification.{widget_id}.errors")
    copy_errors = [*source_errors, *threshold_errors, *warning_errors, *error_errors]
    status = "invalid" if copy_errors else str(item.get("status"))
    valid  = status in {"available", "partial"} and item.get("state") is not None
    label  = item.get("display_label")
    raw_value = source.get("value") if isinstance(source, Mapping) else None
    display = format_net_position(raw_value) if widget_id == "net_position" and valid else str(label) if valid else "--"
    widget = {"widget_id": widget_id, "title": WIDGET_TITLES[widget_id], "status": status, "state": item.get("state") if valid else None,
              "signal": item.get("signal") if valid else None, "classification_label": label, "display_value": display,
              "display_color_token": item.get("display_color_token") if valid else "invalid" if status == "invalid" else "unavailable",
              "source": source, "thresholds": thresholds, "reason": item.get("reason"), "warnings": warnings, "errors": errors}
    if widget_id == "net_position":
        widget.update({"raw_value": raw_value if valid else None, "unit": source.get("unit") if isinstance(source, Mapping) else "BTC/day"})
    return widget, copy_errors


def _record_ranges(records: Any, *, data_as_of: int | None, item_key: str, point_builder: Callable[[Mapping[str, Any]], dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    if data_as_of is None:
        ranges = {range_id: {**_empty_range(range_id, "unavailable", "screen_data_as_of_unavailable"), item_key: []} for range_id in RANGE_OPTIONS}
        for payload in ranges.values():
            payload.pop("points", None)
        return ranges, []
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return {}, ["source_records_invalid"]
    valid: list[Mapping[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not _timestamp(record.get("timestamp")):
            return {}, [f"source_record_invalid:{index}"]
        valid.append(record)
    output: dict[str, Any] = {}
    for range_id in RANGE_OPTIONS:
        days = RANGE_DAYS[range_id]
        start = data_as_of - (days - 1) * SECONDS_PER_DAY
        points = [point_builder(record) for record in valid if start <= record["timestamp"] <= data_as_of]
        actual = len(points)
        status = "available" if actual == days else "partial" if actual else "unavailable"
        output[range_id] = {"range_id": range_id, "days": days, "status": status, "from_timestamp": start, "to_timestamp": data_as_of,
                            "expected_points": days, "actual_points": actual, "coverage_ratio": min(actual / days, 1.0), item_key: points,
                            "reason": None if status == "available" else "range_history_partial" if status == "partial" else "range_history_unavailable",
                            "warnings": [], "errors": []}
    return output, []


def _drilldown_status(source_status: Any, *, usable: bool, errors: Sequence[str]) -> tuple[str, bool]:
    status = str(source_status) if source_status in VALID_STATUSES else "invalid"
    if errors:
        status = "invalid"
    return status, status == "available" or (status == "partial" and usable)


def _combined_status(*statuses: Any) -> str:
    normalized = [str(status) if status in VALID_STATUSES else "invalid" for status in statuses]
    return max(normalized, key=lambda status: STATUS_PRIORITY[status])


def build_miner_outflow_drilldown(processing: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    feature = processing["features"]["miner_outflow_distribution"]
    aggregate_series = processing["series"]["miner_outflow_total_btc"]
    warnings, warning_errors = _messages(feature.get("warnings"), "processing.features.miner_outflow_distribution.warnings")
    errors, error_errors = _messages(feature.get("errors"), "processing.features.miner_outflow_distribution.errors")
    build_errors = [*warning_errors, *error_errors]
    records = feature.get("records", [])
    ranges, range_errors = _record_ranges(records, data_as_of=data_as_of, item_key="points", point_builder=lambda record: copy.deepcopy(dict(record)))
    build_errors.extend(range_errors)
    feature_current = feature.get("current", {})
    current_status = feature_current.get("status") if isinstance(feature_current, Mapping) else "invalid"
    timestamp = feature_current.get("timestamp") if isinstance(feature_current, Mapping) else None
    exact = next((record for record in records if isinstance(record, Mapping) and record.get("timestamp") == timestamp), None) if _timestamp(timestamp) else None
    if current_status in {"available", "partial"} and exact:
        current = {"status": current_status, "timestamp": timestamp, "value": exact.get("aggregate_outflow_total_btc"),
                   "aggregate_outflow_total_btc": exact.get("aggregate_outflow_total_btc"),
                   "display_value": format_net_position(exact.get("aggregate_outflow_total_btc")), "top_pool_symbol": exact.get("top_pool_symbol"),
                   "top1_share_ratio": exact.get("top1_share_ratio"), "top3_share_ratio": exact.get("top3_share_ratio"),
                   "expected_active_pools": exact.get("expected_active_pools"), "observed_active_pools": exact.get("observed_active_pools"),
                   "missing_active_pools": copy.deepcopy(exact.get("missing_active_pools", [])), "pools": copy.deepcopy(exact.get("pools", []))}
    else:
        current = {"status": current_status if current_status in VALID_STATUSES else "invalid", "timestamp": None, "value": None,
                   "aggregate_outflow_total_btc": None,
                   "display_value": "--", "top_pool_symbol": None, "top1_share_ratio": None, "top3_share_ratio": None,
                   "expected_active_pools": None, "observed_active_pools": None, "missing_active_pools": [], "pools": []}
        if current_status in {"available", "partial"}:
            build_errors.append("outflow_current_not_in_records")
    status, enabled = _drilldown_status(_combined_status(feature.get("status"), aggregate_series.get("status")), usable=exact is not None, errors=build_errors)
    return {"drilldown_id": "miner_outflow_distribution", "title": "Miner outflow distribution", "status": status, "enabled": enabled,
            "unit": "BTC/day", "current": current, "active_symbols": copy.deepcopy(feature.get("active_symbols", [])),
            "inactive_symbols": copy.deepcopy(feature.get("inactive_symbols", [])), "series_by_range": ranges,
            "metadata": {"data_as_of": feature.get("metadata", {}).get("data_as_of")}, "warnings": warnings, "errors": errors}, build_errors


def build_reserve_age_drilldown(processing: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    feature = processing["features"]["reserve_age_context"]
    miners = processing["series"]["miners_unspent_supply_btc"]
    warnings, warning_errors = _messages(feature.get("warnings"), "processing.features.reserve_age_context.warnings")
    errors, error_errors = _messages(feature.get("errors"), "processing.features.reserve_age_context.errors")
    miner_ranges, miner_errors = _record_ranges(miners.get("records"), data_as_of=data_as_of, item_key="points",
                                                point_builder=lambda record: {"timestamp": record.get("timestamp"), "value": record.get("value"), "unit": "BTC"})
    network = feature.get("network_context", {})
    snapshot_ranges, snapshot_errors = _record_ranges(network.get("records") if isinstance(network, Mapping) else None, data_as_of=data_as_of,
                                                       item_key="snapshots", point_builder=lambda record: {"timestamp": record.get("timestamp"),
                                                       "network_total_native_btc": record.get("network_total_native_btc"),
                                                       "bands": copy.deepcopy(record.get("bands", {}))})
    build_errors = [*warning_errors, *error_errors, *miner_errors, *snapshot_errors]
    miner_current = copy.deepcopy(miners.get("current", {}))
    network_current = copy.deepcopy(network.get("current", {})) if isinstance(network, Mapping) else {}
    usable = miner_current.get("status") in {"available", "partial"} or network_current.get("status") in {"available", "partial"}
    status, enabled = _drilldown_status(_combined_status(feature.get("status"), miners.get("status")), usable=usable, errors=build_errors)
    return {"drilldown_id": "reserve_aging", "title": "Reserve Age Context", "status": status, "enabled": enabled,
            "semantic_scope": {"miner_specific": "coinbase_outputs_never_moved", "network_context": "bitcoin_network_utxo_age_distribution",
                               "network_context_is_miner_specific": False},
            "miner_specific": {"title": "Miner Unspent Supply", "scope": "miner_specific", "unit": "BTC", "current": miner_current,
                               "series_by_range": miner_ranges},
            "network_context": {"title": "Bitcoin UTXO Age Distribution", "scope": "bitcoin_network", "is_miner_specific": False,
                                "current": network_current, "snapshots_by_range": snapshot_ranges},
            "metadata": {"data_as_of": feature.get("metadata", {}).get("data_as_of")}, "warnings": warnings, "errors": errors}, build_errors


def build_revenue_drilldown(processing: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    feature = processing["features"]["miner_revenue_breakdown"]
    warnings, warning_errors = _messages(feature.get("warnings"), "processing.features.miner_revenue_breakdown.warnings")
    errors, error_errors = _messages(feature.get("errors"), "processing.features.miner_revenue_breakdown.errors")
    records = feature.get("records", [])
    ranges, range_errors = _record_ranges(records, data_as_of=data_as_of, item_key="points", point_builder=lambda record: copy.deepcopy(dict(record)))
    build_errors = [*warning_errors, *error_errors, *range_errors]
    feature_current = feature.get("current", {})
    timestamp = feature_current.get("timestamp") if isinstance(feature_current, Mapping) else None
    exact = next((record for record in records if isinstance(record, Mapping) and record.get("timestamp") == timestamp), None) if _timestamp(timestamp) else None
    if feature_current.get("status") in {"available", "partial"} and exact:
        current = copy.deepcopy(dict(exact))
        current["status"] = feature_current["status"]
        current["display"] = {"total_revenue": format_currency(exact.get("total_revenue_usd")),
                              "block_reward_revenue": format_currency(exact.get("block_reward_revenue_usd")),
                              "fee_revenue": format_currency(exact.get("fee_revenue_usd")),
                              "fee_share": format_percent(exact.get("derived_fee_share_ratio"))}
    else:
        current = {"status": feature_current.get("status", "invalid"), "timestamp": None, "total_revenue_usd": None,
                   "block_reward_revenue_usd": None, "fee_revenue_usd": None, "derived_fee_share_ratio": None,
                   "derived_fee_share_percent": None, "provider_fee_value": None, "provider_fee_scale": None,
                   "provider_fee_ratio": None, "provider_fee_difference_ratio": None,
                   "display": {"total_revenue": "--", "block_reward_revenue": "--", "fee_revenue": "--", "fee_share": "--"}}
        if feature_current.get("status") in {"available", "partial"}:
            build_errors.append("revenue_current_not_in_records")
    source_status = _combined_status(feature.get("status"), *(processing["series"][series_id].get("status") for series_id in
                                     ("miner_revenue_total_usd", "miner_block_reward_revenue_usd", "miner_fee_revenue_usd", "miner_fee_share_ratio")))
    status, enabled = _drilldown_status(source_status, usable=exact is not None, errors=build_errors)
    return {"drilldown_id": "revenue_breakdown", "title": "Miner revenue breakdown", "status": status, "enabled": enabled,
            "unit": "USD/day", "current": current, "series_by_range": ranges, "metadata": copy.deepcopy(feature.get("metadata", {})),
            "warnings": warnings, "errors": errors}, build_errors


def _phase_bands(thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"state": "capitulation", "minimum": None, "maximum": thresholds.get("capitulation_max"),
             "minimum_inclusive": False, "maximum_inclusive": False},
            {"state": "hope_fear", "minimum": thresholds.get("capitulation_max"), "maximum": thresholds.get("hope_fear_max"),
             "minimum_inclusive": True, "maximum_inclusive": False},
            {"state": "optimism_anxiety", "minimum": thresholds.get("hope_fear_max"), "maximum": thresholds.get("optimism_anxiety_max"),
             "minimum_inclusive": True, "maximum_inclusive": False},
            {"state": "belief_denial", "minimum": thresholds.get("optimism_anxiety_max"), "maximum": thresholds.get("belief_denial_max"),
             "minimum_inclusive": True, "maximum_inclusive": False},
            {"state": "euphoria_greed", "minimum": thresholds.get("belief_denial_max"), "maximum": None,
             "minimum_inclusive": True, "maximum_inclusive": False}]


def build_nupl_drilldown(processing: Mapping[str, Any], classification: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    series = processing["series"]["nupl"]
    basis = processing["features"]["nupl_phase_basis"]
    phase_source = classification["classifications"]["nupl_phase"]
    warnings, warning_errors = _messages(phase_source.get("warnings"), "classification.nupl_phase.warnings")
    errors, error_errors = _messages(phase_source.get("errors"), "classification.nupl_phase.errors")
    ranges, range_errors = _record_ranges(series.get("records"), data_as_of=data_as_of, item_key="points",
                                           point_builder=lambda record: {"timestamp": record.get("timestamp"), "value": record.get("value"),
                                           "price_usd": record.get("price_usd")})
    build_errors = [*warning_errors, *error_errors, *range_errors]
    basis_current = basis.get("current", {})
    if isinstance(basis_current, Mapping) and basis_current.get("status") in {"available", "partial"}:
        current = {"status": basis_current.get("status"), "timestamp": basis_current.get("timestamp"), "value": basis_current.get("value"),
                   "price_usd": basis_current.get("price_usd"), "display_value": format_sopr(basis_current.get("value")),
                   "display_price": format_price(basis_current.get("price_usd"))}
    else:
        current = {"status": basis_current.get("status", "unavailable") if isinstance(basis_current, Mapping) else "invalid", "timestamp": None,
                   "value": None, "price_usd": None, "display_value": "--", "display_price": "--"}
    thresholds = copy.deepcopy(phase_source.get("thresholds", {}))
    phase = {"status": phase_source.get("status"), "state": phase_source.get("state"), "signal": phase_source.get("signal"),
             "classification_label": phase_source.get("display_label"), "display_color_token": phase_source.get("display_color_token"),
             "reason": phase_source.get("reason"), "thresholds": thresholds,
             "previous": copy.deepcopy(phase_source.get("source", {}).get("previous")),
             "change_1d": phase_source.get("source", {}).get("change_1d")}
    usable = current["status"] in {"available", "partial"} and phase_source.get("state") is not None
    status, enabled = _drilldown_status(_combined_status(phase_source.get("status"), basis.get("status"), series.get("status")), usable=usable,
                                        errors=build_errors)
    return {"drilldown_id": "nupl_phases", "title": "NUPL phases", "status": status, "enabled": enabled, "unit": "ratio",
            "current": current, "phase": phase, "series_by_range": ranges, "phase_bands": _phase_bands(thresholds),
            "metadata": {"data_as_of": phase_source.get("source", {}).get("timestamp")}, "warnings": warnings, "errors": errors}, build_errors


def build_drilldowns(processing: Mapping[str, Any], classification: Mapping[str, Any], *, data_as_of: int | None) -> tuple[dict[str, Any], list[str]]:
    drilldowns: dict[str, Any] = {}
    errors: list[str] = []
    builders = (("miner_outflow_distribution", lambda: build_miner_outflow_drilldown(processing, data_as_of=data_as_of)),
                ("reserve_aging", lambda: build_reserve_age_drilldown(processing, data_as_of=data_as_of)),
                ("revenue_breakdown", lambda: build_revenue_drilldown(processing, data_as_of=data_as_of)),
                ("nupl_phases", lambda: build_nupl_drilldown(processing, classification, data_as_of=data_as_of)))
    for drilldown_id, builder in builders:
        drilldowns[drilldown_id], item_errors = builder()
        item = drilldowns[drilldown_id]
        ranges = item.get("series_by_range") or item.get("miner_specific", {}).get("series_by_range") or item.get("network_context", {}).get("snapshots_by_range") or {}
        item["calculation_history"] = {"source": "processing_full_available_history", "fabricated_records": 0,
            "ranges": {range_id: {"actual_points": payload.get("actual_points"), "expected_points": payload.get("expected_points"),
                "status": payload.get("status")} for range_id, payload in ranges.items() if isinstance(payload, Mapping)}}
        errors.extend(f"drilldown_build_error:{drilldown_id}:{error}" for error in item_errors)
    return drilldowns, errors


def _fallback(mode: Any, errors: Sequence[str]) -> dict[str, Any]:
    ranges = {range_id: _empty_range(range_id, "invalid", "invalid_upstream_contract") for range_id in RANGE_OPTIONS}
    charts = {}
    for chart_id, (_, title, subtitle, chart_type, unit, provider) in SERIES_CONFIG.items():
        charts[chart_id] = {"chart_id": chart_id, "title": title, "subtitle": subtitle, "chart_type": chart_type, "unit": unit, "provider": provider,
                            "status": "invalid", "current": {"status": "unavailable", "timestamp": None, "value": None, "unit": unit, "display_value": "--"},
                            "series_by_range": copy.deepcopy(ranges), "warnings": [], "errors": []}
    widgets = {widget_id: {"widget_id": widget_id, "title": WIDGET_TITLES[widget_id], "status": "invalid", "state": None, "signal": None,
                           "classification_label": "INVALID", "display_value": "--", "display_color_token": "invalid", "source": {}, "thresholds": {},
                           "reason": "invalid_upstream_contract", "warnings": [], "errors": []} for widget_id in WIDGET_IDS}
    drilldown_titles = {"miner_outflow_distribution": "Miner outflow distribution", "reserve_aging": "Reserve Age Context",
                        "revenue_breakdown": "Miner revenue breakdown", "nupl_phases": "NUPL phases"}
    drilldowns = {drilldown_id: {"drilldown_id": drilldown_id, "title": drilldown_titles[drilldown_id], "status": "invalid", "enabled": False,
                                 "current": {"status": "unavailable", "timestamp": None, "value": None, "display_value": "--"},
                                 "warnings": [], "errors": ["invalid_upstream_contract"]} for drilldown_id in DRILLDOWN_IDS}
    context = {"asset": None, "data_mode": None, "is_demo": None, "reference_timestamp": None, "execution_timestamp": None, "generated_at": None,
               "processing_data_as_of": None, "classification_data_as_of": None, "data_as_of": None,
               "calculation_history": "full_available_history", "presentation_default_range": DEFAULT_RANGE}
    quality = {"status": "invalid", "availability": {"charts": {chart_id: "invalid" for chart_id in CHART_IDS},
                                                       "widgets": {widget_id: "invalid" for widget_id in WIDGET_IDS},
                                                       "drilldowns": {drilldown_id: "invalid" for drilldown_id in DRILLDOWN_IDS}}, "data_as_of": None,
               "processing_status": "invalid", "classification_status": "invalid", "missing_fields": [*CHART_IDS, *WIDGET_IDS, *DRILLDOWN_IDS],
               "warnings": [], "errors": _stable_unique(list(errors)), "optional_unavailable": []}
    return {"schema": {"id": ON_CHAIN_MINERS_CONTRACT_SCHEMA, "version": ON_CHAIN_MINERS_CONTRACT_VERSION},
            "screen": {"id": ON_CHAIN_MINERS_SCREEN_ID, "route": ON_CHAIN_MINERS_ROUTE, "title": ON_CHAIN_MINERS_TITLE, "family": "on_chain_miners"},
            "stage": "screen_contract", "mode": mode if mode in VALID_MODES else None, "context": context, "range_selector": build_range_selector(),
            "operational_status": {"data_mode": None, "is_demo": None, "quality_status": "invalid", "connection_status": "not_reported",
                                   "cache_status": "not_reported", "generated_at": None, "data_as_of": None},
            "charts": charts, "widgets": widgets, "drilldowns": drilldowns, "quality": quality}


def evaluate_screen_quality(*, processing: Mapping[str, Any], classification: Mapping[str, Any], charts: Mapping[str, Any],
                            widgets: Mapping[str, Any], drilldowns: Mapping[str, Any], data_as_of: int | None, build_errors: Sequence[str]) -> dict[str, Any]:
    chart_availability  = {chart_id: str(charts[chart_id]["status"]) for chart_id in CHART_IDS}
    widget_availability = {widget_id: str(widgets[widget_id]["status"]) for widget_id in WIDGET_IDS}
    drilldown_availability = {drilldown_id: str(drilldowns[drilldown_id]["status"]) for drilldown_id in DRILLDOWN_IDS}
    p_quality, c_quality = processing["quality"], classification["quality"]
    p_warnings, p_warning_errors = _messages(p_quality.get("warnings"), "processing.quality.warnings")
    p_errors, p_error_errors     = _messages(p_quality.get("errors"), "processing.quality.errors")
    c_warnings, c_warning_errors = _messages(c_quality.get("warnings"), "classification.quality.warnings")
    c_errors, c_error_errors     = _messages(c_quality.get("errors"), "classification.quality.errors")
    warnings = [*(f"processing_warning:{message}" for message in p_warnings), *(f"classification_warning:{message}" for message in c_warnings)]
    errors = [*(f"processing_error:{message}" for message in p_errors), *(f"classification_error:{message}" for message in c_errors),
              *build_errors, *p_warning_errors, *p_error_errors, *c_warning_errors, *c_error_errors]
    for chart_id, chart in charts.items():
        warnings.extend(f"chart_warning:{chart_id}:{message}" for message in chart["warnings"])
        errors.extend(f"chart_error:{chart_id}:{message}" for message in chart["errors"])
    for widget_id, widget in widgets.items():
        warnings.extend(f"widget_warning:{widget_id}:{message}" for message in widget["warnings"])
        errors.extend(f"widget_error:{widget_id}:{message}" for message in widget["errors"])
    for drilldown_id, drilldown in drilldowns.items():
        warnings.extend(f"drilldown_warning:{drilldown_id}:{message}" for message in drilldown["warnings"])
        errors.extend(f"drilldown_error:{drilldown_id}:{message}" for message in drilldown["errors"])
    all_availability = (*chart_availability.values(), *widget_availability.values(), *drilldown_availability.values())
    missing = [name for name, status in {**chart_availability, **widget_availability, **drilldown_availability}.items() if status in {"unavailable", "invalid"}]
    semantic = any(status in {"available", "partial"} for status in all_availability)
    if p_quality.get("status") == "invalid" or c_quality.get("status") == "invalid" or errors or "invalid" in all_availability:
        status = "invalid"
    elif p_quality.get("status") == "ok" and c_quality.get("status") == "ok" and all(value == "available" for value in all_availability) and data_as_of is not None:
        status = "ok"
    else:
        status = "partial" if semantic else "invalid"
    if status == "partial" and not warnings and not errors and not missing:
        warnings.append("screen_quality_partial")
    return {"status": status, "availability": {"charts": chart_availability, "widgets": widget_availability, "drilldowns": drilldown_availability},
            "data_as_of": data_as_of if status != "invalid" else None, "processing_status": p_quality.get("status"),
            "classification_status": c_quality.get("status"), "missing_fields": _stable_unique(missing), "warnings": _stable_unique(warnings),
            "errors": _stable_unique(errors), "optional_unavailable": []}


class OnChainMinersContractBuilder:
    def __init__(self, processing_contract: Mapping[str, Any], classification_contract: Mapping[str, Any]) -> None:
        self.processing_contract     = processing_contract
        self.classification_contract = classification_contract

    def build(self) -> dict[str, Any]:
        errors = _validate_upstreams(self.processing_contract, self.classification_contract)
        mode   = self.processing_contract.get("mode") if isinstance(self.processing_contract, Mapping) else None
        if errors:
            return _fallback(mode, errors)
        processing, classification = self.processing_contract, self.classification_contract
        context = processing["context"]
        data_as_of = _data_as_of(processing, classification)
        output_context = {"asset": context.get("asset"), "data_mode": context.get("data_mode"), "is_demo": context.get("is_demo"),
                          "reference_timestamp": context.get("reference_timestamp"), "execution_timestamp": context.get("execution_timestamp"),
                          "generated_at": context.get("generated_at"), "processing_data_as_of": processing["quality"].get("data_as_of"),
                          "classification_data_as_of": classification["quality"].get("data_as_of"), "data_as_of": data_as_of,
                          "calculation_history": "full_available_history", "presentation_default_range": DEFAULT_RANGE}
        charts: dict[str, Any] = {}
        widgets: dict[str, Any] = {}
        build_errors: list[str] = []
        for chart_id in CHART_IDS:
            charts[chart_id], chart_errors = build_chart(chart_id, processing, data_as_of=data_as_of)
            build_errors.extend(f"chart_build_error:{chart_id}:{error}" for error in chart_errors)
        for widget_id in WIDGET_IDS:
            widgets[widget_id], widget_errors = build_widget(widget_id, classification)
            build_errors.extend(f"widget_build_error:{widget_id}:{error}" for error in widget_errors)
        drilldowns, drilldown_errors = build_drilldowns(processing, classification, data_as_of=data_as_of)
        build_errors.extend(drilldown_errors)
        quality = evaluate_screen_quality(processing=processing, classification=classification, charts=charts, widgets=widgets, drilldowns=drilldowns,
                                          data_as_of=data_as_of, build_errors=build_errors)
        output_context["data_as_of"] = quality["data_as_of"]
        output = {"schema": {"id": ON_CHAIN_MINERS_CONTRACT_SCHEMA, "version": ON_CHAIN_MINERS_CONTRACT_VERSION},
                  "screen": {"id": ON_CHAIN_MINERS_SCREEN_ID, "route": ON_CHAIN_MINERS_ROUTE, "title": ON_CHAIN_MINERS_TITLE, "family": "on_chain_miners"},
                  "stage": "screen_contract", "mode": mode, "context": output_context, "range_selector": build_range_selector(),
                  "operational_status": {"data_mode": context.get("data_mode"), "is_demo": context.get("is_demo"), "quality_status": quality["status"],
                                         "connection_status": "not_reported", "cache_status": "not_reported", "generated_at": context.get("generated_at"),
                                         "data_as_of": quality["data_as_of"]},
                  "charts": charts, "widgets": widgets, "drilldowns": drilldowns,
                  "technical_analysis": {"status": "blocked_upstream", "recalculate_in_hmi": False,
                      "targets": {chart_id: {"status": "blocked_upstream", "reason": "daily_scalar_source_has_no_legitimate_ohlc",
                          "required_capability": "multiple_temporal_observations_per_daily_bucket", "indicator_ids": [],
                          "technical_event_ids": []} for chart_id in ("miner_reserve", "sopr_7d", "hashrate", "difficulty")},
                      "event_indexes": {"by_id": {}, "technical_event_ids": []}},
                  "history_contract": {"requested_days": 360, "available_days": min(
                      len(processing["series"][series_id].get("records", [])) for series_id, *_ in SERIES_CONFIG.values()),
                      "status": "blocked_upstream", "fabricated_records": 0},
                  "quality": quality}
        output = align_on_chain_miners_to_sp_v2_0(output, processing, classification)
        copied, copy_errors = copy_json_safe_value(output, path="screen_contract")
        if copy_errors:
            output = _fallback(mode, copy_errors)
        else:
            output = copied
        try:
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            output = _fallback(mode, [f"screen_contract_serialization_failed:{type(exc).__name__}"])
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        return output


def build_on_chain_miners_screen_contract(processing_contract: Mapping[str, Any], classification_contract: Mapping[str, Any]) -> dict[str, Any]:
    processing_before, _ = copy_json_safe_value(processing_contract, path="processing")
    classification_before, _ = copy_json_safe_value(classification_contract, path="classification")
    output = OnChainMinersContractBuilder(processing_contract, classification_contract).build()
    processing_after, _ = copy_json_safe_value(processing_contract, path="processing")
    classification_after, _ = copy_json_safe_value(classification_contract, path="classification")
    if processing_before != processing_after or classification_before != classification_after:
        raise RuntimeError("Contract Builder mutated an upstream contract")
    return output
