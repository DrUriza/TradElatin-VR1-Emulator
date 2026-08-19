"""Validation and normalization for open-interest and funding Input."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .open_interest_and_funding_data_raw_extract import (
    FAMILY,
    SCREEN_TIMEFRAMES,
    TIMEFRAME_SECONDS,
    OpenInterestAndFundingFetcher,
    OpenInterestAndFundingRawExtractor,
)

OHLC_FIELDS = ("open", "high", "low", "close")
OI_SNAPSHOT_NUMERIC_FIELDS = ("open_interest_usd", "open_interest_quantity", "open_interest_by_stable_coin_margin",
    "open_interest_quantity_by_coin_margin", "open_interest_quantity_by_stable_coin_margin", "open_interest_change_percent_5m",
    "open_interest_change_percent_15m", "open_interest_change_percent_30m", "open_interest_change_percent_1h",
    "open_interest_change_percent_4h", "open_interest_change_percent_24h")
CONFIRMATION_METADATA = {
    "cryptoquant_open_interest": {"provider": "cryptoquant", "endpoint_id": "open_interest", "unit": "USD"},
    "cryptoquant_funding_rates": {"provider": "cryptoquant", "endpoint_id": "funding_rates", "unit": "percent"},
    "glassnode_futures_open_interest_sum": {"provider": "glassnode", "endpoint_id": "futures_open_interest_sum", "unit": "USD"},
    "glassnode_futures_funding_rate_perpetual": {"provider": "glassnode", "endpoint_id": "futures_funding_rate_perpetual", "unit": "percent"},
    "glassnode_futures_estimated_leverage_ratio": {"provider": "glassnode", "endpoint_id": "futures_estimated_leverage_ratio", "unit": "ratio"},
}


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid_{name}")
    return value.strip()


def _zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def normalize_finite_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("invalid_numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_numeric") from exc
    if not math.isfinite(result):
        raise ValueError("invalid_numeric")
    return _zero(result)


def normalize_non_negative_float(value: Any) -> float:
    result = normalize_finite_float(value)
    if result < 0:
        raise ValueError("negative_value")
    return result


def normalize_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_positive_int")
    result = normalize_finite_float(value)
    if result <= 0 or not result.is_integer():
        raise ValueError("invalid_positive_int")
    return int(result)


def normalize_timestamp_utc(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_timestamp")
    if isinstance(value, (int, float)):
        number = normalize_finite_float(value)
        if number < 0 or not number.is_integer():
            raise ValueError("invalid_timestamp")
        integer = int(number)
        return integer // 1000 if integer >= 100_000_000_000 else integer
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid_timestamp")
    text = value.strip()
    if text.replace(".", "", 1).isdigit():
        number = normalize_finite_float(text)
        if not number.is_integer():
            raise ValueError("invalid_timestamp")
        return normalize_timestamp_utc(int(number))
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    result = int(parsed.astimezone(timezone.utc).timestamp())
    if result < 0:
        raise ValueError("invalid_timestamp")
    return result


def unwrap_coinglass_response(response: Any) -> list[Any]:
    if not isinstance(response, Mapping) or response.get("code") not in (0, "0", 200, "200") or not _sequence(response.get("data")):
        raise ValueError("invalid_coinglass_envelope")
    return copy.deepcopy(list(response["data"]))


def unwrap_cryptoquant_response(response: Any) -> tuple[str | None, list[Any]]:
    if not isinstance(response, Mapping) or not isinstance(response.get("status"), Mapping) or response["status"].get("code") not in (200, "200"):
        raise ValueError("invalid_cryptoquant_envelope")
    result = response.get("result")
    if not isinstance(result, Mapping) or not _sequence(result.get("data")):
        raise ValueError("invalid_cryptoquant_envelope")
    window = result.get("window")
    if not isinstance(window, str) or window != "hour":
        raise ValueError("invalid_cryptoquant_window")
    return window, copy.deepcopy(list(result["data"]))


def unwrap_glassnode_response(response: Any) -> list[Any]:
    if not _sequence(response):
        raise ValueError("invalid_glassnode_envelope")
    return copy.deepcopy(list(response))


def normalize_coinglass_ohlc_record(record: Mapping[str, Any], *, metric_id: str) -> dict[str, Any]:
    if not isinstance(record, Mapping) or metric_id not in {"open_interest_ohlc", "funding_rate_ohlc"}:
        raise ValueError("invalid_ohlc_record")
    number = normalize_non_negative_float if metric_id == "open_interest_ohlc" else normalize_finite_float
    output = {"timestamp": normalize_timestamp_utc(record.get("time"))}
    output.update({field: number(record.get(field)) for field in OHLC_FIELDS})
    if output["high"] < max(output["open"], output["close"], output["low"]) or output["low"] > min(output["open"], output["close"], output["high"]):
        raise ValueError("inconsistent_ohlc")
    return output


def normalize_open_interest_exchange_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_open_interest_exchange_record")
    symbol = _text(record.get("symbol"), "symbol")
    if symbol.casefold() != "btc":
        raise ValueError("asset_not_btc")
    output = {"exchange": _text(record.get("exchange"), "exchange"), "symbol": "BTC"}
    for field in OI_SNAPSHOT_NUMERIC_FIELDS:
        value = record.get(field)
        output[field] = None if value is None else (normalize_non_negative_float(value) if "change_percent" not in field else normalize_finite_float(value))
    return output


def normalize_funding_exchange_response(records: Sequence[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: dict[tuple[str, str, str], dict[str, Any]] = {}
    invalid = []
    for index, parent in enumerate(records):
        try:
            if not isinstance(parent, Mapping):
                raise ValueError("invalid_funding_parent")
            if parent.get("symbol") != "BTC":
                continue
            pending = []
            for source, margin_type in (("stablecoin_margin_list", "stablecoin"), ("token_margin_list", "token")):
                children = parent.get(source, [])
                if not _sequence(children):
                    raise ValueError("invalid_funding_list")
                for child in children:
                    if not isinstance(child, Mapping):
                        raise ValueError("invalid_funding_record")
                    pending.append({"symbol": "BTC", "margin_type": margin_type, "exchange": _text(child.get("exchange"), "exchange"),
                        "funding_rate_interval_hours": normalize_positive_int(child.get("funding_rate_interval")),
                        "funding_rate_percent": normalize_finite_float(child.get("funding_rate")),
                        "next_funding_timestamp": normalize_timestamp_utc(child.get("next_funding_time"))})
            for row in pending:
                normalized[(row["symbol"], row["margin_type"], row["exchange"])] = row
        except ValueError as exc:
            invalid.append({"index": index, "reason": str(exc)})
    return list(normalized.values()), invalid


def normalize_options_info_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_options_record")
    mapping = {"open_interest": "open_interest_contracts", "oi_market_share": "market_share_percent",
        "open_interest_change_24h": "open_interest_change_percent_24h", "open_interest_usd": "open_interest_usd",
        "volume_usd_24h": "volume_usd_24h", "volume_change_percent_24h": "volume_change_percent_24h"}
    output = {"exchange": _text(record.get("exchange_name"), "exchange")}
    for source, target in mapping.items():
        value = record.get(source)
        output[target] = None if value is None else (normalize_non_negative_float(value) if target in {"open_interest_contracts", "market_share_percent", "open_interest_usd", "volume_usd_24h"} else normalize_finite_float(value))
    return output


def normalize_cryptoquant_record(record: Mapping[str, Any], *, metric_id: str, provider_window: str | None) -> dict[str, Any]:
    field, unit = ("open_interest", "USD") if metric_id == "cryptoquant_open_interest" else ("funding_rates", "percent")
    if not isinstance(record, Mapping):
        raise ValueError("invalid_cryptoquant_record")
    value = normalize_non_negative_float(record.get(field)) if field == "open_interest" else normalize_finite_float(record.get(field))
    return {"timestamp": normalize_timestamp_utc(record.get("date")), "value": value, "unit": unit, "provider_window": provider_window}


def normalize_glassnode_record(record: Mapping[str, Any], *, metric_id: str) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_glassnode_record")
    value = normalize_non_negative_float(record.get("v")) if metric_id in {"glassnode_futures_open_interest_sum", "glassnode_futures_estimated_leverage_ratio"} else normalize_finite_float(record.get("v"))
    return {"timestamp": normalize_timestamp_utc(record.get("t")), "value": value, "provider_interval": "1h"}


def upsert_records_by_timestamp(existing_records: Sequence[Mapping[str, Any]], incoming_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    for record in (*existing_records, *incoming_records):
        if not isinstance(record, Mapping) or type(record.get("timestamp")) is not int:
            raise ValueError("invalid_upsert_record")
        records[record["timestamp"]] = copy.deepcopy(dict(record))
    return [records[key] for key in sorted(records)]


def _upsert_composite(existing: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    records = {tuple(row[key] for key in keys): copy.deepcopy(dict(row)) for row in existing if isinstance(row, Mapping) and all(key in row for key in keys)}
    records.update({tuple(row[key] for key in keys): copy.deepcopy(dict(row)) for row in incoming})
    return [records[key] for key in sorted(records)]


def detect_internal_gaps(records: Sequence[Mapping[str, Any]], expected_interval_seconds: int) -> list[dict[str, int]]:
    gaps = []
    for previous, following in zip(records, records[1:]):
        difference = following["timestamp"] - previous["timestamp"]
        if difference > expected_interval_seconds:
            missing = max(0, difference // expected_interval_seconds - 1)
            gaps.append({"previous_timestamp": previous["timestamp"], "next_timestamp": following["timestamp"],
                "expected_interval_seconds": expected_interval_seconds, "missing_records": missing,
                "start_timestamp": previous["timestamp"] + expected_interval_seconds,
                "end_timestamp": following["timestamp"] - expected_interval_seconds})
    return gaps


def _existing_input(existing_state: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if existing_state is None:
        return None
    if not isinstance(existing_state, Mapping):
        raise ValueError("existing_state is incompatible")
    candidate = existing_state.get("input", existing_state)
    if not isinstance(candidate, Mapping) or candidate.get("family") != FAMILY or candidate.get("stage") != "input":
        raise ValueError("existing_state is incompatible")
    return candidate


def determine_open_interest_and_funding_input_mode(*, requested_mode: str | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                                    existing_state: Mapping[str, Any] | None = None) -> str:
    if requested_mode is not None:
        if requested_mode not in {"bootstrap", "incremental", "recovery"}:
            raise ValueError("unsupported mode")
        return requested_mode
    if recovery_requests:
        return "recovery"
    existing = _existing_input(existing_state)
    if existing is None:
        return "bootstrap"
    for metric in ("open_interest_ohlc", "funding_rate_ohlc"):
        for timeframe in SCREEN_TIMEFRAMES:
            if not existing.get("series", {}).get(metric, {}).get("timeframes", {}).get(timeframe, {}).get("records"):
                return "bootstrap"
    return "incremental"


def _normalize_rows(rows: Sequence[Any], normalizer: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid, invalid = [], []
    for index, row in enumerate(rows):
        try:
            valid.append(normalizer(row))
        except (TypeError, ValueError, KeyError) as exc:
            invalid.append({"index": index, "reason": str(exc)})
    deduplicated = {record["timestamp"]: record for record in valid}
    return [deduplicated[key] for key in sorted(deduplicated)], invalid


def _reject_future_records(records: Sequence[Mapping[str, Any]], invalid: list[dict[str, Any]], reference_timestamp: int) -> list[dict[str, Any]]:
    accepted = []
    for record in records:
        if record["timestamp"] > reference_timestamp:
            invalid.append({"index": len(invalid), "reason": "timestamp_after_reference_timestamp"})
        else:
            accepted.append(copy.deepcopy(dict(record)))
    return accepted


def _timeframe_payload(raw_payload: Mapping[str, Any] | None, existing: Mapping[str, Any] | None, metric_id: str, timeframe: str,
                       reference_timestamp: int) -> dict[str, Any]:
    existing_records = copy.deepcopy(existing.get("records", [])) if isinstance(existing, Mapping) else []
    endpoint = "aggregated_open_interest_ohlc" if metric_id == "open_interest_ohlc" else "oi_weighted_funding_rate_ohlc"
    unit = "USD" if metric_id == "open_interest_ohlc" else "percent_points"
    structural, reason, request_error, rows = False, None, False, []
    if not isinstance(raw_payload, Mapping):
        request_error, reason = True, "request_missing"
    elif raw_payload.get("status") == "error":
        request_error, reason = True, "request_failed"
    else:
        try:
            rows = unwrap_coinglass_response(raw_payload.get("response"))
        except ValueError as exc:
            structural, reason = True, str(exc)
    incoming, invalid = _normalize_rows(rows, lambda row: normalize_coinglass_ohlc_record(row, metric_id=metric_id))
    incoming = _reject_future_records(incoming, invalid, reference_timestamp)
    records = upsert_records_by_timestamp(existing_records, incoming)
    gaps = detect_internal_gaps(records, TIMEFRAME_SECONDS[timeframe])
    if structural:
        status = "invalid"
    elif invalid and not records:
        status, reason = "invalid", invalid[0]["reason"]
    elif records and (request_error or invalid or gaps):
        status = "partial"
    elif records:
        status, reason = "available", None
    else:
        status, reason = "unavailable", reason or "empty_data"
    return {"status": status, "provider": "coinglass", "endpoint_id": endpoint, "timeframe": timeframe, "unit": unit,
        "representation": "percentage_points" if metric_id == "funding_rate_ohlc" else None, "incoming_records": incoming, "records": records,
        "invalid_records": invalid, "warnings": ["timestamp_after_reference_timestamp"] if any(row["reason"] == "timestamp_after_reference_timestamp" for row in invalid) else [], "records_available": len(records), "incoming_valid_count": len(incoming),
        "incoming_invalid_count": len(invalid), "first_timestamp": records[0]["timestamp"] if records else None,
        "last_timestamp": records[-1]["timestamp"] if records else None, "expected_interval_seconds": TIMEFRAME_SECONDS[timeframe],
        "gaps": gaps, "stale": bool(request_error and records), "reason": reason if status != "available" else None}


def _resample_ohlc_payload(source: Mapping[str, Any], existing: Mapping[str, Any] | None, metric_id: str,
                           source_timeframe: str, target_timeframe: str, reference_timestamp: int) -> dict[str, Any]:
    """Build complete higher-timeframe OHLC locally from persisted lower-timeframe bars.

    Provider-native 15m history is retained from bootstrap. Incremental calls update
    the current hierarchy without paying for 5m/15m/1h/4h/1d separately.
    """
    source_records = [copy.deepcopy(dict(row)) for row in source.get("records", []) if isinstance(row, Mapping)]
    existing_records = copy.deepcopy(existing.get("records", [])) if isinstance(existing, Mapping) else []
    source_seconds, target_seconds = TIMEFRAME_SECONDS[source_timeframe], TIMEFRAME_SECONDS[target_timeframe]
    if target_seconds % source_seconds != 0 or target_seconds <= source_seconds:
        raise ValueError("invalid_resample_timeframe")
    factor = target_seconds // source_seconds
    last_existing = max((row.get("timestamp") for row in existing_records if isinstance(row, Mapping) and type(row.get("timestamp")) is int), default=None)
    phase = (last_existing % target_seconds) if last_existing is not None else (source_records[0]["timestamp"] % target_seconds if source_records else 0)
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in source_records:
        stamp = row.get("timestamp")
        if type(stamp) is not int:
            continue
        bucket = phase + ((stamp - phase) // target_seconds) * target_seconds
        buckets.setdefault(bucket, []).append(row)
    incoming: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        if last_existing is not None and bucket < last_existing:
            continue
        rows = sorted(buckets[bucket], key=lambda row: row["timestamp"])
        expected = [bucket + offset * source_seconds for offset in range(factor)]
        if len(rows) != factor or [row["timestamp"] for row in rows] != expected:
            continue
        incoming.append({
            "timestamp": bucket,
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
        })
    records = upsert_records_by_timestamp(existing_records, incoming)
    gaps = detect_internal_gaps(records, target_seconds)
    source_status = source.get("status")
    if records and not gaps and source_status in {"available", "partial"}:
        status, reason = ("available", None) if source_status == "available" else ("partial", "source_partial")
    elif records:
        status, reason = "partial", "partial_response"
    else:
        status, reason = "unavailable", "insufficient_complete_source_bucket"
    endpoint = "aggregated_open_interest_ohlc" if metric_id == "open_interest_ohlc" else "oi_weighted_funding_rate_ohlc"
    unit = "USD" if metric_id == "open_interest_ohlc" else "percent_points"
    return {
        "status": status, "provider": "coinglass", "endpoint_id": endpoint, "timeframe": target_timeframe, "unit": unit,
        "representation": "percentage_points" if metric_id == "funding_rate_ohlc" else None,
        "incoming_records": incoming, "records": records, "invalid_records": [], "warnings": [],
        "records_available": len(records), "incoming_valid_count": len(incoming), "incoming_invalid_count": 0,
        "first_timestamp": records[0]["timestamp"] if records else None, "last_timestamp": records[-1]["timestamp"] if records else None,
        "expected_interval_seconds": target_seconds, "gaps": gaps, "stale": bool(source.get("stale")),
        "reason": reason if status != "available" else None,
        "construction": {"method": "local_ohlc_resample", "source_timeframe": source_timeframe, "paid_request": False},
    }


def _snapshot_payload(raw_payload: Mapping[str, Any] | None, existing: Mapping[str, Any] | None, kind: str) -> dict[str, Any]:
    # A missing payload can be intentional during an incremental cycle: optional
    # secondary snapshots are refreshed on a slower cadence. Reuse the persisted
    # normalized snapshot instead of turning a planned cache hit into a failure.
    if raw_payload is None and isinstance(existing, Mapping):
        cached = copy.deepcopy(dict(existing))
        cached["stale"] = True
        warnings = list(cached.get("warnings", []))
        if "not_refreshed_this_cycle" not in warnings:
            warnings.append("not_refreshed_this_cycle")
        cached["warnings"] = warnings
        return cached
    existing_records = copy.deepcopy(existing.get("records", [])) if isinstance(existing, Mapping) else []
    failed, structural, reason, rows = False, False, None, []
    if not isinstance(raw_payload, Mapping) or raw_payload.get("status") == "error":
        failed, reason = True, "request_failed"
    else:
        try:
            rows = unwrap_coinglass_response(raw_payload.get("response"))
        except ValueError as exc:
            structural, reason = True, str(exc)
    invalid: list[dict[str, Any]] = []
    warnings = []
    if kind == "funding":
        incoming, invalid = normalize_funding_exchange_response(rows)
        keys = ("symbol", "margin_type", "exchange")
    else:
        normalizer = normalize_open_interest_exchange_record if kind == "open_interest" else normalize_options_info_record
        if kind == "open_interest":
            compatible = [row for row in rows if isinstance(row, Mapping) and isinstance(row.get("symbol"), str) and row["symbol"].strip().casefold() == "btc"]
            if len(compatible) != len(rows):
                warnings.append("non_btc_records_filtered")
            rows = compatible
        incoming, invalid = _normalize_snapshot_rows(rows, normalizer)
        keys = ("exchange", "symbol") if kind == "open_interest" else ("exchange",)
    records = _upsert_composite(existing_records, incoming, keys)
    if structural:
        status = "invalid"
    elif records and (failed or invalid):
        status = "partial"
    elif records:
        status, reason = "available", None
    else:
        status, reason = "unavailable", reason or "empty_data"
    aggregate = next((copy.deepcopy(row) for row in records if row.get("exchange") == "All"), None) if kind != "funding" else None
    return {"status": status, "records": records, "aggregate_record": aggregate, "invalid_records": invalid, "warnings": warnings,
        "stale": bool(failed and records), "reason": reason if status != "available" else None}


def _normalize_snapshot_rows(rows: Sequence[Any], normalizer: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid, invalid = [], []
    for index, row in enumerate(rows):
        try:
            valid.append(normalizer(row))
        except (TypeError, ValueError, KeyError) as exc:
            invalid.append({"index": index, "reason": str(exc)})
    return valid, invalid


def _confirmation_payload(raw_payload: Mapping[str, Any] | None, existing: Mapping[str, Any] | None, metric_id: str,
                          reference_timestamp: int) -> dict[str, Any]:
    # Confirmation providers are deliberately slower-cadence sources. If this
    # cycle did not request them, preserve the persisted series and mark it stale
    # rather than reporting a provider error that never happened.
    if raw_payload is None and isinstance(existing, Mapping):
        cached = copy.deepcopy(dict(existing))
        cached["incoming_records"] = []
        cached["stale"] = True
        warnings = list(cached.get("warnings", []))
        if "not_refreshed_this_cycle" not in warnings:
            warnings.append("not_refreshed_this_cycle")
        cached["warnings"] = warnings
        return cached
    existing_records = copy.deepcopy(existing.get("records", [])) if isinstance(existing, Mapping) else []
    metadata = CONFIRMATION_METADATA[metric_id]
    provider = metadata["provider"]
    structural, failed, reason, rows, window = False, False, None, [], None
    if not isinstance(raw_payload, Mapping) or raw_payload.get("status") == "error":
        failed, reason = True, "request_failed"
    else:
        try:
            if provider == "cryptoquant":
                window, rows = unwrap_cryptoquant_response(raw_payload.get("response"))
            else:
                rows = unwrap_glassnode_response(raw_payload.get("response"))
        except ValueError as exc:
            structural, reason = True, str(exc)
    normalizer = (lambda row: normalize_cryptoquant_record(row, metric_id=metric_id, provider_window=window)) if provider == "cryptoquant" else (lambda row: normalize_glassnode_record(row, metric_id=metric_id))
    incoming, invalid = _normalize_rows(rows, normalizer)
    incoming = _reject_future_records(incoming, invalid, reference_timestamp)
    records = upsert_records_by_timestamp(existing_records, incoming)
    status = "invalid" if structural or (invalid and not records) else ("partial" if records and (failed or invalid) else ("available" if records else "unavailable"))
    if invalid and not records:
        reason = invalid[0]["reason"]
    return {"status": status, "provider": provider, "endpoint_id": metadata["endpoint_id"], "unit": metadata["unit"],
        "records": records, "incoming_records": incoming, "invalid_records": invalid,
        "provider_window": "hour" if provider == "cryptoquant" else None, "provider_interval": "1h" if provider == "glassnode" else None,
        "warnings": ["timestamp_after_reference_timestamp"] if any(row["reason"] == "timestamp_after_reference_timestamp" for row in invalid) else [],
        "stale": bool(failed and records), "reason": None if status == "available" else reason or "empty_data"}


def preprocess_open_interest_and_funding_raw(raw_contract: Mapping[str, Any], *, existing_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw_contract, Mapping) or raw_contract.get("family") != FAMILY or raw_contract.get("stage") != "raw_input":
        raise ValueError("raw contract is incompatible")
    context, raw, existing = raw_contract.get("context"), raw_contract.get("raw"), _existing_input(existing_state)
    if not isinstance(context, Mapping) or not isinstance(raw, Mapping) or context.get("asset") != "BTC" or context.get("exchange_scope") != "all_exchanges":
        raise ValueError("raw context is incompatible")
    series = {}
    for metric, endpoint, unit in (("open_interest_ohlc", "aggregated_open_interest_ohlc", "USD"),
                                   ("funding_rate_ohlc", "oi_weighted_funding_rate_ohlc", "percent_points")):
        timeframes: dict[str, Any] = {}
        raw_frames = raw.get("series", {}).get(metric, {}).get("timeframes", {})
        old_frames = existing.get("series", {}).get(metric, {}).get("timeframes", {}) if existing else {}
        # 1m is the recurring paid source. Bootstrap also loads provider-native
        # 15m to seed deep history efficiently.
        timeframes["1m"] = _timeframe_payload(raw_frames.get("1m"), old_frames.get("1m"), metric, "1m", context["reference_timestamp"])
        if raw_frames.get("15m") is not None:
            timeframes["15m"] = _timeframe_payload(raw_frames.get("15m"), old_frames.get("15m"), metric, "15m", context["reference_timestamp"])
        else:
            timeframes["15m"] = _resample_ohlc_payload(timeframes["1m"], old_frames.get("15m"), metric, "1m", "15m", context["reference_timestamp"])
        # Prefer an explicit recovery response when present; otherwise build the
        # remaining display timeframes locally from the canonical base source.
        if raw_frames.get("5m") is not None:
            timeframes["5m"] = _timeframe_payload(raw_frames.get("5m"), old_frames.get("5m"), metric, "5m", context["reference_timestamp"])
        else:
            timeframes["5m"] = _resample_ohlc_payload(timeframes["1m"], old_frames.get("5m"), metric, "1m", "5m", context["reference_timestamp"])
        for timeframe in ("1h", "4h", "1d"):
            if raw_frames.get(timeframe) is not None:
                timeframes[timeframe] = _timeframe_payload(raw_frames.get(timeframe), old_frames.get(timeframe), metric, timeframe, context["reference_timestamp"])
            else:
                timeframes[timeframe] = _resample_ohlc_payload(timeframes["15m"], old_frames.get(timeframe), metric, "15m", timeframe, context["reference_timestamp"])
        # Preserve the public ordering expected by Processing/HMI consumers.
        timeframes = {timeframe: timeframes[timeframe] for timeframe in SCREEN_TIMEFRAMES}
        series[metric] = {"provider": "coinglass", "endpoint_id": endpoint, "unit": unit, "timeframes": timeframes}
        if metric == "funding_rate_ohlc":
            series[metric].update(representation="percentage_points", aggregation="open_interest_weighted")
    raw_snapshots, old_snapshots = raw.get("snapshots", {}), existing.get("snapshots", {}) if existing else {}
    snapshots = {"open_interest_by_exchange": _snapshot_payload(raw_snapshots.get("open_interest_exchange_list"), old_snapshots.get("open_interest_by_exchange"), "open_interest"),
        "funding_rate_by_exchange": _snapshot_payload(raw_snapshots.get("funding_rate_exchange_list"), old_snapshots.get("funding_rate_by_exchange"), "funding"),
        "options_open_interest": _snapshot_payload(raw_snapshots.get("options_info"), old_snapshots.get("options_open_interest"), "options")}
    raw_confirmations, old_confirmations = raw.get("confirmations", {}), existing.get("confirmations", {}) if existing else {}
    confirmations = {"open_interest": {
        "cryptoquant": _confirmation_payload(raw_confirmations.get("cryptoquant_open_interest"), old_confirmations.get("open_interest", {}).get("cryptoquant"), "cryptoquant_open_interest", context["reference_timestamp"]),
        "glassnode": _confirmation_payload(raw_confirmations.get("glassnode_futures_open_interest_sum"), old_confirmations.get("open_interest", {}).get("glassnode"), "glassnode_futures_open_interest_sum", context["reference_timestamp"])},
        "funding_rate": {
        "cryptoquant": _confirmation_payload(raw_confirmations.get("cryptoquant_funding_rates"), old_confirmations.get("funding_rate", {}).get("cryptoquant"), "cryptoquant_funding_rates", context["reference_timestamp"]),
        "glassnode": _confirmation_payload(raw_confirmations.get("glassnode_futures_funding_rate_perpetual"), old_confirmations.get("funding_rate", {}).get("glassnode"), "glassnode_futures_funding_rate_perpetual", context["reference_timestamp"])},
        "estimated_leverage_ratio": {
        "glassnode": _confirmation_payload(raw_confirmations.get("glassnode_futures_estimated_leverage_ratio"), old_confirmations.get("estimated_leverage_ratio", {}).get("glassnode"), "glassnode_futures_estimated_leverage_ratio", context["reference_timestamp"])}}
    required = {f"{metric}.{timeframe}": series[metric]["timeframes"][timeframe]["status"] for metric in series for timeframe in SCREEN_TIMEFRAMES}
    required.update({"open_interest_exchange_snapshot": snapshots["open_interest_by_exchange"]["status"], "funding_exchange_snapshot": snapshots["funding_rate_by_exchange"]["status"]})
    optional = {"options_open_interest_snapshot": snapshots["options_open_interest"]["status"]}
    optional.update({f"{metric}.{provider}": payload["status"] for metric, providers in confirmations.items() for provider, payload in providers.items()})
    recovery = [{"metric_id": metric, "timeframe": timeframe, "start_timestamp": gap["start_timestamp"], "end_timestamp": gap["end_timestamp"]}
        for metric in series for timeframe, payload in series[metric]["timeframes"].items() for gap in payload["gaps"]]
    if any(status == "invalid" for status in required.values()):
        quality_status = "invalid"
    elif any(status != "available" for status in required.values()) or any(status != "available" for status in optional.values()):
        quality_status = "partial"
    else:
        quality_status = "ok"
    generated_at = datetime.fromtimestamp(context["execution_timestamp"], timezone.utc).isoformat().replace("+00:00", "Z")
    availability = {"open_interest_primary": _aggregate_status(series["open_interest_ohlc"]["timeframes"]),
        "funding_rate_primary": _aggregate_status(series["funding_rate_ohlc"]["timeframes"]),
        "open_interest_exchange_snapshot": {"status": snapshots["open_interest_by_exchange"]["status"]},
        "funding_exchange_snapshot": {"status": snapshots["funding_rate_by_exchange"]["status"]},
        "options_open_interest_snapshot": {"status": snapshots["options_open_interest"]["status"]},
        "estimated_leverage_ratio": {"status": confirmations["estimated_leverage_ratio"]["glassnode"]["status"], "provider": "glassnode", "endpoint_id": "futures_estimated_leverage_ratio"},
        "open_interest_market_cap_ratio": {"status": "unavailable", "reason": "market_cap_source_not_configured"},
        "perpetual_vs_dated_futures_split": {"status": "unavailable", "reason": "dated_futures_open_interest_not_separated_by_current_sources"}}
    return {"family": FAMILY, "stage": "input", "mode": raw_contract["mode"], "context": {**copy.deepcopy(dict(context)), "generated_at": generated_at},
        "series": series, "snapshots": snapshots, "confirmations": confirmations, "availability": availability,
        "quality": {"status": quality_status, "required_endpoint_statuses": required, "optional_endpoint_statuses": optional,
            "recovery_required": bool(recovery), "recovery_requests": recovery, "warnings": [], "errors": []}}


def _aggregate_status(timeframes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [payload["status"] for payload in timeframes.values()]
    status = "invalid" if "invalid" in statuses else ("unavailable" if all(item == "unavailable" for item in statuses) else ("available" if all(item == "available" for item in statuses) else "partial"))
    return {"status": status}


class OpenInterestAndFundingInputPreprocessor:
    def __init__(self, raw_extractor: OpenInterestAndFundingRawExtractor, existing_state: Mapping[str, Any] | None = None) -> None:
        self.raw_extractor = raw_extractor
        self.existing_state = copy.deepcopy(existing_state)

    def determine_mode(self, *, requested_mode: str | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None) -> str:
        return determine_open_interest_and_funding_input_mode(requested_mode=requested_mode, recovery_requests=recovery_requests, existing_state=self.existing_state)

    def preprocess_raw(self, raw_contract: Mapping[str, Any]) -> dict[str, Any]:
        return preprocess_open_interest_and_funding_raw(raw_contract, existing_state=self.existing_state)

    def run(self, *, reference_timestamp: int, requested_mode: str | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
            include_snapshots: bool = True, include_confirmations: bool = True, data_mode: str = "live", is_demo: bool = False,
            execution_timestamp: int | None = None, refresh_secondary: bool = False) -> dict[str, Any]:
        mode = self.determine_mode(requested_mode=requested_mode, recovery_requests=recovery_requests)
        raw = self.raw_extractor.extract(mode=mode, reference_timestamp=reference_timestamp, existing_state=self.existing_state,
            recovery_requests=recovery_requests, include_snapshots=include_snapshots, include_confirmations=include_confirmations,
            data_mode=data_mode, is_demo=is_demo, execution_timestamp=execution_timestamp, refresh_secondary=refresh_secondary)
        return self.preprocess_raw(raw)


def run_open_interest_and_funding_input(*, fetcher: OpenInterestAndFundingFetcher, reference_timestamp: int,
                                        requested_mode: str | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                        existing_state: Mapping[str, Any] | None = None, include_snapshots: bool = True,
                                        include_confirmations: bool = True, data_mode: str = "live", is_demo: bool = False,
                                        execution_timestamp: int | None = None, refresh_secondary: bool = False) -> dict[str, Any]:
    return OpenInterestAndFundingInputPreprocessor(OpenInterestAndFundingRawExtractor(fetcher), existing_state).run(
        reference_timestamp=reference_timestamp, requested_mode=requested_mode, recovery_requests=recovery_requests,
        include_snapshots=include_snapshots, include_confirmations=include_confirmations, data_mode=data_mode,
        is_demo=is_demo, execution_timestamp=execution_timestamp, refresh_secondary=refresh_secondary)
