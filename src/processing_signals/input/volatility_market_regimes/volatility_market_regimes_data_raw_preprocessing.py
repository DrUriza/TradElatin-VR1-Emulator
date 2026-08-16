"""Normalization and persistence merge for Volatility Market Regimes Input v0.1."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .volatility_market_regimes_data_raw_extract import (
    BASE_INTERVAL, BOOTSTRAP_HISTORY_DAYS, COINGLASS_POSITIONING_ENDPOINT_ID, COINGLASS_PROVIDER,
    ENDPOINT_MANIFEST, GLASSNODE_PROVIDER,
    GLASSNODE_REALIZED_VOL_ENDPOINT_ID, GLASSNODE_DVOL_ENDPOINT_ID, INTERVAL_SECONDS, VALID_MODES, VOLATILITY_MARKET_REGIMES_FAMILY,
)

PERCENT_SUM_TOLERANCE = 0.25
RATIO_TOLERANCE       = 0.02
STALE_TOLERANCE       = 2 * INTERVAL_SECONDS
DATASETS = {
    (COINGLASS_PROVIDER, COINGLASS_POSITIONING_ENDPOINT_ID): ("coinglass", "top_position_ratio"),
    (GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID): ("glassnode", "realized_volatility"),
    (GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID): ("glassnode", "dvol"),
}
DATASET_IDS = tuple(f"{provider}.{name}" for provider, name in DATASETS.values())


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _finite(value: Any, name: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid_{name}")
    result = float(value)
    if non_negative and result < 0:
        raise ValueError(f"negative_{name}")
    return 0.0 if result == 0 else result


def _timestamp(value: Any, name: str, *, milliseconds: bool = False) -> int:
    numeric = _finite(value, name, non_negative=True)
    result = int(numeric)
    return result // 1000 if milliseconds else result


def determine_volatility_market_regimes_input_mode(*, existing_contract: Mapping[str, Any] | None = None,
                                                    recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                                    requested_mode: str | None = None) -> str:
    if requested_mode is not None:
        if requested_mode not in VALID_MODES:
            raise ValueError("unsupported_mode")
        if requested_mode == "recovery" and not recovery_requests:
            raise ValueError("recovery_requests_required")
        return requested_mode
    if recovery_requests:
        return "recovery"
    providers = existing_contract.get("providers", {}) if isinstance(existing_contract, Mapping) else {}
    for provider, name in DATASETS.values():
        payload = providers.get(provider, {}).get(name, {}) if isinstance(providers, Mapping) else {}
        if not isinstance(payload, Mapping) or not payload.get("records"):
            return "bootstrap"
    return "incremental"


def unwrap_coinglass_positioning_response(response: Any) -> list[Any]:
    if not isinstance(response, Mapping):
        raise ValueError("invalid_envelope")
    if response.get("code") not in {"0", 0, "200", 200}:
        raise ValueError("provider_error")
    data = response.get("data")
    if not _sequence(data):
        raise ValueError("invalid_envelope")
    return list(data)


def unwrap_glassnode_realized_volatility_response(response: Any) -> list[Any]:
    if not _sequence(response):
        raise ValueError("invalid_envelope")
    return list(response)


def normalize_coinglass_positioning_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_record")
    timestamp = _timestamp(record.get("time"), "timestamp", milliseconds=True)
    long = _finite(record.get("top_position_long_percent"), "long_percent", non_negative=True)
    short = _finite(record.get("top_position_short_percent"), "short_percent", non_negative=True)
    ratio = _finite(record.get("top_position_long_short_ratio"), "long_short_ratio")
    if long > 100 or short > 100 or ratio <= 0 or abs(long + short - 100) > PERCENT_SUM_TOLERANCE:
        raise ValueError("inconsistent_positioning_record")
    if short == 0 or abs(ratio - long / short) > RATIO_TOLERANCE:
        raise ValueError("inconsistent_positioning_ratio")
    return {"timestamp": timestamp, "long_percent": long, "short_percent": short, "long_short_ratio": ratio}


def normalize_glassnode_realized_volatility_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_record")
    value = _finite(record.get("v"), "realized_volatility", non_negative=True)
    return {"timestamp": _timestamp(record.get("t"), "timestamp"), "value_native": value,
        "value_percent": 0.0 if value == 0 else value * 100}




def normalize_glassnode_dvol_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("invalid_record")
    timestamp = _timestamp(record.get("t"), "timestamp")
    # Glassnode DVOL is an OHLC metric. Accept both flat OHLC fields and a nested v payload.
    payload = record.get("v") if isinstance(record.get("v"), Mapping) else record
    open_value = _finite(payload.get("o"), "dvol_open", non_negative=True)
    high_value = _finite(payload.get("h"), "dvol_high", non_negative=True)
    low_value = _finite(payload.get("l"), "dvol_low", non_negative=True)
    close_value = _finite(payload.get("c"), "dvol_close", non_negative=True)
    if high_value < max(open_value, close_value) or low_value > min(open_value, close_value):
        raise ValueError("invalid_dvol_ohlc")
    return {"timestamp": timestamp, "open": open_value, "high": high_value,
            "low": low_value, "close": close_value}


def upsert_timestamp_records(existing_records: Sequence[Mapping[str, Any]],
                             incoming_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    values = {int(record["timestamp"]): copy.deepcopy(dict(record)) for record in existing_records}
    values.update({int(record["timestamp"]): copy.deepcopy(dict(record)) for record in incoming_records})
    return [values[timestamp] for timestamp in sorted(values)]


def detect_hourly_gaps(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ranges = []
    for previous, current in zip(records, records[1:]):
        difference = int(current["timestamp"]) - int(previous["timestamp"])
        if difference > INTERVAL_SECONDS:
            ranges.append({"after_timestamp": int(previous["timestamp"]), "before_timestamp": int(current["timestamp"]),
                "missing_intervals": difference // INTERVAL_SECONDS - 1})
    return {"gap_count": len(ranges), "gap_ranges": ranges}


def _previous(existing: Mapping[str, Any] | None, provider: str, name: str) -> Mapping[str, Any]:
    payload = existing.get("providers", {}).get(provider, {}).get(name, {}) if isinstance(existing, Mapping) else {}
    return payload if isinstance(payload, Mapping) else {}


def _dataset(*, requests: Sequence[Mapping[str, Any]], existing: Mapping[str, Any], provider: str, endpoint_id: str,
             reference_timestamp: int, execution_timestamp: int) -> dict[str, Any]:
    normalizer = {
        (COINGLASS_PROVIDER, COINGLASS_POSITIONING_ENDPOINT_ID): normalize_coinglass_positioning_record,
        (GLASSNODE_PROVIDER, GLASSNODE_REALIZED_VOL_ENDPOINT_ID): normalize_glassnode_realized_volatility_record,
        (GLASSNODE_PROVIDER, GLASSNODE_DVOL_ENDPOINT_ID): normalize_glassnode_dvol_record,
    }[(provider, endpoint_id)]
    unwrapper = {
        COINGLASS_PROVIDER: unwrap_coinglass_positioning_response,
        GLASSNODE_PROVIDER: unwrap_glassnode_realized_volatility_response,
    }[provider]
    incoming_by_timestamp, invalid_count, envelope_invalid = {}, 0, False
    successful, failed, empty = 0, 0, 0
    warnings, errors = [], []
    for request in requests:
        if request.get("status") != "ok":
            failed += 1
            warnings.append(f"{request.get('request_id')}:request_failed")
            continue
        try:
            raw_records = unwrapper(request.get("response"))
            successful += 1
            if not raw_records:
                empty += 1
            for raw_record in raw_records:
                try:
                    record = normalizer(raw_record)
                    incoming_by_timestamp[record["timestamp"]] = record
                except (TypeError, ValueError):
                    invalid_count += 1
        except (TypeError, ValueError):
            envelope_invalid = True
            errors.append(f"{request.get('request_id')}:invalid_envelope")
    incoming = [incoming_by_timestamp[key] for key in sorted(incoming_by_timestamp)]
    previous_records = copy.deepcopy(existing.get("records", [])) if isinstance(existing.get("records", []), list) else []
    records = upsert_timestamp_records(previous_records, incoming)
    gaps = detect_hourly_gaps(records)
    reason = None
    if not records:
        if envelope_invalid:
            status, reason = "invalid", "invalid_envelope"
        elif invalid_count:
            status, reason = "invalid", "all_records_invalid"
        elif failed and not successful:
            status, reason = "unavailable", "request_failed"
        else:
            status, reason = "unavailable", "empty_response"
    elif envelope_invalid:
        status, reason = "partial", "latest_attempt_invalid"
    elif failed:
        status, reason = "partial", "latest_refresh_failed"
    elif invalid_count:
        status, reason = "partial", "some_records_invalid"
    elif gaps["gap_count"]:
        status, reason = "partial", "gaps_detected"
    elif records[-1]["timestamp"] < reference_timestamp - STALE_TOLERANCE:
        status, reason = "partial", "stale_latest_record"
    else:
        status = "available"
    request_ids = [str(item.get("request_id")) for item in requests]
    latest_attempt = {"request_ids": request_ids, "successful": successful, "failed": failed,
        "invalid_envelopes": int(envelope_invalid), "invalid_records": invalid_count, "empty_responses": empty}
    return {"status": status, "reason": reason, "interval": BASE_INTERVAL, "interval_seconds": INTERVAL_SECONDS,
        "records": records, "incoming_records": incoming, "records_available": len(records),
        "first_available_timestamp": records[0]["timestamp"] if records else None,
        "last_available_timestamp": records[-1]["timestamp"] if records else None, **gaps,
        "source_data_as_of": records[-1]["timestamp"] if records else None,
        "latest_attempt": latest_attempt, "provenance": {"provider": provider, "endpoint_id": endpoint_id,
            "path": ENDPOINT_MANIFEST[(provider, endpoint_id)], "params": [copy.deepcopy(item.get("params", {})) for item in requests],
            "request_ids": request_ids, "reference_timestamp": reference_timestamp, "execution_timestamp": execution_timestamp},
        "warnings": sorted(set(warnings)), "errors": sorted(set(errors))}


def evaluate_volatility_market_regimes_input_quality(providers: Mapping[str, Any], *, mode: str,
                                                     required_datasets: Sequence[str] = DATASET_IDS) -> dict[str, Any]:
    statuses = {identifier: providers[provider][name]["status"] for (provider, name), identifier in
        zip(DATASETS.values(), DATASET_IDS, strict=True)}
    required = list(required_datasets)
    missing = [item for item in required if statuses[item] == "unavailable"]
    partial = [item for item in required if statuses[item] == "partial"]
    invalid = [item for item in required if statuses[item] == "invalid"]
    if invalid or missing:
        status = "invalid" if mode == "bootstrap" or invalid else "partial"
    else:
        status = "partial" if partial else "ok"
    warnings = sorted([f"{item}:{statuses[item]}" for item in required if statuses[item] in {"partial", "unavailable"}])
    errors = sorted([f"{item}:invalid" for item in invalid])
    recovery_required = any(statuses[item] != "available" or providers[item.split(".")[0]][item.split(".")[1]]["gap_count"] for item in required)
    return {"status": status, "required_datasets": required, "missing_required_datasets": missing,
        "partial_datasets": partial, "invalid_datasets": invalid, "recovery_required": recovery_required,
        "warnings": warnings, "errors": errors}


class VolatilityMarketRegimesInputPreprocessor:
    def __init__(self, *, existing_contract: Mapping[str, Any] | None = None) -> None:
        self.existing_contract = copy.deepcopy(existing_contract)

    def run(self, raw_bundle: Mapping[str, Any]) -> dict[str, Any]:
        if (not isinstance(raw_bundle, Mapping) or raw_bundle.get("family") != VOLATILITY_MARKET_REGIMES_FAMILY
                or raw_bundle.get("stage") != "extracted_raw" or raw_bundle.get("mode") not in VALID_MODES
                or not isinstance(raw_bundle.get("requests"), list)):
            raise ValueError("invalid_raw_bundle")
        mode, reference, execution = raw_bundle["mode"], raw_bundle.get("reference_timestamp"), raw_bundle.get("execution_timestamp")
        if type(reference) is not int or type(execution) is not int:
            raise ValueError("invalid_raw_timestamps")
        grouped = {key: [] for key in DATASETS}
        for request in raw_bundle["requests"]:
            if not isinstance(request, Mapping) or (request.get("provider"), request.get("endpoint_id")) not in DATASETS:
                raise ValueError("invalid_raw_request")
            grouped[(request["provider"], request["endpoint_id"])].append(request)
        providers = {provider: {} for provider, _ in DATASETS.values()}
        targeted = []
        for key, (provider, name) in DATASETS.items():
            requests = grouped[key]
            previous = _previous(self.existing_contract, provider, name)
            if mode == "recovery" and not requests:
                providers[provider][name] = copy.deepcopy(previous) if previous else _dataset(requests=[], existing={},
                    provider=key[0], endpoint_id=key[1], reference_timestamp=reference, execution_timestamp=execution)
            else:
                providers[provider][name] = _dataset(requests=requests, existing=previous, provider=key[0], endpoint_id=key[1],
                    reference_timestamp=reference, execution_timestamp=execution)
                if requests:
                    targeted.append(f"{provider}.{name}")
        required = targeted if mode == "recovery" else list(DATASET_IDS)
        quality = evaluate_volatility_market_regimes_input_quality(providers, mode=mode, required_datasets=required)
        return {"schema": {"id": "trad_elatin.volatility_market_regimes.input.v1", "version": "1.0.0"},
            "family": VOLATILITY_MARKET_REGIMES_FAMILY, "stage": "input", "mode": mode,
            "reference_timestamp": reference, "execution_timestamp": execution,
            "dimensions": {"asset": "BTC", "symbol": "BTCUSDT", "exchange": "Binance", "interval": BASE_INTERVAL},
            "providers": providers, "quality": quality}


def preprocess_volatility_market_regimes_input(raw_bundle: Mapping[str, Any], *,
                                               existing_contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return VolatilityMarketRegimesInputPreprocessor(existing_contract=existing_contract).run(raw_bundle)


def run_volatility_market_regimes_input(*, fetcher, reference_timestamp: int,
                                        existing_contract: Mapping[str, Any] | None = None,
                                        requested_mode: str | None = None,
                                        recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                        clock=None, bootstrap_history_days: int = BOOTSTRAP_HISTORY_DAYS,
                                        incremental_hours: int = 12) -> dict[str, Any]:
    """Public Input facade aligned with the other seven families."""
    from .volatility_market_regimes_data_raw_extract import VolatilityMarketRegimesRawExtractor
    mode = determine_volatility_market_regimes_input_mode(
        existing_contract=existing_contract,
        recovery_requests=recovery_requests,
        requested_mode=requested_mode,
    )
    raw = VolatilityMarketRegimesRawExtractor(fetcher, clock=clock).run(
        mode=mode,
        reference_timestamp=reference_timestamp,
        recovery_requests=recovery_requests,
        bootstrap_history_days=bootstrap_history_days,
        incremental_hours=incremental_hours,
    )
    return VolatilityMarketRegimesInputPreprocessor(existing_contract=existing_contract).run(raw)
