from __future__ import annotations

import copy
import math
import time
from collections.abc import Mapping, Sequence
from datetime        import datetime, timezone
from typing          import Any, Callable

from .on_chain_miners_data_raw_extract import (
    COINGLASS_PROVIDER,
    COLLECTION_EXTENSION_IDS,
    CORE_METRIC_IDS,
    CRYPTOQUANT_PROVIDER,
    ENDPOINTS,
    GLASSNODE_PROVIDER,
    ON_CHAIN_MINERS_FAMILY,
    SCREEN_EXTENSION_METRIC_IDS,
    SECONDS_PER_DAY,
    TIME_SERIES_EXTENSION_IDS,
    UTXO_AGE_BANDS,
    DEFAULT_INCLUDE_SCREEN_EXTENSIONS,
    VALID_MODES,
    OnChainMinersFetcher,
    OnChainMinersRawExtractor,
    is_validated_miner_flag,
    resolve_existing_input_state,
)


UNITS = {
    "miner_reserve": "BTC", "sopr": "ratio", "hashrate": "H/s", "difficulty": "provider_native_difficulty",
    "miner_net_position_change": "BTC/day", "miner_outflow_total": "BTC/day", "mpi": "z_score",
    "puell_multiple": "ratio", "sth_sopr": "ratio", "lth_sopr": "ratio", "nupl": "ratio",
    "miners_unspent_supply": "BTC", "utxo_age_distribution": "mixed",
    "miner_revenue_total_usd": "USD/day",
    "miner_revenue_from_fees": "provider_native_percentage",
}
# Daily providers may omit the open boundary or the still-incomplete current day.
HISTORY_COVERAGE_TOLERANCE_DAYS = 2


def _records_for(existing_contract: Mapping[str, Any] | None, metric_id: str) -> list[Mapping[str, Any]]:
    series = (existing_contract or {}).get("series", {})
    payload = series.get(metric_id, {}) if isinstance(series, Mapping) else {}
    records = payload.get("records", []) if isinstance(payload, Mapping) else []
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def determine_on_chain_miners_input_mode(*, existing_contract: Mapping[str, Any] | None = None,
                                         recovery_requests: Sequence[Mapping[str, Any]] | None = None, requested_mode: str | None = None) -> str:
    existing_contract = resolve_existing_input_state(existing_contract)
    if requested_mode is not None:
        if requested_mode not in VALID_MODES:
            raise ValueError(f"Unsupported on_chain_miners input mode: {requested_mode}")
        if requested_mode == "recovery" and not recovery_requests:
            raise ValueError("recovery mode requires recovery_requests")
        return requested_mode
    if recovery_requests:
        return "recovery"
    return "incremental" if all(_records_for(existing_contract, metric_id) for metric_id in CORE_METRIC_IDS) else "bootstrap"


def unwrap_cryptoquant_response(response: Mapping[str, Any] | None) -> tuple[str | None, list[Any]]:
    if not isinstance(response, Mapping):
        raise ValueError("cryptoquant_response_must_be_mapping")
    status = response.get("status")
    if not isinstance(status, Mapping):
        raise ValueError("cryptoquant_status_missing")
    code = status.get("code")
    if code not in (200, "200"):
        raise ValueError(f"cryptoquant_request_failed:{code}:{status.get('message')}")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("cryptoquant_result_missing")
    if "window" not in result:
        raise ValueError("cryptoquant_window_missing")
    data = result.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        raise ValueError("cryptoquant_data_must_be_sequence")
    return str(result["window"]) if result["window"] is not None else None, list(data)


def unwrap_glassnode_response(response: Sequence[Any] | None) -> list[Any]:
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes, bytearray)):
        raise ValueError("glassnode_response_must_be_sequence")
    return list(response)


def unwrap_coinglass_response(response: Mapping[str, Any] | None) -> list[Any]:
    if not isinstance(response, Mapping):
        raise ValueError("coinglass_response_must_be_mapping")
    code = response.get("code")
    if code not in (0, "0", 200, "200"):
        raise ValueError(f"coinglass_request_failed:{code}:{response.get('msg')}")
    data = response.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        raise ValueError("coinglass_data_must_be_sequence")
    return list(data)


def parse_cryptoquant_daily_timestamp(date_value: Any) -> int:
    if not isinstance(date_value, str):
        raise ValueError("cryptoquant_date_must_be_YYYY-MM-DD")
    try:
        parsed = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("cryptoquant_date_must_be_YYYY-MM-DD") from exc
    return int(parsed.timestamp())


def normalize_unix_timestamp(timestamp: Any) -> int:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or int(timestamp) != timestamp or timestamp < 0:
        raise ValueError("timestamp_must_be_non_negative_integer")
    normalized = int(timestamp)
    return normalized // 1000 if normalized > 100_000_000_000 else normalized


def normalize_optional_finite_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value_must_be_number_or_null")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("value_must_be_finite")
    return 0.0 if normalized == 0.0 else normalized


def _base_record(metric_id: str, timestamp: int, value: float | None, source_window: str | None = None) -> dict[str, Any]:
    endpoint = ENDPOINTS[metric_id]
    output = {"timestamp": timestamp, "value": value, "unit": UNITS[metric_id], "provider": endpoint["provider"],
              "endpoint_id": endpoint["endpoint_id"], "source_field": endpoint["source_field"]}
    if source_window is not None:
        output["source_window"] = source_window
    return output


def normalize_miner_reserve_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _base_record("miner_reserve", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")))


def normalize_sopr_record(record: Mapping[str, Any], source_window: str = "1h") -> dict[str, Any]:
    # Glassnode is canonical for SOPR in VR1; auxiliary SOPR variants are not
    # fabricated when the provider returns the scalar series only.
    output = _base_record("sopr", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")), source_window)
    output.update({"sopr": output["value"], "a_sopr": None, "sth_sopr": None, "lth_sopr": None})
    return output


def normalize_hashrate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _base_record("hashrate", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")), "1h")


def normalize_difficulty_record(record: Mapping[str, Any], source_window: str = "1h") -> dict[str, Any]:
    return _base_record("difficulty", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")), source_window)


def normalize_miner_net_position_change_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _base_record("miner_net_position_change", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")), "24h")


def normalize_miner_outflow_total_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _base_record("miner_outflow_total", normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")), "24h")


def normalize_mpi_record(record: Mapping[str, Any], source_window: str = "day") -> dict[str, Any]:
    return _base_record("mpi", parse_cryptoquant_daily_timestamp(record.get("date")), normalize_optional_finite_number(record.get("mpi")), source_window)


def _normalize_coinglass_record(metric_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
    output = _base_record(metric_id, normalize_unix_timestamp(record.get("timestamp")), normalize_optional_finite_number(record.get(ENDPOINTS[metric_id]["source_field"])))
    if "price" in record:
        output["price"] = normalize_optional_finite_number(record.get("price"))
    return output


def normalize_puell_multiple_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_coinglass_record("puell_multiple", record)


def normalize_sth_sopr_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_coinglass_record("sth_sopr", record)


def normalize_lth_sopr_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_coinglass_record("lth_sopr", record)


def normalize_nupl_record(record: Mapping[str, Any]) -> dict[str, Any]:
    output = _normalize_coinglass_record("nupl", record)
    output["price_usd"] = output.pop("price", None)
    return output


def _normalize_glassnode_extension(metric_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
    output = _base_record(metric_id, normalize_unix_timestamp(record.get("t")), normalize_optional_finite_number(record.get("v")))
    if metric_id == "miners_unspent_supply":
        output["scope"] = "miner_specific"
    return output


def normalize_utxo_age_distribution_record(record: Mapping[str, Any], source_window: str = "day") -> dict[str, Any]:
    bands = {band: {"native_btc": normalize_optional_finite_number(record.get(f"range_{band}")),
                    "usd": normalize_optional_finite_number(record.get(f"range_{band}_usd")),
                    "percent": normalize_optional_finite_number(record.get(f"range_{band}_percent"))} for band in UTXO_AGE_BANDS}
    return {"timestamp": parse_cryptoquant_daily_timestamp(record.get("date")), "scope": "bitcoin_network", "provider": "cryptoquant",
            "endpoint_id": "utxo_age_distribution", "source_window": source_window, "bands": bands}


def _copy_normalizing_negative_zero(value: Any) -> Any:
    if isinstance(value, float) and value == 0.0:
        return 0.0
    if isinstance(value, Mapping):
        return {str(key): _copy_normalizing_negative_zero(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_copy_normalizing_negative_zero(item) for item in value]
    return copy.deepcopy(value)


def upsert_on_chain_records(existing_records: Sequence[Mapping[str, Any]], incoming_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_timestamp: dict[int, dict[str, Any]] = {}
    for record in (*existing_records, *incoming_records):
        timestamp = normalize_unix_timestamp(record.get("timestamp"))
        by_timestamp[timestamp] = _copy_normalizing_negative_zero(dict(record))
        by_timestamp[timestamp]["timestamp"] = timestamp
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


NORMALIZERS: dict[str, Callable[..., dict[str, Any]]] = {
    "miner_reserve": normalize_miner_reserve_record, "sopr": normalize_sopr_record, "hashrate": normalize_hashrate_record,
    "difficulty": normalize_difficulty_record, "miner_net_position_change": normalize_miner_net_position_change_record,
    "miner_outflow_total": normalize_miner_outflow_total_record, "mpi": normalize_mpi_record, "puell_multiple": normalize_puell_multiple_record,
    "sth_sopr": normalize_sth_sopr_record, "lth_sopr": normalize_lth_sopr_record, "nupl": normalize_nupl_record,
    "miners_unspent_supply": lambda record: _normalize_glassnode_extension("miners_unspent_supply", record),
    "miner_revenue_total_usd": lambda record: _normalize_glassnode_extension("miner_revenue_total_usd", record),
    "miner_revenue_from_fees": lambda record: _normalize_glassnode_extension("miner_revenue_from_fees", record),
    "utxo_age_distribution": normalize_utxo_age_distribution_record,
}


def _unwrap(metric_id: str, response: Any) -> tuple[str | None, list[Any]]:
    provider = ENDPOINTS[metric_id]["provider"]
    if provider == CRYPTOQUANT_PROVIDER:
        return unwrap_cryptoquant_response(response)
    if provider == GLASSNODE_PROVIDER:
        return None, unwrap_glassnode_response(response)
    if provider == COINGLASS_PROVIDER:
        return None, unwrap_coinglass_response(response)
    raise ValueError("unsupported_provider")


def _detect_gaps(records: Sequence[Mapping[str, Any]], requested_from: int | None, requested_to: int | None) -> list[dict[str, int]]:
    timestamps = sorted({int(record["timestamp"]) for record in records if requested_from is None or int(record["timestamp"]) >= requested_from
                         if requested_to is None or int(record["timestamp"]) <= requested_to})
    gaps = []
    for earlier, later in zip(timestamps, timestamps[1:]):
        missing_days = (later - earlier) // SECONDS_PER_DAY - 1
        if later - earlier > SECONDS_PER_DAY and missing_days > 0:
            gaps.append({"gap_start_timestamp": earlier + SECONDS_PER_DAY, "gap_end_timestamp": later - SECONDS_PER_DAY, "missing_days": missing_days})
    return gaps


def _history_coverage(records: Sequence[Mapping[str, Any]], requested_from: Any, requested_to: Any) -> dict[str, Any]:
    if not isinstance(requested_from, int) or not isinstance(requested_to, int) or requested_from > requested_to:
        return {"requested_days": 0, "covered_days": 0, "coverage_ratio": 0.0, "history_complete": False}
    requested_days = (requested_to - requested_from) // SECONDS_PER_DAY + 1
    covered_dates  = {int(record["timestamp"]) - int(record["timestamp"]) % SECONDS_PER_DAY for record in records
                      if isinstance(record, Mapping) and isinstance(record.get("timestamp"), int)
                      and requested_from <= int(record["timestamp"]) <= requested_to}
    covered_days   = min(len(covered_dates), requested_days)
    coverage_ratio = min(1.0, max(0.0, covered_days / requested_days)) if requested_days else 0.0
    history_complete = covered_days >= max(1, requested_days - HISTORY_COVERAGE_TOLERANCE_DAYS)
    return {"requested_days": requested_days, "covered_days": covered_days, "coverage_ratio": coverage_ratio, "history_complete": history_complete}


def preprocess_on_chain_metric(*, metric_id: str, raw_payload: Mapping[str, Any], existing_series: Mapping[str, Any] | None = None,
                               mode: str = "bootstrap") -> dict[str, Any]:
    endpoint          = ENDPOINTS[metric_id]
    previous          = (existing_series or {}).get("records", [])
    previous          = previous if isinstance(previous, Sequence) and not isinstance(previous, (str, bytes, bytearray)) else []
    incoming: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    received = 0
    envelope_invalid = False
    request_failed   = raw_payload.get("status") != "ok"
    requested_from   = raw_payload.get("from_timestamp")
    requested_to     = raw_payload.get("to_timestamp")
    if request_failed:
        errors.append(str((raw_payload.get("error") or {}).get("message", "request_failed")))
        if previous:
            warnings.append("latest_request_failed_history_preserved")
    else:
        try:
            source_window, raw_records = _unwrap(metric_id, raw_payload.get("response"))
            received = len(raw_records)
            for index, raw_record in enumerate(raw_records):
                try:
                    if not isinstance(raw_record, Mapping):
                        raise ValueError("provider_record_must_be_mapping")
                    normalizer = NORMALIZERS[metric_id]
                    normalized = normalizer(raw_record, source_window or "day") if endpoint["provider"] == CRYPTOQUANT_PROVIDER else normalizer(raw_record)
                    if requested_from is not None and normalized["timestamp"] < requested_from:
                        continue
                    if requested_to is not None and normalized["timestamp"] > requested_to:
                        continue
                    if metric_id == "utxo_age_distribution":
                        if not any(value is not None for band in normalized["bands"].values() for value in band.values()):
                            unavailable.append({"timestamp": normalized["timestamp"], "status": "unavailable", "reason": "all_age_bands_unavailable"})
                        else:
                            incoming.append(normalized)
                    elif normalized["value"] is None:
                        unavailable.append({"timestamp": normalized["timestamp"], "status": "unavailable", "reason": "provider_value_null", "source_field": endpoint["source_field"]})
                    else:
                        incoming.append(normalized)
                except (TypeError, ValueError) as exc:
                    reason = str(exc)
                    field = endpoint.get("source_field") or ("bands" if metric_id == "utxo_age_distribution" else "value")
                    invalid.append({"record_index": index, "status": "invalid", "reason": reason, "field": field})
                    if reason == "value_must_be_finite":
                        warnings.append(f"invalid_record:{metric_id}:{field}:value_must_be_finite")
        except (TypeError, ValueError) as exc:
            envelope_invalid = True
            errors.append(str(exc))

    incoming = upsert_on_chain_records([], incoming)
    records  = upsert_on_chain_records(previous, incoming)
    gaps = _detect_gaps(records, requested_from, requested_to)
    coverage = _history_coverage(records, requested_from, requested_to)
    if gaps:
        warnings.append("daily_gaps_detected")
    if not coverage["history_complete"]:
        warnings.append("requested_history_not_fully_covered")
    if envelope_invalid:
        status = "invalid"
        if records:
            warnings.append("invalid_envelope_history_preserved")
    elif request_failed:
        status = "partial" if records else "unavailable"
    elif records and (unavailable or invalid or gaps or not coverage["history_complete"]):
        status = "partial"
    elif records:
        status = "available"
    elif invalid:
        status = "invalid"
    else:
        status = "unavailable"
    first = records[0]["timestamp"] if records else None
    last  = records[-1]["timestamp"] if records else None
    output = {"metric_id": metric_id, "provider": endpoint["provider"], "endpoint_id": endpoint["endpoint_id"], "source_field": endpoint.get("source_field"),
            "source_window": "day" if endpoint["provider"] == CRYPTOQUANT_PROVIDER else None, "unit": UNITS[metric_id], "status": status,
            "incoming_records": incoming, "records": records, "unavailable_records": unavailable, "invalid_records": invalid, "gaps": gaps,
            "warnings": warnings, "errors": errors,
            "metadata": {"records_before": len(previous), "records_received": received, "records_valid_received": len(incoming), "records_after": len(records),
                         "first_timestamp": first, "last_timestamp": last, "requested_from": requested_from, "requested_to": requested_to,
                         "history_preserved": bool(previous and len(records) >= len(previous)), "first_available_timestamp": first,
                         "last_available_timestamp": last, "records_available": len(records), **coverage,
                          "history_coverage_tolerance_days": HISTORY_COVERAGE_TOLERANCE_DAYS}}
    if metric_id == "utxo_age_distribution":
        output["metadata"].update({"scope": "bitcoin_network", "is_miner_specific": False})
    return output


def preprocess_miner_entities(raw_payload: Mapping[str, Any], existing_collection: Mapping[str, Any] | None = None) -> dict[str, Any]:
    valid_by_symbol: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    errors: list[str] = []
    received = 0
    previous = _copy_normalizing_negative_zero((existing_collection or {}).get("records", []))
    previous = previous if isinstance(previous, list) else []
    warnings: list[str] = []
    catalog_source = "live"
    refresh_succeeded = False
    request_failed = raw_payload.get("status") != "ok"
    try:
        response = raw_payload.get("response")
        if request_failed:
            raise RuntimeError("miner_entity_catalog_request_failed")
        if not isinstance(response, Mapping) or not isinstance(response.get("status"), Mapping) \
                or response["status"].get("code") not in (200, "200") or not isinstance(response.get("result"), Mapping):
            raise ValueError("miner_entity_catalog_invalid")
        data = response["result"].get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
            raise ValueError("miner_entity_catalog_invalid")
        refresh_succeeded = True
        received = len(data)
        for index, item in enumerate(data):
            if not isinstance(item, Mapping):
                invalid.append({"index": index, "status": "invalid", "reason": "entity_must_be_mapping"})
                continue
            if not is_validated_miner_flag(item.get("is_validated")):
                invalid.append({"index": index, "status": "invalid", "reason": "is_validated_must_be_integer_one"})
                continue
            if not isinstance(item.get("symbol"), str) or not item["symbol"].strip():
                invalid.append({"index": index, "status": "invalid", "reason": "entity_symbol_invalid"})
                continue
            symbol = item["symbol"].strip()
            valid_by_symbol[symbol] = {"name": item.get("name"), "symbol": symbol, "is_validated": 1,
                                       "market_type": item.get("market_type"), "provider": "cryptoquant"}
    except RuntimeError as exc:
        errors.append(str(exc))
        catalog_source = "existing_state" if previous else None
        if previous:
            warnings.extend(("miner_entity_catalog_request_failed", "miner_entity_catalog_reused_from_existing_state"))
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
        catalog_source = "existing_state" if previous else None
        if previous:
            warnings.extend(("miner_entity_catalog_invalid", "miner_entity_catalog_reused_from_existing_state"))
    records = [valid_by_symbol[symbol] for symbol in sorted(valid_by_symbol)] if refresh_succeeded else previous
    if request_failed:
        status = "partial" if records else "unavailable"
    elif errors:
        status = "invalid"
    else:
        status = "partial" if invalid and records else "invalid" if invalid and not records else "available" if records else "unavailable"
    symbols = [record.get("symbol") for record in records if isinstance(record, Mapping) and isinstance(record.get("symbol"), str)]
    reused = sorted(symbols) if catalog_source == "existing_state" else []
    if refresh_succeeded and not records:
        warnings.append("no_validated_miner_entities")
    return {"metric_id": "miner_entities", "status": status, "records": records, "invalid_records": invalid,
            "warnings": list(dict.fromkeys(warnings)), "errors": list(dict.fromkeys(errors)),
            "metadata": {"entities_received": received, "entities_validated": len(records), "symbols": sorted(symbols),
                         "catalog_source": catalog_source, "catalog_refresh_succeeded": refresh_succeeded, "reused_symbols": reused}}


def _existing_pools(existing_collection: Mapping[str, Any] | None) -> Mapping[str, Any]:
    pools = (existing_collection or {}).get("pools", {})
    return pools if isinstance(pools, Mapping) else {}


def preprocess_miner_outflow_by_pool(raw_payload: Mapping[str, Any], existing_collection: Mapping[str, Any] | None = None) -> dict[str, Any]:
    pools = {symbol: _copy_normalizing_negative_zero(dict(payload)) for symbol, payload in _existing_pools(existing_collection).items() if isinstance(payload, Mapping)}
    historical_symbols = set(pools)
    requested_symbols = sorted(set(raw_payload.get("entity_symbols", [])))
    catalog_state = raw_payload.get("catalog_state")
    catalog_source = raw_payload.get("catalog_source")
    catalog_reused = catalog_source == "existing_state" and raw_payload.get("catalog_refresh_succeeded") is False
    if catalog_state == "valid":
        for symbol, pool in pools.items():
            pool["active"] = symbol in requested_symbols
    requests = raw_payload.get("requests", [])
    requests = requests if isinstance(requests, Sequence) and not isinstance(requests, (str, bytes, bytearray)) else []
    collection_warnings: list[str] = []
    collection_errors: list[str] = []
    if catalog_reused:
        collection_warnings.append("miner_outflow_catalog_reused")
    if raw_payload.get("fanout_skipped_no_symbols"):
        collection_warnings.append("miner_outflow_fanout_skipped_no_symbols")
    successful_symbols: set[str] = set()
    failed_symbols: set[str] = set()
    for request in requests:
        if not isinstance(request, Mapping) or not isinstance(request.get("miner_symbol"), str):
            collection_errors.append("outflow_request_must_identify_miner_symbol")
            continue
        symbol = request.get("miner_symbol")
        previous = pools.get(symbol, {}).get("records", [])
        records_in: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        if request.get("status") == "ok":
            try:
                window, data = unwrap_cryptoquant_response(request.get("response"))
                for index, item in enumerate(data):
                    try:
                        if not isinstance(item, Mapping):
                            raise ValueError("provider_record_must_be_mapping")
                        normalized = {"timestamp": parse_cryptoquant_daily_timestamp(item.get("date")),
                                      "outflow_total": normalize_optional_finite_number(item.get("outflow_total")),
                                      "outflow_top10": normalize_optional_finite_number(item.get("outflow_top10")),
                                      "outflow_mean": normalize_optional_finite_number(item.get("outflow_mean")), "unit": "BTC", "provider": "cryptoquant",
                                      "endpoint_id": "miner_outflow", "source_window": window}
                        if all(normalized[field] is None for field in ("outflow_total", "outflow_top10", "outflow_mean")):
                            unavailable.append({"timestamp": normalized["timestamp"], "status": "unavailable", "reason": "provider_values_null"})
                        else:
                            records_in.append(normalized)
                    except (TypeError, ValueError) as exc:
                        reason = str(exc)
                        invalid.append({"record_index": index, "status": "invalid", "reason": reason,
                                        "field": "outflow_values"})
                        if reason == "value_must_be_finite":
                            warnings.append("invalid_record:miner_outflow_by_pool:outflow_values:value_must_be_finite")
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        else:
            errors.append((request.get("error") or {}).get("message", "request_failed"))
        records = upsert_on_chain_records(previous, records_in)
        if request.get("status") != "ok":
            failed_symbols.add(symbol)
            if records:
                warnings.append(f"pool_update_failed_using_preserved_history:{symbol}")
                collection_warnings.append(f"pool_update_failed_using_preserved_history:{symbol}")
        else:
            successful_symbols.add(symbol)
        status = ("invalid" if errors and request.get("status") == "ok" else "partial" if errors and records else
                  "unavailable" if errors else "partial" if records and (invalid or unavailable) else "available" if records else
                  "invalid" if invalid else "unavailable")
        pools[symbol] = {"miner_symbol": symbol, "status": status, "active": True, "records": records, "unavailable_records": unavailable,
                         "invalid_records": invalid, "warnings": warnings, "errors": errors,
                         "metadata": {"first_timestamp": records[0]["timestamp"] if records else None,
                                      "last_timestamp": records[-1]["timestamp"] if records else None, "records_after": len(records)}}
    active_symbols = sorted(symbol for symbol, pool in pools.items() if pool.get("active") is True)
    inactive_symbols = sorted(set(pools) - set(active_symbols))
    active_statuses = [pools[symbol].get("status") for symbol in active_symbols]
    data_values: list[int] = []
    for symbol in active_symbols:
        timestamp = pools[symbol].get("metadata", {}).get("last_timestamp")
        if isinstance(timestamp, int):
            data_values.append(timestamp)
        else:
            collection_warnings.append(f"active_pool_data_as_of_unavailable:{symbol}")
    data_as_of = min(data_values) if active_symbols and len(data_values) == len(active_symbols) else None
    usable = bool(data_values)
    if collection_errors or any(value == "invalid" for value in active_statuses):
        status = "invalid"
    elif not active_symbols or not usable:
        status = "unavailable"
    elif catalog_reused or failed_symbols or any(value != "available" for value in active_statuses) or data_as_of is None:
        status = "partial"
    else:
        status = "available"
    return {"metric_id": "miner_outflow_by_pool", "status": status, "unit": "BTC", "pools": {symbol: pools[symbol] for symbol in sorted(pools)},
            "warnings": list(dict.fromkeys(collection_warnings)), "errors": list(dict.fromkeys(collection_errors)),
            "metadata": {"pools_historical": len(set(pools) | historical_symbols), "pools_active": len(active_symbols),
                         "pools_inactive": len(inactive_symbols), "pools_requested": len(requested_symbols),
                         "pools_available": active_statuses.count("available"), "pools_partial": active_statuses.count("partial"),
                         "pools_unavailable": active_statuses.count("unavailable"), "active_symbols": active_symbols,
                         "inactive_symbols": inactive_symbols, "requested_symbols": requested_symbols,
                         "successful_symbols": sorted(successful_symbols), "failed_symbols": sorted(failed_symbols),
                         "symbols": sorted(pools), "catalog_source": catalog_source,
                         "catalog_refresh_succeeded": raw_payload.get("catalog_refresh_succeeded"), "data_as_of": data_as_of}}


def evaluate_on_chain_miners_quality(series: Mapping[str, Mapping[str, Any]], include_enrichment: bool = False,
                                     collections: Mapping[str, Mapping[str, Any]] | None = None,
                                     include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS) -> dict[str, Any]:
    collections = collections or {}
    availability = {metric_id: payload.get("status", "invalid") for metric_id, payload in {**series, **collections}.items()}
    warnings: list[str] = []
    errors: list[str] = []
    required_ids = CORE_METRIC_IDS + (SCREEN_EXTENSION_METRIC_IDS if include_screen_extensions else ())
    missing_fields = [metric_id for metric_id in required_ids if availability.get(metric_id) in {None, "unavailable", "invalid"}]
    for metric_id, payload in {**series, **collections}.items():
        target = errors if metric_id in required_ids and payload.get("status") == "invalid" else warnings
        target.extend(f"{metric_id}: {message}" for message in payload.get("errors", []))
        warnings.extend(f"{metric_id}: {message}" for message in payload.get("warnings", []))
    required_statuses = [availability.get(metric_id, "invalid") for metric_id in required_ids]
    status = "invalid" if "invalid" in required_statuses or errors else "ok" if all(value == "available" for value in required_statuses) else "partial"
    temporal_ids = CORE_METRIC_IDS + (TIME_SERIES_EXTENSION_IDS if include_screen_extensions else ())
    last_values = [series[metric_id]["metadata"]["last_available_timestamp"] for metric_id in temporal_ids if series.get(metric_id, {}).get("records")]
    if include_screen_extensions:
        outflow_as_of = collections.get("miner_outflow_by_pool", {}).get("metadata", {}).get("data_as_of")
        if outflow_as_of is not None:
            last_values.append(outflow_as_of)
    expected_count = len(temporal_ids) + (1 if include_screen_extensions else 0)
    data_as_of = min(last_values) if len(last_values) == expected_count else None
    return {"status": status, "availability": availability, "missing_fields": missing_fields, "warnings": warnings, "errors": errors,
            "recovery_required": status != "ok", "data_as_of": data_as_of}


class OnChainMinersInputPreprocessor:
    def __init__(self, *, raw_extractor: OnChainMinersRawExtractor, existing_contract: Mapping[str, Any] | None = None) -> None:
        self.raw_extractor     = raw_extractor
        self.existing_contract = resolve_existing_input_state(existing_contract)

    def run(self, *, requested_mode: str | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
            reference_timestamp: int, include_enrichment: bool = False,
            include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS, execution_timestamp: int | None = None) -> dict[str, Any]:
        execution_timestamp = normalize_unix_timestamp(int(time.time()) if execution_timestamp is None else execution_timestamp)
        mode = determine_on_chain_miners_input_mode(existing_contract=self.existing_contract, recovery_requests=recovery_requests, requested_mode=requested_mode)
        raw  = self.raw_extractor.run(mode=mode, reference_timestamp=reference_timestamp, existing_contract=self.existing_contract,
                                      recovery_requests=recovery_requests, include_enrichment=include_enrichment,
                                      include_screen_extensions=include_screen_extensions,
                                      execution_timestamp=execution_timestamp)
        existing_series = self.existing_contract.get("series", {})
        existing_series = existing_series if isinstance(existing_series, Mapping) else {}
        series = _copy_normalizing_negative_zero(dict(existing_series)) if mode in {"incremental", "recovery"} else {}
        for metric_id, payload in raw["raw"].items():
            if metric_id not in COLLECTION_EXTENSION_IDS:
                series[metric_id] = preprocess_on_chain_metric(
                    metric_id=metric_id, raw_payload=payload, existing_series=existing_series.get(metric_id, {}), mode=mode)
        required_series = CORE_METRIC_IDS + (TIME_SERIES_EXTENSION_IDS if include_screen_extensions else ())
        if mode == "recovery":
            for metric_id in required_series:
                if metric_id not in series:
                    series[metric_id] = preprocess_on_chain_metric(
                        metric_id=metric_id,
                        raw_payload={"status": "error", "error": {"message": "not_requested_in_recovery"},
                                     "from_timestamp": None, "to_timestamp": None},
                        existing_series={}, mode=mode)
        existing_collections = self.existing_contract.get("collections", {})
        existing_collections = existing_collections if isinstance(existing_collections, Mapping) else {}
        collections: dict[str, Any] = _copy_normalizing_negative_zero(dict(existing_collections)) if mode in {"incremental", "recovery"} else {}
        if "miner_entities" in raw["raw"]:
            collections["miner_entities"] = preprocess_miner_entities(
                raw["raw"]["miner_entities"], existing_collections.get("miner_entities", {}))
        if "miner_outflow_by_pool" in raw["raw"]:
            collections["miner_outflow_by_pool"] = preprocess_miner_outflow_by_pool(
                raw["raw"]["miner_outflow_by_pool"], existing_collections.get("miner_outflow_by_pool", {}))
        if mode == "recovery" and include_screen_extensions:
            if "miner_entities" not in collections:
                collections["miner_entities"] = preprocess_miner_entities(
                    {"status": "error", "error": {"message": "not_requested_in_recovery"}}, {})
            if "miner_outflow_by_pool" not in collections:
                collections["miner_outflow_by_pool"] = preprocess_miner_outflow_by_pool(
                    {"status": "error", "entity_symbols": [], "requests": [], "fanout_skipped_no_symbols": True}, {})
        generated_at = datetime.fromtimestamp(execution_timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
        return {"family": ON_CHAIN_MINERS_FAMILY, "stage": "input", "mode": mode,
                "context": {"asset": self.raw_extractor.asset, "data_mode": self.raw_extractor.data_mode, "is_demo": self.raw_extractor.is_demo,
                            "reference_timestamp": reference_timestamp, "execution_timestamp": execution_timestamp,
                            "generated_at": generated_at, "include_enrichment": bool(include_enrichment),
                            "include_screen_extensions": bool(include_screen_extensions)},
                "series": series, "collections": collections,
                "quality": evaluate_on_chain_miners_quality(series, include_enrichment, collections, include_screen_extensions)}


def run_on_chain_miners_input(*, fetcher: OnChainMinersFetcher, reference_timestamp: int, asset: str = "BTC",
                              existing_contract: Mapping[str, Any] | None = None, requested_mode: str | None = None,
                              recovery_requests: Sequence[Mapping[str, Any]] | None = None, data_mode: str = "live", is_demo: bool = False,
                              include_enrichment: bool = False, include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS,
                              execution_timestamp: int | None = None) -> dict[str, Any]:
    execution_timestamp = normalize_unix_timestamp(int(time.time()) if execution_timestamp is None else execution_timestamp)
    extractor    = OnChainMinersRawExtractor(fetcher=fetcher, asset=asset, data_mode=data_mode, is_demo=is_demo)
    preprocessor = OnChainMinersInputPreprocessor(raw_extractor=extractor, existing_contract=existing_contract)
    return preprocessor.run(requested_mode=requested_mode, recovery_requests=recovery_requests, reference_timestamp=reference_timestamp,
                            include_enrichment=include_enrichment, include_screen_extensions=include_screen_extensions,
                            execution_timestamp=execution_timestamp)
