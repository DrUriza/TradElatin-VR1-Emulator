from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing          import Any, Callable


SECONDS_PER_DAY             = 86_400
HASHES_PER_EXAHASH          = 1_000_000_000_000_000_000
DIFFICULTY_PER_TRILLION     = 1_000_000_000_000
SOPR_SMA_PERIOD_DAYS        = 7
RESERVE_TREND_WINDOWS_DAYS  = (7, 30, 90)
DEFAULT_RESERVE_TREND_DAYS  = 30
DAILY_WINDOW_TOLERANCE_DAYS = 2
STATUS_PRIORITY             = {"available": 0, "partial": 1, "unavailable": 2, "invalid": 3}
UTXO_AGE_BANDS              = ("0d_1d", "1d_1w", "1w_1m", "1m_3m", "3m_6m", "6m_12m", "12m_18m", "18m_2y", "2y_3y", "3y_5y", "5y_7y", "7y_10y", "10y_inf")
REVENUE_ABSOLUTE_TOLERANCE_USD  = 1e-6
REVENUE_RELATIVE_TOLERANCE      = 1e-12
FEE_SHARE_CONSISTENCY_TOLERANCE = 0.02


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("value_must_be_finite_number")
    number = float(value)
    return 0.0 if number == 0.0 else number


def _metadata(source_count: int, records: Sequence[Mapping[str, Any]], unavailable: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"records_source": source_count, "records_calculated": len(records), "records_unavailable": len(unavailable),
            "first_valid_timestamp": records[0]["timestamp"] if records else None, "last_valid_timestamp": records[-1]["timestamp"] if records else None,
            "calculation_history": "full_available_history", "history_truncated": False}


def _stable_unique(messages: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _source_timestamp_errors(source: Mapping[str, Any], metric_id: str) -> list[str]:
    errors: list[str] = []
    previous: int | None = None
    seen: set[int] = set()
    records = source.get("records", [])
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return [f"source_records_must_be_sequence:{metric_id}"]
    for record in records:
        if not isinstance(record, Mapping):
            return [f"source_record_must_be_mapping:{metric_id}"]
        timestamp = record.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            return [f"source_timestamp_invalid:{metric_id}"]
        if timestamp in seen:
            errors.append(f"source_duplicate_timestamp:{metric_id}:{timestamp}")
            break
        if previous is not None and timestamp < previous:
            errors.append(f"source_timestamps_not_strictly_ascending:{metric_id}")
            break
        seen.add(timestamp)
        previous = timestamp
    return errors


def build_current_snapshot(series_payload: Mapping[str, Any]) -> dict[str, Any]:
    records = series_payload.get("records", [])
    if isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)):
        for record in reversed(records):
            if isinstance(record, Mapping) and isinstance(record.get("timestamp"), int):
                try:
                    value = _finite(record.get("value"))
                except ValueError:
                    continue
                return {"status": "available", "timestamp": int(record["timestamp"]), "value": value, "unit": record.get("unit", series_payload.get("unit"))}
    return {"status": "unavailable", "value": None, "reason": "no_valid_calculated_records"}


def _series_payload(*, metric_id: str, unit: str, source_count: int, records: list[dict[str, Any]], unavailable: list[dict[str, Any]],
                    warnings: list[str] | None = None, errors: list[str] | None = None, source_status: str = "available",
                    force_invalid: bool = False, partial_reasons: bool = False) -> dict[str, Any]:
    warnings = list(warnings or [])
    errors   = list(errors or [])
    if force_invalid:
        status = "invalid"
    elif not records:
        status = "unavailable"
    elif source_status == "partial" or partial_reasons:
        status = "partial"
    else:
        status = "available"
    payload = {"metric_id": metric_id, "status": status, "unit": unit, "records": records, "unavailable_records": unavailable,
               "warnings": warnings, "errors": errors, "metadata": _metadata(source_count, records, unavailable)}
    payload["current"] = build_current_snapshot(payload)
    return payload


def _blocked_source_series(*, metric_id: str, unit: str, source_metric_id: str, source_status: str) -> dict[str, Any]:
    invalid = source_status == "invalid"
    reason  = f"source_series_{source_status}:{source_metric_id}"
    payload = _series_payload(metric_id=metric_id, unit=unit, source_count=0, records=[], unavailable=[],
                              warnings=[] if invalid else [reason], errors=[reason] if invalid else [], force_invalid=invalid)
    payload["status"]  = source_status
    payload["current"] = {"status": "unavailable", "value": None, "reason": f"source_series_{source_status}"}
    return payload


def apply_source_series_context(payload: Mapping[str, Any], source: Mapping[str, Any], source_metric_id: str) -> dict[str, Any]:
    """Combine mathematical and Input status using invalid > unavailable > partial > available."""
    output        = dict(payload)
    source_status = str(source["status"])
    result_status = str(output["status"])
    output["status"] = max((source_status, result_status), key=lambda status: STATUS_PRIORITY[status])
    source_marker = f"source_series_{source_status}:{source_metric_id}" if source_status != "available" else None
    warnings = list(output.get("warnings", []))
    errors   = list(output.get("errors", []))
    if source_marker:
        (errors if source_status == "invalid" else warnings).append(source_marker)
    warnings.extend(f"input_series_warning:{source_metric_id}:{message}" for message in source.get("warnings", []))
    errors.extend(f"input_series_error:{source_metric_id}:{message}" for message in source.get("errors", []))
    if source.get("gaps"):
        warnings.append(f"input_series_gaps:{source_metric_id}:{len(source['gaps'])}")
    output["warnings"] = _stable_unique(warnings)
    output["errors"]   = _stable_unique(errors)
    return output


def _copy_base_series(source: Mapping[str, Any], *, metric_id: str, unit: str, transform: Callable[[Mapping[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    if source["status"] in {"invalid", "unavailable"}:
        return apply_source_series_context(_blocked_source_series(metric_id=metric_id, unit=unit, source_metric_id=str(source["metric_id"]),
                                                                   source_status=str(source["status"])), source, str(source["metric_id"]))
    source_records = source.get("records", [])
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        for record in source_records:
            records.append(transform(record))
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    payload = _series_payload(metric_id=metric_id, unit=unit, source_count=len(source_records), records=records, unavailable=[], errors=errors,
                              force_invalid=bool(errors))
    return apply_source_series_context(payload, source, str(source["metric_id"]))




def _daily_close_source(source: Mapping[str, Any], *, metric_id: str) -> dict[str, Any]:
    """Collapse intraday provider observations to one canonical UTC close per day."""
    if source.get("status") in {"invalid", "unavailable"}:
        return dict(source)
    records = source.get("records", [])
    by_day: dict[int, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("timestamp"), int):
            continue
        day = int(record["timestamp"]) - int(record["timestamp"]) % SECONDS_PER_DAY
        previous = by_day.get(day)
        if previous is None or int(record["timestamp"]) >= int(previous["timestamp"]):
            by_day[day] = record
    daily = []
    for day in sorted(by_day):
        record = dict(by_day[day])
        record["timestamp"] = day
        record["source_timestamp"] = int(by_day[day]["timestamp"])
        record["source_resolution"] = "1h" if len(records) > len(by_day) else record.get("source_window", "24h")
        daily.append(record)
    output = dict(source)
    output["records"] = daily
    output["metadata"] = {**dict(source.get("metadata", {})), "daily_close_normalization": True,
                          "source_records_before_daily_close": len(records), "daily_records": len(daily)}
    return output


def build_daily_ohlc_from_intraday(records: Sequence[Mapping[str, Any]], *, unit: str,
                                   value_transform: Callable[[float], float] | None = None) -> list[dict[str, Any]]:
    """Construct legitimate UTC daily OHLC from multiple real provider observations."""
    buckets: dict[int, list[tuple[int, float]]] = {}
    for record in records:
        try:
            timestamp = int(record["timestamp"])
            value = _finite(record["value"])
            if value_transform is not None:
                value = _finite(value_transform(value))
        except (KeyError, TypeError, ValueError):
            continue
        day = timestamp - timestamp % SECONDS_PER_DAY
        buckets.setdefault(day, []).append((timestamp, value))
    candles: list[dict[str, Any]] = []
    for day in sorted(buckets):
        observations = sorted(buckets[day])
        values = [value for _, value in observations]
        candles.append({"timestamp": day, "open": values[0], "high": max(values), "low": min(values), "close": values[-1],
                        "is_closed": True, "observation_count": len(observations), "unit": unit,
                        "ohlc_origin": "processing_derived", "source_resolution": "1h"})
    return candles


def build_sopr_7d_intraday_candles(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build 7-day rolling SOPR at provider resolution, then aggregate those values to daily OHLC."""
    ordered = sorted((int(r["timestamp"]), _finite(r.get("sopr", r.get("value")))) for r in records
                     if isinstance(r, Mapping) and isinstance(r.get("timestamp"), int))
    if not ordered:
        return []
    window_seconds = 7 * SECONDS_PER_DAY
    smoothed: list[dict[str, Any]] = []
    left = 0
    running: list[tuple[int, float]] = []
    for timestamp, value in ordered:
        running.append((timestamp, value))
        while left < len(running) and running[left][0] < timestamp - window_seconds + 3600:
            left += 1
        window = running[left:]
        if window and timestamp - window[0][0] >= window_seconds - 3600:
            smoothed.append({"timestamp": timestamp, "value": sum(v for _, v in window) / len(window)})
    return build_daily_ohlc_from_intraday(smoothed, unit="ratio")


def _copy_direct_value_series(source: Mapping[str, Any], *, metric_id: str, unit: str, source_metric_id: str) -> dict[str, Any]:
    if source.get("status") in {"invalid", "unavailable"}:
        return apply_source_series_context(_blocked_source_series(metric_id=metric_id, unit=unit, source_metric_id=source_metric_id,
                                                                   source_status=str(source.get("status"))), source, source_metric_id)
    records = []
    errors = []
    for index, record in enumerate(source.get("records", [])):
        try:
            records.append({"timestamp": int(record["timestamp"]), "value": _finite(record["value"]), "unit": unit,
                            "provider": str(record.get("provider", "glassnode")), "source_metric_id": source_metric_id,
                            "endpoint_id": record.get("endpoint_id")})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"record[{index}]: {exc}")
    payload = _series_payload(metric_id=metric_id, unit=unit, source_count=len(source.get("records", [])), records=records,
                              unavailable=[], errors=errors, force_invalid=bool(errors), source_status=str(source.get("status", "available")))
    return apply_source_series_context(payload, source, source_metric_id)


def _reserve_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"timestamp": int(record["timestamp"]), "value": _finite(record["value"]), "unit": "BTC", "source_metric_id": "miner_reserve",
            "provider": str(record.get("provider", "glassnode"))}


def _sopr_record(record: Mapping[str, Any]) -> dict[str, Any]:
    value = _finite(record.get("sopr", record.get("value")))
    return {"timestamp": int(record["timestamp"]), "value": value, "sopr": value,
            **{field: None if record.get(field) is None else _finite(record[field]) for field in ("a_sopr", "sth_sopr", "lth_sopr")},
            "unit": "ratio", "source_metric_id": "sopr", "provider": str(record.get("provider", "glassnode"))}


def _mpi_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"timestamp": int(record["timestamp"]), "value": _finite(record["value"]), "unit": "z_score", "source_metric_id": "mpi",
            "provider": str(record.get("provider", "cryptoquant"))}


def build_sopr_7d_series(sopr_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(sopr_records):
        try:
            timestamp = int(record["timestamp"])
            if index < SOPR_SMA_PERIOD_DAYS - 1:
                unavailable.append({"timestamp": timestamp, "status": "unavailable", "reason": "insufficient_history"})
                continue
            window = sopr_records[index - SOPR_SMA_PERIOD_DAYS + 1:index + 1]
            contiguous = all(int(window[position]["timestamp"]) - int(window[position - 1]["timestamp"]) == SECONDS_PER_DAY
                             for position in range(1, len(window)))
            if not contiguous:
                unavailable.append({"timestamp": timestamp, "status": "unavailable", "reason": "non_contiguous_window"})
                continue
            values = [_finite(item.get("sopr", item.get("value"))) for item in window]
            value  = _finite(sum(values) / SOPR_SMA_PERIOD_DAYS)
            records.append({"timestamp": timestamp, "value": 0.0 if value == 0.0 else value, "unit": "ratio", "period_days": SOPR_SMA_PERIOD_DAYS,
                            "observations": SOPR_SMA_PERIOD_DAYS, "window_start_timestamp": int(window[0]["timestamp"]), "window_end_timestamp": timestamp,
                            "source_metric_id": "sopr", "calculation": "simple_moving_average"})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"record[{index}]: {exc}")
    has_gap = any(item["reason"] == "non_contiguous_window" for item in unavailable)
    return _series_payload(metric_id="sopr_7d", unit="ratio", source_count=len(sopr_records), records=records, unavailable=unavailable,
                           warnings=(["sopr_7d_non_contiguous_history"] if has_gap else []) + (["sopr_7d_insufficient_history"] if not records else []),
                           errors=errors, force_invalid=bool(errors), partial_reasons=has_gap)


def _build_conversion_series(source_records: Sequence[Mapping[str, Any]], *, metric_id: str, unit: str, source_unit: str, conversion: str,
                             scale: int, source_metric_id: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(source_records):
        try:
            source_value = _finite(record["value"])
            if source_value < 0:
                raise ValueError("negative_source_value")
            records.append({"timestamp": int(record["timestamp"]), "value": source_value / scale, "unit": unit, "source_value": source_value,
                            "source_unit": source_unit, "conversion": conversion, "conversion_scale": float(scale), "source_metric_id": source_metric_id})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"record[{index}]: {exc}")
    return _series_payload(metric_id=metric_id, unit=unit, source_count=len(source_records), records=records, unavailable=[], errors=errors, force_invalid=bool(errors))


def build_hashrate_eh_s_series(hashrate_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _build_conversion_series(hashrate_records, metric_id="hashrate_eh_s", unit="EH/s", source_unit="H/s", conversion="H/s_to_EH/s",
                                    scale=HASHES_PER_EXAHASH, source_metric_id="hashrate")


def build_difficulty_trillion_series(difficulty_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _build_conversion_series(difficulty_records, metric_id="difficulty_t", unit="T", source_unit="provider_native_difficulty",
                                    conversion="native_difficulty_to_trillion", scale=DIFFICULTY_PER_TRILLION, source_metric_id="difficulty")


def build_miner_net_position_change_series(reserve_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(reserve_records):
        try:
            timestamp = int(record["timestamp"])
            current   = _finite(record["value"])
            if index == 0:
                unavailable.append({"timestamp": timestamp, "status": "unavailable", "reason": "insufficient_previous_record"})
                continue
            previous = reserve_records[index - 1]
            if timestamp - int(previous["timestamp"]) != SECONDS_PER_DAY:
                unavailable.append({"timestamp": timestamp, "status": "unavailable", "reason": "previous_day_missing"})
                continue
            previous_value = _finite(previous["value"])
            value = _finite(current - previous_value)
            records.append({"timestamp": timestamp, "value": 0.0 if value == 0.0 else value, "unit": "BTC/day", "current_reserve_btc": current,
                            "previous_reserve_btc": previous_value, "previous_timestamp": int(previous["timestamp"]), "period_seconds": SECONDS_PER_DAY,
                            "source_metric_id": "miner_reserve", "calculation": "daily_first_difference"})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"record[{index}]: {exc}")
    has_gap = any(item["reason"] == "previous_day_missing" for item in unavailable)
    return _series_payload(metric_id="miner_net_position_change", unit="BTC/day", source_count=len(reserve_records), records=records,
                           unavailable=unavailable, warnings=["non_contiguous_daily_history"] if has_gap else [], errors=errors,
                           force_invalid=bool(errors), partial_reasons=has_gap)


def _linear_regression(records: Sequence[Mapping[str, Any]]) -> dict[str, float | int | None]:
    first_timestamp = int(records[0]["timestamp"])
    x = [(int(record["timestamp"]) - first_timestamp) / SECONDS_PER_DAY for record in records]
    y = [_finite(record["value"]) for record in records]
    mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((value - mean_x) ** 2 for value in x)
    if denominator == 0:
        raise ValueError("regression_x_variance_zero")
    slope     = sum((x_value - mean_x) * (y_value - mean_y) for x_value, y_value in zip(x, y)) / denominator
    intercept = mean_y - slope * mean_x
    total     = sum((value - mean_y) ** 2 for value in y)
    residual  = sum((y_value - (intercept + slope * x_value)) ** 2 for x_value, y_value in zip(x, y))
    r_squared = 1.0 if total == 0 else 1.0 - residual / total
    first_value, last_value = y[0], y[-1]
    result = {"slope_btc_per_day": 0.0 if slope == 0.0 else slope, "normalized_slope_percent_per_day": None if mean_y == 0 else 100 * slope / mean_y,
            "intercept_btc": intercept, "r_squared": r_squared, "mean_reserve_btc": mean_y, "net_change_btc": last_value - first_value,
            "percent_change": None if first_value == 0 else 100 * (last_value - first_value) / abs(first_value), "first_timestamp": first_timestamp,
            "last_timestamp": int(records[-1]["timestamp"]), "first_value_btc": first_value, "last_value_btc": last_value}
    for key, value in result.items():
        if isinstance(value, float):
            result[key] = _finite(value)
    return result


def build_reserve_trend_features(reserve_records: Sequence[Mapping[str, Any]], *, window_days: Sequence[int] = RESERVE_TREND_WINDOWS_DAYS,
                                 default_window_days: int = DEFAULT_RESERVE_TREND_DAYS) -> dict[str, Any]:
    windows: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    current_timestamp = int(reserve_records[-1]["timestamp"]) if reserve_records else None
    for days in window_days:
        if not isinstance(days, int) or days <= 0:
            errors.append(f"invalid_window_days:{days}")
            continue
        theoretical_start = current_timestamp - (days - 1) * SECONDS_PER_DAY if current_timestamp is not None else None
        selected = [record for record in reserve_records if theoretical_start is not None and theoretical_start <= int(record["timestamp"]) <= current_timestamp]
        observations = len(selected)
        first = int(selected[0]["timestamp"]) if selected else None
        last  = int(selected[-1]["timestamp"]) if selected else None
        leading_missing  = max(0, (first - theoretical_start) // SECONDS_PER_DAY) if first is not None and theoretical_start is not None else days
        trailing_missing = max(0, (current_timestamp - last) // SECONDS_PER_DAY) if last is not None and current_timestamp is not None else 0
        span_days        = ((last - first) // SECONDS_PER_DAY) + 1 if first is not None and last is not None else 0
        internal_missing = max(0, span_days - observations)
        total_missing    = leading_missing + trailing_missing + internal_missing
        window_warnings: list[str] = []
        window_errors: list[str] = []
        window = {"window_days": days, "observations": observations, "expected_observations": days, "coverage_ratio": min(observations / days, 1.0),
                  "history_complete": False, "theoretical_start_timestamp": theoretical_start, "theoretical_end_timestamp": current_timestamp,
                  "first_timestamp": first, "last_timestamp": last, "leading_missing_days": leading_missing, "trailing_missing_days": trailing_missing,
                  "internal_missing_days": internal_missing, "total_missing_days": total_missing, "span_calendar_days": span_days,
                  "warnings": window_warnings, "errors": window_errors}
        if observations >= 3:
            try:
                window.update(_linear_regression(selected))
                window["history_complete"] = total_missing <= DAILY_WINDOW_TOLERANCE_DAYS
                if window["history_complete"]:
                    window["status"] = "available"
                else:
                    window["status"] = "partial"
                    window_warnings.append("reserve_trend_window_incomplete")
            except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
                reason = "regression_x_variance_zero" if str(exc) == "regression_x_variance_zero" else "non_finite_regression_result"
                window.update({"status": "invalid", "slope_btc_per_day": None, "normalized_slope_percent_per_day": None, "intercept_btc": None,
                               "r_squared": None, "mean_reserve_btc": None, "net_change_btc": None, "percent_change": None})
                window_errors.append(reason)
        else:
            window.update({"status": "unavailable", "slope_btc_per_day": None, "normalized_slope_percent_per_day": None, "intercept_btc": None,
                           "r_squared": None, "mean_reserve_btc": None, "net_change_btc": None, "percent_change": None,
                           "first_timestamp": first, "last_timestamp": last})
            window_warnings.append("insufficient_observations_for_regression")
        windows[f"{days}d"] = window
    default = windows.get(f"{default_window_days}d", {"status": "invalid"})
    status  = str(default["status"])
    warnings.extend(default.get("warnings", []))
    errors.extend(default.get("errors", []))
    return {"feature_id": "reserve_trend", "status": status, "default_window_days": default_window_days, "windows": windows,
            "warnings": _stable_unique(warnings), "errors": _stable_unique(errors)}


def _mpi_basis(mpi_series: Mapping[str, Any]) -> dict[str, Any]:
    records  = mpi_series.get("records", [])
    current  = build_current_snapshot(mpi_series)
    previous = None
    change   = None
    if len(records) >= 2 and int(records[-1]["timestamp"]) - int(records[-2]["timestamp"]) == SECONDS_PER_DAY:
        previous = {"status": "available", "timestamp": int(records[-2]["timestamp"]), "value": _finite(records[-2]["value"]), "unit": "z_score"}
        change   = _finite(_finite(records[-1]["value"]) - _finite(records[-2]["value"]))
    return {"source_metric_id": "mpi", "current": current, "previous": previous, "change_1d": change, "unit": "z_score"}


def _status_from_source(source_status: str, *, has_records: bool, partial: bool = False, invalid: bool = False) -> str:
    if invalid or source_status == "invalid":
        return "invalid"
    if source_status == "unavailable" or not has_records:
        return "unavailable"
    return "partial" if partial or source_status == "partial" else "available"


def _simple_extension_series(source: Mapping[str, Any], *, metric_id: str, unit: str, provider: str, endpoint_id: str,
                             extra: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[str] = _source_timestamp_errors(source, str(source.get("metric_id", metric_id)))
    if not errors and source.get("status") not in {"invalid", "unavailable"}:
        for index, record in enumerate(source.get("records", [])):
            try:
                timestamp = record["timestamp"]
                if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
                    raise ValueError("timestamp_must_be_non_negative_integer")
                if record.get("unit") != source.get("unit") or record.get("provider") != provider or record.get("endpoint_id") != endpoint_id:
                    raise ValueError("incompatible_extension_record_contract")
                value = _finite(record["value"])
                records.append({"timestamp": timestamp, "value": value, "unit": unit, "provider": provider, "endpoint_id": endpoint_id,
                                **(extra(record) if extra else {})})
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"record[{index}]:{exc}")
    status = _status_from_source(str(source.get("status", "invalid")), has_records=bool(records), invalid=bool(errors))
    warnings = [f"input_series_warning:{source.get('metric_id')}:{message}" for message in source.get("warnings", [])]
    propagated_errors = [f"input_series_error:{source.get('metric_id')}:{message}" for message in source.get("errors", [])]
    if source.get("status") == "partial":
        warnings.append(f"source_series_partial:{source.get('metric_id')}")
    payload = _series_payload(metric_id=metric_id, unit=unit, source_count=len(source.get("records", [])), records=records, unavailable=[],
                              warnings=_stable_unique(warnings), errors=_stable_unique([*errors, *propagated_errors]), force_invalid=status == "invalid",
                              partial_reasons=status == "partial")
    payload["status"] = status
    return payload


def build_miners_unspent_supply_series(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = _simple_extension_series(source, metric_id="miners_unspent_supply_btc", unit="BTC", provider="glassnode",
                                       endpoint_id="miners_unspent_supply", extra=lambda _: {"scope": "miner_specific"})
    payload["metadata"].update({"scope": "miner_specific", "meaning": "coinbase_outputs_never_moved"})
    source_current = source.get("current")
    if isinstance(source_current, Mapping) and source_current.get("status") == "available":
        try:
            timestamp, value, unit = source_current["timestamp"], _finite(source_current["value"]), source_current.get("unit")
            exact = next((record for record in payload["records"] if record["timestamp"] == timestamp and record["value"] == value
                          and record["unit"] == unit), None)
            if not exact:
                raise ValueError("input_current_not_in_valid_records")
            payload["current"] = {"status": "available", "timestamp": timestamp, "value": value, "unit": "BTC"}
        except (KeyError, TypeError, ValueError):
            payload["status"] = "invalid"
            payload["current"] = {"status": "unavailable", "value": None, "reason": "input_current_not_in_valid_records"}
            payload["errors"] = _stable_unique([*payload["errors"], "input_current_not_in_valid_records"])
    elif source_current is None:
        # The normalized Input may omit the duplicated current envelope. Keep the
        # deterministic latest snapshot already derived from validated records.
        if not payload["records"]:
            payload["current"] = {"status": "unavailable", "value": None, "reason": "no_valid_records"}
    else:
        payload["current"] = {"status": "unavailable", "value": None, "reason": "input_current_unavailable"}
        if payload["status"] == "available":
            payload["status"] = "partial" if payload["records"] else "unavailable"
        payload["warnings"] = _stable_unique([*payload["warnings"], "input_current_unavailable:miners_unspent_supply"])
    return payload


def build_nupl_series(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = _simple_extension_series(source, metric_id="nupl", unit="ratio", provider="coinglass", endpoint_id="bitcoin_nupl",
                                       extra=lambda record: {"price_usd": None if record.get("price_usd") is None else _finite(record["price_usd"])})
    source_current = source.get("current")
    if isinstance(source_current, Mapping) and source_current.get("status") == "available":
        try:
            timestamp = source_current["timestamp"]
            value = _finite(source_current["value"])
            price = None if source_current.get("price_usd") is None else _finite(source_current["price_usd"])
            exact = next((record for record in payload["records"] if record["timestamp"] == timestamp and record["value"] == value
                          and record.get("price_usd") == price and record.get("unit") == source_current.get("unit")), None)
            payload["current"] = ({"status": "available", "timestamp": timestamp, "value": value, "price_usd": price, "unit": "ratio"}
                                  if exact else {"status": "unavailable", "value": None, "reason": "input_current_not_in_valid_records"})
            if not exact:
                payload["status"] = "invalid"
                same_value = any(record["timestamp"] == timestamp and record["value"] == value for record in payload["records"])
                payload["errors"] = _stable_unique([*payload["errors"], "nupl_current_price_mismatch" if same_value else "input_current_not_in_valid_records"])
        except (KeyError, TypeError, ValueError):
            payload["status"] = "invalid"
            payload["current"] = {"status": "unavailable", "value": None, "reason": "invalid_input_current"}
            payload["errors"] = _stable_unique([*payload["errors"], "invalid_nupl_input_current"])
    elif source_current is None:
        if not payload["records"]:
            payload["current"] = {"status": "unavailable", "value": None, "reason": "no_valid_records"}
    else:
        payload["current"] = {"status": "unavailable", "value": None, "reason": "input_current_unavailable"}
        if payload["status"] == "available":
            payload["status"] = "unavailable"
    return payload


def build_nupl_phase_basis(nupl_series: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(nupl_series.get("current", {"status": "unavailable", "value": None}))
    previous: dict[str, Any] = {"status": "unavailable", "timestamp": None, "value": None, "price_usd": None, "unit": "ratio",
                                "reason": "previous_calendar_day_unavailable"}
    change = None
    warnings = list(nupl_series.get("warnings", []))
    errors = list(nupl_series.get("errors", []))
    if current.get("status") == "available":
        target = int(current["timestamp"]) - SECONDS_PER_DAY
        match = next((record for record in nupl_series.get("records", []) if record.get("timestamp") == target), None)
        if match:
            previous = {"status": "available", "timestamp": target, "value": _finite(match["value"]), "price_usd": match.get("price_usd"), "unit": "ratio", "reason": None}
            change = _finite(_finite(current["value"]) - _finite(match["value"]))
        else:
            warnings.append("previous_calendar_day_unavailable")
    status = str(nupl_series.get("status", "invalid"))
    if status == "available" and current.get("status") != "available":
        status = "unavailable"
    return {"feature_id": "nupl_phase_basis", "status": status, "current": current, "previous": previous, "change_1d": change,
            "warnings": _stable_unique(warnings), "errors": _stable_unique(errors),
            "metadata": {"previous_policy": "exact_previous_calendar_day", "classification_pending": True,
                         "data_as_of": current.get("timestamp") if current.get("status") == "available" else None}}


def build_miner_outflow_distribution(collection: Mapping[str, Any]) -> dict[str, Any]:
    source_status = str(collection.get("status", "invalid"))
    warnings = [f"input_collection_warning:miner_outflow_by_pool:{message}" for message in collection.get("warnings", [])]
    errors = [f"input_collection_error:miner_outflow_by_pool:{message}" for message in collection.get("errors", [])]
    pools = collection.get("pools", {})
    if source_status == "invalid" or not isinstance(pools, Mapping):
        return {"feature_id": "miner_outflow_distribution", "status": "invalid", "unit": "BTC/day", "records": [],
                "current": {"status": "unavailable", "value": None, "reason": "source_collection_invalid"}, "active_symbols": [],
                "inactive_symbols": [], "warnings": _stable_unique(warnings), "errors": _stable_unique([*errors, "source_collection_invalid"]),
                "metadata": {"data_as_of": None, "timestamps_processed": 0, "calculation": "cross_pool_exact_timestamp_distribution"}}
    if any(not isinstance(symbol, str) for symbol in pools):
        return {"feature_id": "miner_outflow_distribution", "status": "invalid", "unit": "BTC/day", "records": [],
                "current": {"status": "unavailable", "value": None, "reason": "source_collection_invalid"}, "active_symbols": [],
                "inactive_symbols": [], "warnings": _stable_unique(warnings), "errors": _stable_unique([*errors, "non_string_pool_symbol"]),
                "metadata": {"data_as_of": None, "timestamps_processed": 0, "calculation": "cross_pool_exact_timestamp_distribution"}}
    active_symbols = sorted(symbol for symbol, payload in pools.items() if isinstance(payload, Mapping) and payload.get("active") is True)
    inactive_symbols = sorted(symbol for symbol in pools if symbol not in active_symbols)
    by_timestamp: dict[int, list[dict[str, Any]]] = {}
    invalid = False
    for symbol in sorted(pools):
        payload = pools[symbol]
        required_pool_keys = {"miner_symbol", "active", "status", "records", "warnings", "errors", "metadata"}
        if not isinstance(payload, Mapping) or not required_pool_keys <= set(payload) or payload.get("miner_symbol") != symbol \
                or not isinstance(payload.get("active"), bool) or payload.get("status") not in STATUS_PRIORITY:
            invalid = True
            errors.append(f"invalid_pool_structure:{symbol}")
            continue
        pool_status = str(payload["status"])
        if pool_status == "invalid":
            invalid = True
            errors.append(f"source_pool_invalid:{symbol}")
            continue
        if pool_status == "unavailable":
            warnings.append(f"source_pool_unavailable:{symbol}")
            continue
        if pool_status == "partial":
            warnings.append(f"source_pool_partial:{symbol}")
        previous_timestamp = None
        for index, record in enumerate(payload.get("records", [])):
            try:
                timestamp = record["timestamp"]
                if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
                    raise ValueError("timestamp_must_be_non_negative_integer")
                if previous_timestamp is not None and timestamp <= previous_timestamp:
                    raise ValueError("pool_timestamps_must_be_unique_ascending")
                previous_timestamp = timestamp
                if record.get("unit") != "BTC" or record.get("provider") != "cryptoquant" or record.get("endpoint_id") != "miner_outflow" \
                        or record.get("source_window") != "day":
                    raise ValueError("incompatible_outflow_record_contract")
                values = {field: _finite(record[field]) for field in ("outflow_total", "outflow_top10", "outflow_mean")}
                if any(value < 0 for value in values.values()):
                    raise ValueError("outflow_value_must_be_non_negative")
                by_timestamp.setdefault(timestamp, []).append({"miner_symbol": symbol, "active": payload.get("active") is True,
                                                               "outflow_total_btc": values["outflow_total"], "outflow_top10_btc": values["outflow_top10"],
                                                               "outflow_mean_btc": values["outflow_mean"]})
            except (KeyError, TypeError, ValueError) as exc:
                invalid = True
                errors.append(f"pool_record_invalid:{symbol}:{index}:{exc}")
    records: list[dict[str, Any]] = []
    for timestamp in sorted(by_timestamp):
        observed = by_timestamp[timestamp]
        aggregate = _finite(sum(pool["outflow_total_btc"] for pool in observed))
        ordered = sorted(observed, key=lambda pool: (-pool["outflow_total_btc"], pool["miner_symbol"]))
        record_warnings: list[str] = []
        for rank, pool in enumerate(ordered, 1):
            pool["pool_share_ratio"] = None if aggregate == 0 else _finite(pool["outflow_total_btc"] / aggregate)
            pool["rank"] = rank
        if aggregate == 0:
            record_warnings.append("outflow_share_unavailable_zero_aggregate")
        missing = sorted(set(active_symbols) - {pool["miner_symbol"] for pool in observed})
        record_warnings.extend(f"outflow_missing_active_pool:{symbol}:{timestamp}" for symbol in missing)
        status = "partial" if missing else "available"
        records.append({"timestamp": timestamp, "aggregate_outflow_total_btc": aggregate, "expected_active_pools": len(active_symbols),
                        "observed_active_pools": sum(pool["active"] for pool in observed), "missing_active_pools": missing,
                        "pool_count_with_data": len(observed), "pools": ordered, "top_pool_symbol": ordered[0]["miner_symbol"] if ordered else None,
                        "top1_share_ratio": None if aggregate == 0 else ordered[0]["pool_share_ratio"],
                        "top3_share_ratio": None if aggregate == 0 else _finite(sum(pool["pool_share_ratio"] for pool in ordered[:3])),
                        "status": status, "warnings": record_warnings, "errors": []})
        warnings.extend(record_warnings)
    if invalid:
        records = []
    source_as_of = collection.get("metadata", {}).get("data_as_of")
    exact = next((record for record in records if record["timestamp"] == source_as_of), None) if isinstance(source_as_of, int) else None
    if not active_symbols:
        current = {"status": "unavailable", "value": None, "reason": "no_active_outflow_pools"}
        warnings.append("no_active_outflow_pools")
    elif invalid:
        current = {"status": "unavailable", "value": None, "reason": "source_pool_invalid"}
    elif exact:
        current = {"status": exact["status"], "timestamp": exact["timestamp"], "value": exact["aggregate_outflow_total_btc"], "unit": "BTC/day"}
    else:
        current = {"status": "unavailable", "value": None, "reason": "outflow_current_timestamp_not_available" if source_as_of is not None else "source_data_as_of_unavailable"}
        if source_as_of is not None:
            warnings.append("outflow_current_timestamp_not_available")
    partial_pool = any(isinstance(payload, Mapping) and payload.get("status") == "partial" for payload in pools.values())
    unavailable_active = any(symbol in active_symbols and isinstance(payload, Mapping) and payload.get("status") == "unavailable"
                             for symbol, payload in pools.items())
    status = ("invalid" if invalid else "unavailable" if not active_symbols or (unavailable_active and not records) else
              _status_from_source(source_status, has_records=bool(records), partial=partial_pool or unavailable_active
                                  or any(record["status"] == "partial" for record in records)))
    return {"feature_id": "miner_outflow_distribution", "status": status, "unit": "BTC/day", "records": records, "current": current,
            "active_symbols": active_symbols, "inactive_symbols": inactive_symbols, "warnings": _stable_unique(warnings), "errors": _stable_unique(errors),
            "metadata": {"data_as_of": current.get("timestamp") if current.get("status") in {"available", "partial"} else None,
                         "timestamps_processed": len(records), "calculation": "cross_pool_exact_timestamp_distribution"}}


def build_miner_outflow_total_series(distribution: Mapping[str, Any]) -> dict[str, Any]:
    blocked = distribution.get("status") in {"invalid", "unavailable"}
    records = [] if blocked else [{"timestamp": record["timestamp"], "value": record["aggregate_outflow_total_btc"], "unit": "BTC/day", "provider": "derived",
                "calculation_source": "miner_outflow_by_pool"} for record in distribution.get("records", [])]
    payload = _series_payload(metric_id="miner_outflow_total_btc", unit="BTC/day", source_count=len(records), records=records, unavailable=[],
                              warnings=list(distribution.get("warnings", [])), errors=list(distribution.get("errors", [])),
                              force_invalid=distribution.get("status") == "invalid", partial_reasons=distribution.get("status") == "partial")
    payload["status"] = distribution.get("status", "invalid")
    payload["current"] = ({"status": distribution["current"]["status"], "timestamp": distribution["current"]["timestamp"],
                           "value": distribution["current"]["value"], "unit": "BTC/day"}
                          if distribution.get("current", {}).get("status") in {"available", "partial"} else dict(distribution.get("current", {})))
    return payload




def build_miner_pressure_basis(outflow_series: Mapping[str, Any], sopr_7d: Mapping[str, Any], window: int = 90) -> dict[str, Any]:
    records = [r for r in outflow_series.get("records", []) if isinstance(r, Mapping)]
    current_sopr = sopr_7d.get("current", {}) if isinstance(sopr_7d, Mapping) else {}
    if not records:
        return {"source_metric_id": "miner_outflow_total_btc", "status": "unavailable",
                "current": {"status": "unavailable", "value": None, "reason": "miner_outflow_unavailable"},
                "components": {"reserve_outflow_z_90d": None, "sopr_7d": current_sopr.get("value")}}
    values = [_finite(r["value"]) for r in records[-window:]]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
    std = math.sqrt(variance)
    z = 0.0 if std == 0 else (values[-1] - mean) / std
    timestamp = int(records[-1]["timestamp"])
    return {"source_metric_id": "miner_outflow_total_btc", "status": outflow_series.get("status", "available"),
            "current": {"status": "available", "timestamp": timestamp, "value": z, "unit": "z_score",
                        "components": {"reserve_outflow_z_90d": z, "sopr_7d": current_sopr.get("value")}},
            "components": {"reserve_outflow_z_90d": z, "sopr_7d": current_sopr.get("value")},
            "window_days": min(window, len(values)), "calculation": "miner_outflow_zscore_with_sopr_context"}


def build_reserve_age_context(miners_series: Mapping[str, Any], utxo_source: Mapping[str, Any]) -> dict[str, Any]:
    warnings = [f"input_series_warning:utxo_age_distribution:{message}" for message in utxo_source.get("warnings", [])]
    errors = [f"input_series_error:utxo_age_distribution:{message}" for message in utxo_source.get("errors", [])]
    records: list[dict[str, Any]] = []
    ordering_errors = _source_timestamp_errors(utxo_source, "utxo_age_distribution")
    errors.extend(ordering_errors)
    invalid = utxo_source.get("status") == "invalid" or bool(ordering_errors)
    if not invalid and utxo_source.get("status") not in {"invalid", "unavailable"}:
        for index, record in enumerate(utxo_source.get("records", [])):
            try:
                timestamp = record["timestamp"]
                source_bands = record["bands"]
                if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0 or not isinstance(source_bands, Mapping):
                    raise ValueError("invalid_utxo_age_record_structure")
                native_values = [None if source_bands[band].get("native_btc") is None else _finite(source_bands[band]["native_btc"]) for band in UTXO_AGE_BANDS]
                if any(value is not None and value < 0 for value in native_values):
                    raise ValueError("utxo_native_btc_must_be_non_negative")
                total = _finite(sum(value for value in native_values if value is not None))
                bands: dict[str, Any] = {}
                for band, native in zip(UTXO_AGE_BANDS, native_values):
                    source_band = source_bands[band]
                    usd = None if source_band.get("usd") is None else _finite(source_band["usd"])
                    percent = None if source_band.get("percent") is None else _finite(source_band["percent"])
                    bands[band] = {"native_btc": native, "usd": usd, "provider_percent": percent,
                                   "derived_share_ratio": None if native is None or total == 0 else _finite(native / total)}
                record_warnings = ["utxo_age_share_unavailable_zero_total"] if total == 0 else []
                records.append({"timestamp": timestamp, "network_total_native_btc": total, "bands": bands, "status": "available",
                                "warnings": record_warnings, "errors": []})
                warnings.extend(record_warnings)
            except (KeyError, TypeError, ValueError) as exc:
                invalid = True
                errors.append(f"utxo_record_invalid:{index}:{exc}")
    network_status = _status_from_source(str(utxo_source.get("status", "invalid")), has_records=bool(records), invalid=invalid)
    input_current = utxo_source.get("current")
    if network_status == "invalid":
        records = []
        network_current = {"status": "unavailable", "reason": "invalid_utxo_source"}
    elif isinstance(input_current, Mapping) and input_current.get("status") == "available":
        timestamp = input_current.get("timestamp")
        exact = next((record for record in records if record["timestamp"] == timestamp), None)
        if exact:
            network_current = {"status": "available", **exact}
        else:
            records = []
            network_status = "invalid"
            network_current = {"status": "unavailable", "reason": "utxo_input_current_not_in_valid_records"}
            errors.append("utxo_input_current_not_in_valid_records")
    elif input_current is None:
        if records:
            network_current = {"status": "available", **records[-1]}
        else:
            network_current = {"status": "unavailable", "reason": "no_valid_records"}
            if network_status == "available":
                network_status = "unavailable"
    else:
        network_current = {"status": "unavailable", "reason": "input_current_unavailable"}
        if network_status == "available":
            network_status = "partial" if records else "unavailable"
        warnings.append("input_current_unavailable:utxo_age_distribution")
    miner_current = dict(miners_series.get("current", {"status": "unavailable", "reason": "no_valid_miner_unspent_supply"}))
    timestamps = [current.get("timestamp") for current in (miner_current, network_current) if current.get("status") == "available"]
    data_as_of = min(timestamps) if len(timestamps) == 2 else None
    status = max((str(miners_series.get("status", "invalid")), network_status), key=lambda value: STATUS_PRIORITY[value])
    if status == "invalid":
        records = []
        network_current = {"status": "unavailable", "reason": "source_feature_invalid"}
        data_as_of = None
    return {"feature_id": "reserve_age_context", "status": status,
            "miner_specific": {"scope": "miner_specific", "series_id": "miners_unspent_supply_btc", "current": miner_current,
                               "meaning": "coinbase_outputs_never_moved"},
            "network_context": {"scope": "bitcoin_network", "is_miner_specific": False, "records": records, "current": network_current},
            "warnings": _stable_unique([*miners_series.get("warnings", []), *warnings]), "errors": _stable_unique([*miners_series.get("errors", []), *errors]),
            "metadata": {"semantic_policy": "miner_unspent_supply_plus_bitcoin_network_age_context", "data_as_of": data_as_of}}


def build_miner_revenue_series(source: Mapping[str, Any], *, metric_id: str) -> dict[str, Any]:
    if metric_id != "miner_revenue_total_usd":
        raise ValueError("only miner_revenue_total_usd is a provider revenue series; block/fee revenue are derived")
    return _simple_extension_series(source, metric_id=metric_id, unit="USD/day", provider="glassnode", endpoint_id="revenue_sum")


def _derived_revenue_series(metric_id: str, unit: str, feature: Mapping[str, Any], field: str) -> dict[str, Any]:
    if feature.get("status") == "invalid":
        payload = _series_payload(metric_id=metric_id, unit=unit, source_count=0, records=[], unavailable=[], warnings=[],
                                  errors=list(feature.get("errors", [])), force_invalid=True)
        payload["current"] = {"status": "unavailable", "value": None, "reason": "source_feature_invalid"}
        return payload
    records = [{"timestamp": record["timestamp"], "value": record[field], "unit": unit, "provider": "derived",
                "calculation_source": "miner_revenue_breakdown"} for record in feature.get("records", []) if record.get(field) is not None]
    payload = _series_payload(metric_id=metric_id, unit=unit, source_count=len(feature.get("records", [])), records=records, unavailable=[],
                              warnings=list(feature.get("warnings", [])), errors=list(feature.get("errors", [])),
                              force_invalid=feature.get("status") == "invalid", partial_reasons=feature.get("status") == "partial")
    payload["status"] = feature.get("status", "invalid")
    feature_current = feature.get("current", {})
    if feature_current.get("status") in {"available", "partial"}:
        timestamp = feature_current.get("timestamp")
        exact = next((record for record in records if record["timestamp"] == timestamp), None)
        if exact:
            payload["current"] = {"status": feature_current["status"], "timestamp": timestamp, "value": exact["value"], "unit": unit}
        else:
            payload["status"] = "invalid"
            payload["current"] = {"status": "unavailable", "value": None, "reason": "derived_revenue_current_not_in_records"}
            payload["errors"] = _stable_unique([*payload["errors"], f"derived_revenue_current_not_in_records:{metric_id}"])
    else:
        payload["current"] = {"status": "unavailable", "value": None, "reason": feature_current.get("reason", "source_feature_current_unavailable")}
    return payload


def _revenue_source_errors(source: Mapping[str, Any], metric_id: str, *, unit: str, provider: str, endpoint_id: str) -> list[str]:
    errors = _source_timestamp_errors(source, metric_id)
    if source.get("unit") != unit:
        errors.append("incompatible_revenue_from_fees_contract:unit" if metric_id == "miner_revenue_from_fees" else f"incompatible_revenue_source_contract:{metric_id}:unit")
    for index, record in enumerate(source.get("records", [])):
        if not isinstance(record, Mapping):
            errors.append(f"revenue_source_record_invalid:{metric_id}:{index}:record_must_be_mapping")
            continue
        for field, expected in (("unit", unit), ("provider", provider), ("endpoint_id", endpoint_id)):
            if record.get(field) != expected:
                errors.append(f"incompatible_revenue_from_fees_contract:{field}" if metric_id == "miner_revenue_from_fees"
                              else f"incompatible_revenue_source_contract:{metric_id}:{field}")
        try:
            value = _finite(record.get("value"))
            if value < 0:
                raise ValueError("value_must_be_non_negative")
        except (TypeError, ValueError) as exc:
            errors.append(f"revenue_source_record_invalid:{metric_id}:{index}:{exc}")
    return _stable_unique(errors)


def build_miner_revenue_breakdown(total_source: Mapping[str, Any], fee_source: Mapping[str, Any],
                                  *, input_data_as_of: int | None = None) -> dict[str, Any]:
    """Derive fee and block-reward revenue from Glassnode total revenue + fee share.

    Glassnode ``revenue_from_fees`` is the share of miner revenue attributable
    to fees.  ``volume_mined_sum`` is therefore not required to reconstruct the
    revenue breakdown and is not polled by the normal Screen contract.
    """
    sources = {"miner_revenue_total_usd": total_source, "miner_revenue_from_fees": fee_source}
    warnings = [f"input_series_warning:{metric_id}:{message}" for metric_id, source in sources.items() for message in source.get("warnings", [])]
    errors = [f"input_series_error:{metric_id}:{message}" for metric_id, source in sources.items() for message in source.get("errors", [])]
    errors.extend(_revenue_source_errors(total_source, "miner_revenue_total_usd", unit="USD/day", provider="glassnode", endpoint_id="revenue_sum"))
    errors.extend(_revenue_source_errors(fee_source, "miner_revenue_from_fees", unit="provider_native_percentage", provider="glassnode",
                                         endpoint_id="revenue_from_fees"))
    statuses = [str(source.get("status", "invalid")) for source in sources.values()]
    if errors or "invalid" in statuses:
        status = "invalid"
        records: list[dict[str, Any]] = []
    elif "unavailable" in statuses:
        status = "unavailable"
        records = []
    else:
        total_map = {record["timestamp"]: record for record in total_source.get("records", [])}
        fee_map = {record["timestamp"]: record for record in fee_source.get("records", [])}
        common = sorted(set(total_map) & set(fee_map))
        if set(common) != (set(total_map) | set(fee_map)):
            warnings.append("revenue_timestamp_alignment_incomplete")
        records = []
        invalid = False
        for timestamp in common:
            try:
                total = _finite(total_map[timestamp]["value"])
                provider_value = _finite(fee_map[timestamp]["value"])
                if total < 0 or provider_value < 0:
                    raise ValueError("revenue_values_must_be_non_negative")
                # Glassnode fixtures and live endpoint commonly expose this as a
                # percentage. Accept a ratio too so the contract remains robust.
                provider_ratio = provider_value if 0 <= provider_value <= 1 else provider_value / 100.0
                if not 0 <= provider_ratio <= 1:
                    raise ValueError("provider_fee_share_out_of_range")
                fee = _finite(total * provider_ratio)
                block = _finite(total - fee)
                records.append({"timestamp": timestamp, "total_revenue_usd": total,
                                "block_reward_revenue_usd": block, "fee_revenue_usd": fee,
                                "derived_fee_share_ratio": provider_ratio,
                                "derived_fee_share_percent": _finite(provider_ratio * 100.0),
                                "provider_fee_value": provider_value,
                                "provider_fee_scale": "ratio" if provider_value <= 1 else "percent",
                                "provider_fee_ratio": provider_ratio, "provider_fee_difference_ratio": 0.0,
                                "unit": "USD/day", "status": "available", "warnings": [], "errors": []})
            except (KeyError, TypeError, ValueError) as exc:
                invalid = True
                errors.append(str(exc))
        if invalid:
            records = []
        status = "invalid" if invalid else "unavailable" if not records else "partial" if "partial" in statuses or warnings else "available"
    eligible = [record for record in records if input_data_as_of is None or record["timestamp"] <= input_data_as_of]
    current_record = next((record for record in records if record["timestamp"] == input_data_as_of), None) if input_data_as_of is not None else (records[-1] if records else None)
    if current_record is None and eligible:
        current_record = eligible[-1]
        warnings.append("revenue_current_before_input_data_as_of")
        if status == "available":
            status = "partial"
    current = ({"status": current_record["status"], "timestamp": current_record["timestamp"], "value": current_record["fee_revenue_usd"], "unit": "USD/day"}
               if current_record and status != "invalid" else {"status": "unavailable", "value": None,
                                                                "reason": "source_feature_invalid" if status == "invalid" else "no_common_revenue_timestamp"})
    return {"feature_id": "miner_revenue_breakdown", "status": status, "records": records, "current": current,
            "warnings": _stable_unique(warnings), "errors": _stable_unique(errors),
            "metadata": {"alignment": "exact_timestamp_intersection", "fee_revenue_formula": "total_revenue_usd_times_provider_fee_share",
                         "block_reward_formula": "total_revenue_usd_minus_fee_revenue_usd",
                         "provider_fee_scale_policy": "ratio_or_percent", "data_as_of": current.get("timestamp") if current.get("status") in {"available", "partial"} else None}}


def build_on_chain_miners_features(input_series: Mapping[str, Any], input_collections: Mapping[str, Any] | None = None,
                                   *, input_data_as_of: int | None = None, include_screen_extensions: bool = True) -> dict[str, Any]:
    # Core provider series are collected at 1h from Glassnode for legitimate daily OHLC.
    # Classification features continue to use one canonical UTC close per day.
    reserve_source = _daily_close_source(input_series["miner_reserve"], metric_id="miner_reserve")
    sopr_source = _daily_close_source(input_series["sopr"], metric_id="sopr")
    hashrate_source = _daily_close_source(input_series["hashrate"], metric_id="hashrate")
    difficulty_source = _daily_close_source(input_series["difficulty"], metric_id="difficulty")
    reserve = _copy_base_series(reserve_source, metric_id="miner_reserve_btc", unit="BTC", transform=_reserve_record)
    sopr    = _copy_base_series(sopr_source, metric_id="sopr", unit="ratio", transform=_sopr_record)
    mpi     = _copy_base_series(input_series["mpi"], metric_id="mpi", unit="z_score", transform=_mpi_record)
    def derived_from(source: Mapping[str, Any], source_id: str, metric_id: str, unit: str, builder: Callable[[Sequence[Mapping[str, Any]]], dict[str, Any]]) -> dict[str, Any]:
        if source["status"] in {"invalid", "unavailable"}:
            payload = _blocked_source_series(metric_id=metric_id, unit=unit, source_metric_id=source_id, source_status=str(source["status"]))
        else:
            payload = builder(source["records"])
        return apply_source_series_context(payload, source, source_id)

    sopr_7d    = derived_from(sopr_source, "sopr", "sopr_7d", "ratio", build_sopr_7d_series)
    hashrate   = derived_from(hashrate_source, "hashrate", "hashrate_eh_s", "EH/s", build_hashrate_eh_s_series)
    difficulty = derived_from(difficulty_source, "difficulty", "difficulty_t", "T", build_difficulty_trillion_series)
    net_position = _copy_direct_value_series(input_series["miner_net_position_change"], metric_id="miner_net_position_change",
                                             unit="BTC/day", source_metric_id="miner_net_position_change")
    if reserve_source["status"] == "invalid":
        reserve_trend = {"feature_id": "reserve_trend", "status": "invalid", "default_window_days": DEFAULT_RESERVE_TREND_DAYS, "windows": {},
                         "warnings": [], "errors": ["source_series_invalid:miner_reserve"]}
    elif reserve_source["status"] == "unavailable":
        reserve_trend = {"feature_id": "reserve_trend", "status": "unavailable", "default_window_days": DEFAULT_RESERVE_TREND_DAYS, "windows": {},
                         "warnings": ["source_series_unavailable:miner_reserve"], "errors": []}
    else:
        reserve_trend = build_reserve_trend_features(reserve["records"])
        if reserve_source["status"] == "partial" and reserve_trend["status"] == "available":
            reserve_trend["status"] = "partial"
        reserve_trend["warnings"] = _stable_unique([*reserve_trend["warnings"],
                                                     *(["source_series_partial:miner_reserve"] if reserve_source["status"] == "partial" else []),
                                                     *(f"input_series_warning:miner_reserve:{message}" for message in reserve_source.get("warnings", []))])
        reserve_trend["errors"] = _stable_unique([*reserve_trend["errors"],
                                                   *(f"input_series_error:miner_reserve:{message}" for message in reserve_source.get("errors", []))])
    daily_candles = {
        "miner_reserve": build_daily_ohlc_from_intraday(input_series["miner_reserve"].get("records", []), unit="BTC"),
        "sopr_7d": build_sopr_7d_intraday_candles(input_series["sopr"].get("records", [])),
        "hashrate": build_daily_ohlc_from_intraday(input_series["hashrate"].get("records", []), unit="EH/s", value_transform=lambda v: v / HASHES_PER_EXAHASH),
        "difficulty": build_daily_ohlc_from_intraday(input_series["difficulty"].get("records", []), unit="T", value_transform=lambda v: v / DIFFICULTY_PER_TRILLION),
    }
    series = {"miner_reserve_btc": reserve, "sopr": sopr, "sopr_7d": sopr_7d, "hashrate_eh_s": hashrate,
              "difficulty_t": difficulty, "miner_net_position_change": net_position, "mpi": mpi}
    for chart_id, series_id in (("miner_reserve", "miner_reserve_btc"), ("sopr_7d", "sopr_7d"), ("hashrate", "hashrate_eh_s"), ("difficulty", "difficulty_t")):
        series[series_id]["daily_candles"] = daily_candles[chart_id]
        series[series_id]["metadata"] = {**dict(series[series_id].get("metadata", {})), "daily_ohlc_source": "glassnode_intraday_1h",
                                           "daily_ohlc_candles": len(daily_candles[chart_id]), "ohlc_origin": "processing_derived"}
    features = {"reserve_trend": reserve_trend,
                         "miner_pressure_basis": ({"source_metric_id": "mpi", "status": mpi["status"], "current": mpi["current"], "previous": None,
                                                   "change_1d": None, "unit": "z_score"} if mpi["status"] in {"invalid", "unavailable"} else _mpi_basis(mpi)),
                         "sopr_regime_basis": {"source_metric_id": "sopr_7d", "status": sopr_7d["status"], "current": sopr_7d["current"],
                                               "raw_sopr_current": sopr["current"]},
                         "net_position_basis": {"source_metric_id": "miner_net_position_change", "status": net_position["status"],
                                                "current": net_position["current"]}}
    if include_screen_extensions:
        collections = input_collections or {}
        outflow = build_miner_outflow_distribution(collections["miner_outflow_by_pool"])
        miners_unspent = build_miners_unspent_supply_series(input_series["miners_unspent_supply"])
        reserve_age = build_reserve_age_context(miners_unspent, input_series["utxo_age_distribution"])
        total_revenue = build_miner_revenue_series(input_series["miner_revenue_total_usd"], metric_id="miner_revenue_total_usd")
        revenue = build_miner_revenue_breakdown(input_series["miner_revenue_total_usd"], input_series["miner_revenue_from_fees"],
                                                input_data_as_of=input_data_as_of)
        block_revenue = _derived_revenue_series("miner_block_reward_revenue_usd", "USD/day", revenue, "block_reward_revenue_usd")
        nupl = build_nupl_series(input_series["nupl"])
        direct_outflow = _copy_direct_value_series(input_series["miner_outflow_total"], metric_id="miner_outflow_total_btc",
                                                  unit="BTC/day", source_metric_id="miner_outflow_total")
        features["miner_pressure_basis"] = build_miner_pressure_basis(direct_outflow, sopr_7d)
        features["mpi_context"] = _mpi_basis(mpi) if mpi.get("status") not in {"invalid", "unavailable"} else {"status": mpi.get("status"), "current": mpi.get("current")}
        series.update({"miners_unspent_supply_btc": miners_unspent, "nupl": nupl, "miner_outflow_total_btc": direct_outflow,
                       "miner_revenue_total_usd": total_revenue, "miner_block_reward_revenue_usd": block_revenue,
                       "miner_fee_revenue_usd": _derived_revenue_series("miner_fee_revenue_usd", "USD/day", revenue, "fee_revenue_usd"),
                       "miner_fee_share_ratio": _derived_revenue_series("miner_fee_share_ratio", "ratio", revenue, "derived_fee_share_ratio")})
        features.update({"miner_outflow_distribution": outflow, "reserve_age_context": reserve_age,
                         "miner_revenue_breakdown": revenue, "nupl_phase_basis": build_nupl_phase_basis(nupl)})
    return {"series": series, "features": features}
