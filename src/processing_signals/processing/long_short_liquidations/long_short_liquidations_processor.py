"""Validated orchestration for liquidation Processing v0.1."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
from typing import Any

from processing_signals.processing.math.native_analysis import rolling_zscore, rolling_percentile, difference, rolling_wasserstein, pct_change as native_pct_change, latest

from .long_short_liquidations_feature_builder import (
    EVENT_INTENSITY_MIN_COMPLETE_BINS, EVENT_WINDOWS_SECONDS, MAP_BUCKET_WIDTH_BPS,
    MAP_CENTRAL_TOLERANCE_BPS, MAP_INTERPOLATION_ENABLED, MAP_PROXIMITY_DECAY_BPS,
    PRESSURE_MIN_AVAILABLE_WEIGHT, REALIZED_WINDOWS_SECONDS, aggregate_regular_window, window_end_for_hourly,
    build_event_intensity, build_event_window, build_exchange_distribution, build_map_features,
    build_pressure_score, confirmation, empirical_percentile, variation,
)

REFERENCE_PRICE_MAX_AGE_SECONDS = 120
VALID_DATASET_STATES = {"available", "partial", "unavailable", "invalid"}
VALID_INPUT_QUALITY_STATES = VALID_DATASET_STATES | {"ok"}
PROCESSING_REQUIRED_FEATURES = ["realized.series", "realized.windows.1h", "realized.windows.4h", "realized.windows.12h",
                                "realized.windows.24h", "exchange_distribution", "events.aggregate.24h", "maps.aggregated.base"]
PROCESSING_OPTIONAL_FEATURES = ["realized.confirmations", "exchange_histories", "events.short_windows", "maps.by_exchange",
                                "maps.aligned_exchanges", "maps.max_pain", "maps.spatial", "pressure"]


def _json_safe(value: Any, path: str = "input") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError(f"invalid_input_contract:{path}:non_finite_number")
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _json_safe(item, f"{path}[{index}]" if path else f"[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"invalid_input_contract:{path}:non_string_key")
            _json_safe(item, f"{path}.{key}" if path else key)
        return
    raise ValueError(f"invalid_input_contract:{path}:not_json_safe")


def _messages(value: Any, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"invalid_input_contract:{path}")


def _dataset(value: Any, path: str, collection: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid_input_contract:{path}")
    status = value.get("status")
    if status not in VALID_DATASET_STATES:
        raise ValueError(f"invalid_input_contract:{path}.status")
    if status != "available" and (not isinstance(value.get("reason"), str) or not value["reason"]):
        raise ValueError(f"invalid_input_contract:{path}.reason")
    if not isinstance(value.get(collection, []), list):
        raise ValueError(f"invalid_input_contract:{path}.{collection}")
    if not isinstance(value.get("provenance", {}), Mapping):
        raise ValueError(f"invalid_input_contract:{path}.provenance")
    _messages(value.get("warnings", []), f"{path}.warnings")
    _messages(value.get("errors", []), f"{path}.errors")
    return value


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid_input_contract:{path}")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"invalid_input_contract:{path}")
    return float(value)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid_input_contract:{path}")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"invalid_input_contract:{path}")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid_input_contract:{path}")
    return value


def _require_timestamp(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid_input_contract:{path}")
    return value


def _optional_finite(value: Any, path: str, *, nonnegative: bool = False) -> float | None:
    if value is None:
        return None
    return _finite(value, nonnegative=nonnegative, path=path)


def _validate_records(records: Sequence[Any], path: str, fields: Sequence[str], *, interval: int | None = None) -> None:
    seen: dict[int, tuple[Any, ...]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid_input_contract:{path}[{index}]")
        timestamp = record.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
            raise ValueError(f"invalid_input_contract:{path}[{index}].timestamp")
        values = tuple(record.get(field) for field in fields)
        if timestamp in seen and seen[timestamp] != values:
            raise ValueError(f"invalid_input_contract:{path}:incompatible_duplicate_timestamp")
        seen[timestamp] = values
        for field in fields:
            _finite(record.get(field), nonnegative=True, path=f"{path}[{index}].{field}")
        if interval is not None and (isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0):
            raise ValueError(f"invalid_input_contract:{path}.interval_seconds")


def _validate_exchange_snapshot(records: list[Any], path: str) -> None:
    for index, value in enumerate(records):
        record_path = f"{path}[{index}]"
        record = _require_mapping(value, record_path)
        _require_string(record.get("exchange"), f"{record_path}.exchange")
        _require_string(record.get("exchange_key"), f"{record_path}.exchange_key")
        for field in ("liquidation_usd", "long_liquidation_usd", "short_liquidation_usd"):
            _finite(record.get(field), nonnegative=True, path=f"{record_path}.{field}")


def _validate_events(records: list[Any], path: str) -> None:
    seen: dict[str, tuple[Any, ...]] = {}
    for index, value in enumerate(records):
        record_path = f"{path}[{index}]"
        record = _require_mapping(value, record_path)
        _require_timestamp(record.get("timestamp"), f"{record_path}.timestamp")
        event_id = _require_string(record.get("event_id"), f"{record_path}.event_id")
        for field in ("exchange", "symbol", "base_asset", "order_side"):
            _require_string(record.get(field), f"{record_path}.{field}")
        _finite(record.get("price"), positive=True, path=f"{record_path}.price")
        _finite(record.get("usd_value"), nonnegative=True, path=f"{record_path}.usd_value")
        raw_side = record.get("raw_side")
        if isinstance(raw_side, bool) or not isinstance(raw_side, int) or raw_side not in {1, 2}:
            raise ValueError(f"invalid_input_contract:{record_path}.raw_side")
        signature = (record["timestamp"], record["usd_value"], record["price"])
        if event_id in seen and seen[event_id] != signature:
            raise ValueError(f"invalid_input_contract:{path}:incompatible_duplicate_event_id")
        seen[event_id] = signature


def _validate_map_levels(levels: list[Any], path: str) -> None:
    for index, value in enumerate(levels):
        level_path = f"{path}[{index}]"
        level = _require_mapping(value, level_path)
        _finite(level.get("price_level"), positive=True, path=f"{level_path}.price_level")
        _finite(level.get("provider_liquidation_level"), nonnegative=True, path=f"{level_path}.provider_liquidation_level")
        _optional_finite(level.get("leverage_ratio"), f"{level_path}.leverage_ratio", nonnegative=True)
        if "raw_price_key" in level:
            _require_string(level["raw_price_key"], f"{level_path}.raw_price_key")


def _validate_max_pain(records: list[Any], path: str) -> None:
    for index, value in enumerate(records):
        record_path = f"{path}[{index}]"
        record = _require_mapping(value, record_path)
        for field in ("provider_price", "long_max_pain_liquidation_price", "short_max_pain_liquidation_price"):
            _finite(record.get(field), positive=True, path=f"{record_path}.{field}")
        for field in ("long_max_pain_liquidation_level", "short_max_pain_liquidation_level"):
            _finite(record.get(field), nonnegative=True, path=f"{record_path}.{field}")


def _validate_confirmation_dataset(value: Any, path: str, *, glassnode: bool = False) -> None:
    dataset = _dataset(value, path, "records")
    if dataset["status"] not in {"available", "partial"}:
        return
    if "interval" in dataset:
        _require_string(dataset["interval"], f"{path}.interval")
    if glassnode:
        _require_string(dataset.get("unit"), f"{path}.unit")
    for index, value_record in enumerate(dataset["records"]):
        record_path = f"{path}.records[{index}]"
        record = _require_mapping(value_record, record_path)
        _require_timestamp(record.get("timestamp"), f"{record_path}.timestamp")
        fields = ("value",) if glassnode else ("long_liquidations_usd", "short_liquidations_usd")
        for field in fields:
            _optional_finite(record.get(field), f"{record_path}.{field}", nonnegative=True)


def validate_long_short_liquidations_input(input_contract: Any) -> None:
    _json_safe(input_contract, "")
    if not isinstance(input_contract, Mapping):
        raise ValueError("invalid_input_contract:mapping_required")
    if input_contract.get("family") != "long_short_liquidations":
        raise ValueError("invalid_input_contract:family")
    if input_contract.get("stage") != "input":
        raise ValueError("invalid_input_contract:stage")
    reference = input_contract.get("reference_timestamp")
    if isinstance(reference, bool) or not isinstance(reference, int) or reference <= 0:
        raise ValueError("invalid_input_contract:reference_timestamp")
    providers = input_contract.get("providers")
    if not isinstance(providers, Mapping) or not isinstance(providers.get("coinglass"), Mapping):
        raise ValueError("invalid_input_contract:providers.coinglass")
    quality = input_contract.get("quality")
    if not isinstance(quality, Mapping) or quality.get("status") not in VALID_INPUT_QUALITY_STATES:
        raise ValueError("invalid_input_contract:quality.status")
    _messages(quality.get("warnings", []), "quality.warnings")
    _messages(quality.get("errors", []), "quality.errors")
    cg = providers["coinglass"]
    definitions = (("aggregated_history", "records"), ("exchange_snapshot", "records"), ("aggregated_map", "levels"), ("max_pain", "records"))
    for name, collection in definitions:
        dataset = _dataset(cg.get(name), f"providers.coinglass.{name}", collection)
        if dataset["status"] in {"available", "partial"}:
            path = f"providers.coinglass.{name}.{collection}"
            if name == "aggregated_history":
                _validate_records(dataset[collection], path, ("long_liquidation_usd", "short_liquidation_usd"), interval=3600)
            elif name == "exchange_snapshot":
                _validate_exchange_snapshot(dataset[collection], path)
            elif name == "aggregated_map":
                _validate_map_levels(dataset[collection], path)
            else:
                _validate_max_pain(dataset[collection], path)
    for name, collection in (("pair_history", "records"), ("events", "records"), ("pair_maps", "levels")):
        group = cg.get(name, {})
        if not isinstance(group, Mapping):
            raise ValueError(f"invalid_input_contract:providers.coinglass.{name}")
        for key, value in group.items():
            _require_string(key, f"providers.coinglass.{name}:exchange_key")
            path = f"providers.coinglass.{name}.{key}"
            dataset = _dataset(value, path, collection)
            if dataset["status"] not in {"available", "partial"}:
                continue
            if name == "pair_history":
                _validate_records(dataset[collection], f"{path}.records", ("long_liquidation_usd", "short_liquidation_usd"), interval=3600)
            elif name == "events":
                _validate_events(dataset[collection], f"{path}.records")
            else:
                _validate_map_levels(dataset[collection], f"{path}.levels")
    cryptoquant = providers.get("cryptoquant")
    if cryptoquant is not None:
        cryptoquant = _require_mapping(cryptoquant, "providers.cryptoquant")
        _validate_confirmation_dataset(cryptoquant.get("aggregate_history"), "providers.cryptoquant.aggregate_history")
    glassnode = providers.get("glassnode")
    if glassnode is not None:
        glassnode = _require_mapping(glassnode, "providers.glassnode")
        for name in ("long_liquidations", "short_liquidations", "total_liquidations", "long_liquidation_dominance"):
            _validate_confirmation_dataset(glassnode.get(name), f"providers.glassnode.{name}", glassnode=True)


def validate_reference_price_context(context: Mapping[str, Any] | None, snapshot_observed_at: int | None) -> tuple[float | None, dict[str, Any]]:
    if context is None:
        return None, {"status": "unavailable", "reason": "missing_reference_price"}
    try:
        _json_safe(context, "reference_price_context")
    except ValueError:
        return None, {"status": "unavailable", "reason": "invalid_reference_price_context"}
    required = {"source_family": "prices_ohlcv", "source_market": "spot", "source_timeframe": "1m",
                "price_field": "close", "is_closed_bar": True}
    if not isinstance(context, Mapping) or any(context.get(key) != value for key, value in required.items()):
        return None, {"status": "unavailable", "reason": "invalid_reference_price_context"}
    value, timestamp = context.get("value"), context.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None, {"status": "unavailable", "reason": "invalid_reference_price_context"}
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0 or not isinstance(snapshot_observed_at, int):
        return None, {"status": "unavailable", "reason": "invalid_reference_price_context"}
    if timestamp > snapshot_observed_at:
        return None, {"status": "unavailable", "reason": "future_reference_price"}
    if snapshot_observed_at - timestamp > REFERENCE_PRICE_MAX_AGE_SECONDS:
        return None, {"status": "unavailable", "reason": "stale_reference_price"}
    return float(value), {"status": "available", "reason": None, **deepcopy(dict(context)), "max_age_seconds": REFERENCE_PRICE_MAX_AGE_SECONDS}


def _usable(dataset: Mapping[str, Any], collection: str) -> list[Any]:
    return deepcopy(dataset.get(collection, [])) if dataset.get("status") in {"available", "partial"} else []


def _dataset_group_status(payload: Any) -> str:
    if isinstance(payload, Mapping) and payload.get("status") in VALID_DATASET_STATES:
        return str(payload["status"])
    if isinstance(payload, Mapping):
        statuses = [str(item.get("status")) for item in payload.values()
                    if isinstance(item, Mapping) and item.get("status") in VALID_DATASET_STATES]
        if statuses:
            usable = [status for status in statuses if status in {"available", "partial"}]
            if not usable:
                return "invalid" if "invalid" in statuses else "unavailable"
            if len(usable) == len(statuses) and all(status == "available" for status in statuses):
                return "available"
            return "partial"
    return "unavailable"


def _source_selection(providers: Mapping[str, Any]) -> dict[str, Any]:
    definitions = {
        "realized_aggregate": ("coinglass", "aggregated_history", "canonical"), "realized_by_exchange": ("coinglass", "pair_history", "canonical"),
        "exchange_distribution": ("coinglass", "exchange_snapshot", "snapshot"), "events": ("coinglass", "events", "canonical"),
        "aggregated_map": ("coinglass", "aggregated_map", "snapshot"), "exchange_maps": ("coinglass", "pair_maps", "optional"),
        "max_pain": ("coinglass", "max_pain", "optional"), "cryptoquant_confirmation": ("cryptoquant", "aggregate_history", "confirmation"),
        "glassnode_long_confirmation": ("glassnode", "long_liquidations", "confirmation"),
        "glassnode_short_confirmation": ("glassnode", "short_liquidations", "confirmation"),
        "glassnode_total_confirmation": ("glassnode", "total_liquidations", "confirmation"),
        "glassnode_dominance_reference": ("glassnode", "long_liquidation_dominance", "optional")}
    output = {}
    for name, (provider, dataset, role) in definitions.items():
        parent = providers.get(provider, {}) if isinstance(providers.get(provider, {}), Mapping) else {}
        payload = parent.get(dataset, {}) if isinstance(parent, Mapping) else {}
        status = _dataset_group_status(payload)
        output[name] = {"provider": provider, "dataset_path": f"{provider}.{dataset}", "status": status,
                        "selected": status in {"available", "partial"}, "role": role, "fallback_applied": False}
    return output


def _events_coverage_complete(dataset: Mapping[str, Any], start: int, end: int) -> bool:
    if dataset.get("status") != "available" or "event_endpoint_record_limit_reached" in dataset.get("warnings", []):
        return False
    provenance = dataset.get("provenance", {})
    intervals = [item for item in provenance.get("coverage_intervals", [])
                 if isinstance(item, Mapping) and item.get("status") == "complete"]
    if intervals:
        ranges = sorted((int(item["start"]), int(item["end"])) for item in intervals)
        cursor = start
        for left, right in ranges:
            if right <= cursor:
                continue
            if left > cursor:
                break
            cursor = max(cursor, right)
            if cursor >= end:
                return True
        return False
    params = provenance.get("params", {})
    items = params if isinstance(params, list) else [params]
    ranges = []
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get("start_time"), (int, float)) and isinstance(item.get("end_time"), (int, float)):
            ranges.append((int(item["start_time"] / 1000 if item["start_time"] > 10**11 else item["start_time"]),
                           int(item["end_time"] / 1000 if item["end_time"] > 10**11 else item["end_time"])))
    return any(left <= start and right >= end for left, right in ranges)


def _max_pain(dataset: Mapping[str, Any], reference: float | None) -> dict[str, Any]:
    records = _usable(dataset, "records")
    if not records:
        return {"status": "invalid" if dataset.get("status") == "invalid" else "unavailable", "reason": dataset.get("reason", "max_pain_unavailable")}
    source = records[0]
    output = {"status": dataset["status"], "reason": dataset.get("reason"), "provider_price": source["provider_price"],
              "long_max_pain_price": source["long_max_pain_liquidation_price"],
              "long_max_pain_level": source["long_max_pain_liquidation_level"],
              "short_max_pain_price": source["short_max_pain_liquidation_price"],
              "short_max_pain_level": source["short_max_pain_liquidation_level"],
              "provenance": {"provider": "coinglass", "source_dataset": "coinglass.max_pain",
                             "source_field_mapping": {"long_max_pain_price": "long_max_pain_liquidation_price",
                                                      "short_max_pain_price": "short_max_pain_liquidation_price"}}}
    for side in ("long", "short"):
        output[f"{side}_distance_bps"] = None if reference is None else (output[f"{side}_max_pain_price"] / reference - 1) * 10000
    output["provider_price_difference_bps"] = None if reference is None else (output["provider_price"] / reference - 1) * 10000
    return output


def _invalid_output(reference_timestamp: int, config: Mapping[str, Any] | None, errors: list[str]) -> dict[str, Any]:
    return {"family": "long_short_liquidations", "stage": "processing", "reference_timestamp": reference_timestamp,
            "configuration": {"version": "0.1", **deepcopy(dict(config or {}))}, "source_selection": {},
            "realized": {"series": [], "windows": {}, "variations": {}, "confirmations": {}, "provenance": {}},
            "exchange_distribution": {"status": "invalid", "reason": "input_quality_invalid"}, "exchange_histories": {},
            "events": {"aggregate": {}, "by_exchange": {}, "provenance": {}},
            "maps": {"reference_price": {}, "aggregated": {}, "by_exchange": {}, "aligned_exchanges": {}, "max_pain": {}},
            "pressure": {"status": "unavailable", "reason": "input_quality_invalid"},
            "quality": {"status": "invalid", "required_features": PROCESSING_REQUIRED_FEATURES,
                        "optional_features": PROCESSING_OPTIONAL_FEATURES, "missing_features": [], "invalid_features": ["input.quality"],
                        "partial_features": [], "unavailable_features": [], "warnings": [], "errors": errors}}



def _native_liquidation_analysis(
    realized_series: Sequence[Mapping[str, Any]], *,
    price_history: Sequence[Mapping[str, Any]] | None = None,
    positioning_history: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = list(realized_series)
    timestamps = [int(row["timestamp"]) for row in rows]
    long_values = [row.get("long_liquidation_usd") for row in rows]
    short_values = [row.get("short_liquidation_usd") for row in rows]
    totals = [row.get("total_liquidation_usd") for row in rows]
    total_musd = [None if v is None else float(v) / 1_000_000.0 for v in totals]
    intensity_z = rolling_zscore(totals, 48, 24)
    intensity_pct = rolling_percentile(totals, 168, 24)
    imbalance: list[float | None] = []
    for lv, sv in zip(long_values, short_values, strict=True):
        if lv is None or sv is None or float(lv) + float(sv) == 0:
            imbalance.append(None)
        else:
            imbalance.append((float(lv) - float(sv)) / (float(lv) + float(sv)))
    cascade = difference(totals)
    cascade_z = rolling_zscore(cascade, 48, 24)

    price_lookup = {int(row["timestamp"]): row.get("close") for row in (price_history or []) if isinstance(row, Mapping) and row.get("timestamp") is not None}
    price_values = [price_lookup.get(ts) for ts in timestamps]
    price_return_pct = native_pct_change(price_values, 1, 100.0)
    price_z = rolling_zscore(price_return_pct, 48, 24)
    price_regime = [None if p is None or i is None or z is None else float(p) * 0.35 - float(i) * abs(float(z)) for p,i,z in zip(price_z,imbalance,intensity_z,strict=True)]

    pos_lookup = {int(row["timestamp"]): row.get("long_short_ratio") for row in (positioning_history or []) if isinstance(row, Mapping) and row.get("timestamp") is not None}
    top_position = [pos_lookup.get(ts) for ts in timestamps]
    crowding_raw = [None if value is None or float(value) <= 0 else math.log(float(value)) for value in top_position]
    crowding_score = rolling_zscore(crowding_raw, 48, 24)
    pressure_score = [None if z is None or imb is None else float(z) * float(imb) for z,imb in zip(intensity_z,imbalance,strict=True)]
    combined = [None if c is None and p is None else float((c or 0.0) + (p or 0.0)) / 2.0 for c,p in zip(crowding_score,pressure_score,strict=True)]
    regime_score = [None if z is None or imb is None or cas is None else float(z) * 0.45 + float(imb) * 0.35 + float(cas) * 0.20 for z,imb,cas in zip(intensity_z,imbalance,cascade_z,strict=True)]
    wasserstein = rolling_wasserstein(totals, 24, 168)

    return {
        "status":"available" if rows else "unavailable", "timestamps":timestamps,
        "positioning": {"top_position_ratio":top_position, "top_account_ratio":[None]*len(timestamps), "global_account_ratio":[None]*len(timestamps)},
        "indicators": {
            "liquidation_intensity_zscore": {"intensity_zscore":intensity_z,"intensity_percentile":intensity_pct,"total_liquidations_musd":total_musd},
            "long_short_liquidation_imbalance": {"liquidation_imbalance":imbalance,"long_liquidations_musd":[None if v is None else float(v)/1e6 for v in long_values],"short_liquidations_musd":[None if v is None else float(v)/1e6 for v in short_values]},
            "cascade_acceleration": {"cascade_acceleration":cascade,"cascade_zscore":cascade_z,"total_liquidations_musd":total_musd},
            "price_liquidation_regime": {"price_return_pct":price_return_pct,"price_liquidation_regime_score":price_regime},
            "crowding_liquidation_pressure": {"top_position_ratio":top_position,"crowding_score":crowding_score,"liquidation_pressure_score":pressure_score,"crowding_liquidation_score":combined},
            "liquidation_regime_hmi": {"liquidation_regime_score":regime_score,"wasserstein_distance":wasserstein},
        },
        "current": {"top_position_ratio":latest(top_position),"liquidation_regime_score":latest(regime_score),"wasserstein_distance":latest(wasserstein)},
        "recalculate_in_hmi":False,
    }


def process_long_short_liquidations(input_contract: Mapping[str, Any], *, reference_price_context: Mapping[str, Any] | None = None,
                                    price_history: Sequence[Mapping[str, Any]] | None = None, positioning_history: Sequence[Mapping[str, Any]] | None = None,
                                    config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _json_safe(config, "config")
    validate_long_short_liquidations_input(input_contract)
    source, price_context, configuration = deepcopy(input_contract), deepcopy(reference_price_context), deepcopy(dict(config or {}))
    reference_timestamp = source["reference_timestamp"]
    if source["quality"]["status"] == "invalid":
        result = _invalid_output(reference_timestamp, configuration, ["input_quality_invalid"])
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        return result
    providers, cg = source["providers"], source["providers"]["coinglass"]
    history = cg["aggregated_history"]
    records = _usable(history, "records")
    realized_end = window_end_for_hourly(reference_timestamp, records)
    windows, variations = {}, {}
    for label, seconds in REALIZED_WINDOWS_SECONDS.items():
        current = aggregate_regular_window(records, window_end=realized_end, window_seconds=seconds)
        previous = aggregate_regular_window(records, window_end=realized_end-seconds, window_seconds=seconds)
        if history["status"] == "partial" and current["status"] == "available":
            current["status"], current["reason"] = "partial", "source_dataset_partial"
        windows[label], variations[label] = current, variation(current, previous)
    realized_series = [{**record, "total_liquidation_usd": record["long_liquidation_usd"] + record["short_liquidation_usd"]} for record in records]
    realized_provenance = {"provider": "coinglass", "endpoint_id": history.get("provenance", {}).get("endpoint_id"),
                           "source_dataset": "coinglass.aggregated_history", "source_interval": history.get("interval", "1h"),
                           "source_unit": "USD", "source_status": history["status"], "source_reference_timestamp": reference_timestamp,
                           "calculation_method": "semi_open_regular_windows_and_long_plus_short",
                           "windows": {key: {"window_start": value["window_start"], "window_end": value["window_end"],
                                             "coverage_ratio": value["coverage_ratio"]} for key, value in windows.items()}}
    snapshot = cg["exchange_snapshot"]
    exchange_distribution = build_exchange_distribution(_usable(snapshot, "records"))
    if snapshot["status"] == "invalid":
        exchange_distribution = {"status": "invalid", "reason": snapshot["reason"], "exchanges": []}
    exchange_distribution["provenance"] = {"provider": "coinglass", "endpoint_id": snapshot.get("provenance", {}).get("endpoint_id"),
        "snapshot_observed_at": snapshot.get("snapshot_observed_at"), "source_data_as_of": snapshot.get("source_data_as_of"),
        "valid_exchange_count": len(exchange_distribution.get("exchanges", [])),
        "excluded_exchange_count": len(snapshot.get("records", [])) - len(exchange_distribution.get("exchanges", [])),
        "calculation_method": "computed_long_plus_short_then_valid_total_shares"}
    exchange_histories = {exchange: {label: aggregate_regular_window(_usable(dataset, "records"), window_end=realized_end, window_seconds=seconds)
                                     for label, seconds in REALIZED_WINDOWS_SECONDS.items()}
                          for exchange, dataset in cg.get("pair_history", {}).items() if dataset["status"] in {"available", "partial"}}
    all_events, by_exchange_events = {}, {}
    event_datasets = cg.get("events", {})
    for exchange, dataset in event_datasets.items():
        event_records = _usable(dataset, "records")
        all_events.update({event["event_id"]: event for event in event_records})
        by_exchange_events[exchange] = {label: build_event_window(event_records, window_end=reference_timestamp, window_seconds=seconds,
            coverage_complete=_events_coverage_complete(dataset, reference_timestamp-seconds, reference_timestamp))
            for label, seconds in EVENT_WINDOWS_SECONDS.items()}
    def aggregate_coverage(start: int, end: int) -> bool:
        return bool(event_datasets) and all(_events_coverage_complete(dataset, start, end) for dataset in event_datasets.values())
    aggregate_events = {label: build_event_window(list(all_events.values()), window_end=reference_timestamp, window_seconds=seconds,
                        coverage_complete=aggregate_coverage(reference_timestamp-seconds, reference_timestamp))
                        for label, seconds in EVENT_WINDOWS_SECONDS.items()}
    event_provenance = {"providers": ["coinglass"], "included_exchanges": sorted(event_datasets), "interval": "event",
                        "truncation_detected": any("event_endpoint_record_limit_reached" in d.get("warnings", []) for d in event_datasets.values()),
                        "failed_segments": sum(d.get("status") != "available" for d in event_datasets.values()),
                        "calculation_method": "event_id_deduplication_and_semi_open_windows",
                        "zero_policy": "zero_only_when_request_coverage_complete"}
    map_source = cg["aggregated_map"]
    snapshot_at = map_source.get("snapshot_observed_at")
    price, reference_payload = validate_reference_price_context(price_context, snapshot_at)
    reference_reason = reference_payload.get("reason", "missing_reference_price")
    aggregated_map = build_map_features(_usable(map_source, "levels"), price, reference_reason=reference_reason)
    base_status = ("invalid" if map_source["status"] == "invalid" else "unavailable" if map_source["status"] == "unavailable"
                   else aggregated_map["concentration"]["complete_map"]["status"])
    aggregated_map["base"] = {"status": base_status,
                              "reason": map_source.get("reason") if map_source["status"] in {"invalid", "unavailable"}
                              else aggregated_map["concentration"]["complete_map"].get("reason"),
                              "level_count": len(aggregated_map.get("provider_levels", [])), "concentration": aggregated_map["concentration"]["complete_map"]}
    map_provenance = {"provider": "coinglass", "endpoint_id": map_source.get("provenance", {}).get("endpoint_id"),
        "source_dataset": "coinglass.aggregated_map", "source_snapshot_timestamp": snapshot_at,
        "reference_price_source_family": price_context.get("source_family") if isinstance(price_context, Mapping) else None,
        "reference_price_source_market": price_context.get("source_market") if isinstance(price_context, Mapping) else None,
        "reference_price_timeframe": price_context.get("source_timeframe") if isinstance(price_context, Mapping) else None,
        "reference_price_timestamp": price_context.get("timestamp") if isinstance(price_context, Mapping) else None,
        "side_assignment_method": "spatial_convention_v1", "provider_side_label_supplied": False,
        "bucket_width_bps": MAP_BUCKET_WIDTH_BPS, "central_tolerance_bps": MAP_CENTRAL_TOLERANCE_BPS,
        "interpolation_enabled": MAP_INTERPOLATION_ENABLED, "calculation_method": "decimal_relative_bps_bucketing"}
    aggregated_map["provenance"] = map_provenance
    by_exchange_maps, included, excluded, exclusion_reasons = {}, [], [], {}
    for exchange, dataset in cg.get("pair_maps", {}).items():
        feature = build_map_features(_usable(dataset, "levels"), price, reference_reason=reference_reason)
        by_exchange_maps[exchange] = feature
        if feature["status"] == "available" and feature["buckets"]["status"] in {"available", "partial"} and feature["buckets"]["items"]:
            included.append(exchange)
        else:
            excluded.append(exchange)
            exclusion_reasons[exchange] = feature.get("reason", reference_reason)
    aligned_status = "available" if included and not excluded else "partial" if included else "unavailable"
    aligned = {"status": aligned_status, "reason": None if aligned_status == "available" else "some_exchange_maps_excluded" if included else "no_exchange_maps_aligned",
               "included_exchanges": sorted(included), "excluded_exchanges": sorted(excluded), "exclusion_reasons": exclusion_reasons,
               "buckets": {"status": aligned_status, "reason": None if aligned_status == "available" else
                           "some_exchange_maps_excluded" if included else "no_exchange_maps_aligned",
                           "items": {exchange: deepcopy(by_exchange_maps[exchange]["buckets"]["items"]) for exchange in included}},
               "provenance": {"calculation_method": "independent_exchange_map_alignment", "reference_price_status": reference_payload["status"]}}
    confirmations = {}
    cq = providers.get("cryptoquant", {}).get("aggregate_history", {}) if isinstance(providers.get("cryptoquant"), Mapping) else {}
    if isinstance(cq, Mapping):
        confirmations["cryptoquant"] = confirmation(records, _usable(cq, "records") if cq.get("status") in VALID_DATASET_STATES else [],
                                                       intervals_match=cq.get("interval", "1h") == "1h")
    glassnode = providers.get("glassnode", {}) if isinstance(providers.get("glassnode"), Mapping) else {}
    long_gn, short_gn = glassnode.get("long_liquidations", {}), glassnode.get("short_liquidations", {})
    if isinstance(long_gn, Mapping) and isinstance(short_gn, Mapping):
        joined = {}
        for record in _usable(long_gn, "records"):
            joined.setdefault(record["timestamp"], {})["long"] = record.get("value")
        for record in _usable(short_gn, "records"):
            joined.setdefault(record["timestamp"], {})["short"] = record.get("value")
        gn_records = [{"timestamp": timestamp, "long_liquidations_usd": values["long"], "short_liquidations_usd": values["short"]}
                      for timestamp, values in joined.items() if values.get("long") is not None and values.get("short") is not None]
        confirmations["glassnode"] = confirmation(records, gn_records, units_match=long_gn.get("unit") == short_gn.get("unit") == "USD")
    historical_totals = []
    for offset in range(1, 73):
        item = aggregate_regular_window(records, window_end=realized_end-offset*3600, window_seconds=3600)
        if item["status"] == "available":
            historical_totals.append(item["total_usd"])
    realized_intensity = empirical_percentile(windows["1h"].get("total_usd") or 0, historical_totals, 24) if windows["1h"]["status"] != "unavailable" else None
    deltas = [max(historical_totals[index] - historical_totals[index+1], 0) for index in range(len(historical_totals)-1)]
    previous_1h = aggregate_regular_window(records, window_end=realized_end-3600, window_seconds=3600)
    current_delta = max((windows["1h"].get("total_usd") or 0) - (previous_1h.get("total_usd") or 0), 0)
    realized_acceleration = empirical_percentile(current_delta, deltas, 24)
    event_intensity = build_event_intensity(list(all_events.values()), current_end=reference_timestamp, coverage_checker=aggregate_coverage)
    proximity_value = aggregated_map.get("map_proximity") if isinstance(aggregated_map.get("map_proximity"), (int, float)) else None
    component_values = {"realized_intensity": realized_intensity, "realized_acceleration": realized_acceleration,
        "event_intensity": event_intensity["value"], "map_proximity": proximity_value,
        "map_concentration": aggregated_map.get("concentration", {}).get("complete_map", {}).get("top3_share"),
        "imbalance_magnitude": abs(windows["1h"]["imbalance"]["value"]) if windows["1h"]["imbalance"].get("value") is not None else None}
    pressure = build_pressure_score(component_values)
    pressure["components"] = {name: {"value": value, "status": "available" if value is not None else "unavailable",
                                     "reason": None if value is not None else (event_intensity["reason"] if name == "event_intensity" else "component_unavailable")}
                              for name, value in component_values.items()}
    pressure["components"]["event_intensity"]["metadata"] = event_intensity
    pressure["parameters"] = {"realized_baseline_hours": 72, "realized_minimum_baseline_points": 24, "event_baseline_hours": 24,
        "event_bin_seconds": 900, "event_minimum_complete_bins": EVENT_INTENSITY_MIN_COMPLETE_BINS,
        "map_proximity_decay_bps": MAP_PROXIMITY_DECAY_BPS, "minimum_available_weight": PRESSURE_MIN_AVAILABLE_WEIGHT}
    pressure["provenance"] = {"component_source_paths": {"realized_intensity": "realized.windows.1h", "event_intensity": "events.aggregate",
        "map_proximity": "maps.aggregated.map_proximity", "map_concentration": "maps.aggregated.concentration.complete_map.top3_share",
        "imbalance_magnitude": "realized.windows.1h.imbalance"}, "calculation_method": "weighted_normalized_component_score"}
    statuses = {"realized.series": "invalid" if history["status"] == "invalid" else "available" if realized_series else "unavailable",
                **{f"realized.windows.{key}": "invalid" if history["status"] == "invalid" else value["status"] for key, value in windows.items()},
                "exchange_distribution": exchange_distribution["status"], "events.aggregate.24h": aggregate_events["24h"]["status"],
                "maps.aggregated.base": aggregated_map["base"]["status"]}
    missing = [path for path in PROCESSING_REQUIRED_FEATURES if path not in statuses]
    invalid = [path for path, status in statuses.items() if status == "invalid"]
    partial = [path for path, status in statuses.items() if status == "partial"]
    unavailable = [path for path, status in statuses.items() if status == "unavailable"]
    if missing or invalid:
        quality_status = "invalid"
    elif partial or unavailable or source["quality"]["status"] == "partial":
        quality_status = "partial" if any(status in {"available", "partial"} for status in statuses.values()) else "unavailable"
    else:
        quality_status = "available"
    warnings = list(dict.fromkeys(source["quality"].get("warnings", []) + history.get("warnings", []) + map_source.get("warnings", [])))
    result = {"family": "long_short_liquidations", "stage": "processing", "reference_timestamp": reference_timestamp,
        "configuration": {"version": "0.1", **configuration}, "source_selection": _source_selection(providers),
        "realized": {"series": realized_series, "windows": windows, "variations": variations, "confirmations": confirmations,
                     "provenance": realized_provenance},
        "exchange_distribution": exchange_distribution, "exchange_histories": exchange_histories,
        "events": {"aggregate": aggregate_events, "by_exchange": by_exchange_events, "provenance": event_provenance},
        "maps": {"reference_price": reference_payload, "aggregated": aggregated_map, "by_exchange": by_exchange_maps,
                 "aligned_exchanges": aligned, "max_pain": _max_pain(cg["max_pain"], price)}, "pressure": pressure,
        "liquidation_analysis": _native_liquidation_analysis(realized_series, price_history=price_history, positioning_history=positioning_history),
        "quality": {"status": quality_status, "required_features": PROCESSING_REQUIRED_FEATURES,
                    "optional_features": PROCESSING_OPTIONAL_FEATURES, "missing_features": missing, "invalid_features": invalid,
                    "partial_features": partial, "unavailable_features": unavailable, "warnings": warnings, "errors": []}}
    _json_safe(result, "output")
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


class LongShortLiquidationsProcessor:
    def __init__(self, input_contract: Mapping[str, Any], *, reference_price_context: Mapping[str, Any] | None = None,
                 price_history: Sequence[Mapping[str, Any]] | None = None, positioning_history: Sequence[Mapping[str, Any]] | None = None,
                 config: Mapping[str, Any] | None = None) -> None:
        self.input_contract, self.reference_price_context, self.price_history, self.positioning_history, self.config = input_contract, reference_price_context, price_history, positioning_history, config

    def run(self) -> dict[str, Any]:
        return process_long_short_liquidations(self.input_contract, reference_price_context=self.reference_price_context, price_history=self.price_history, positioning_history=self.positioning_history, config=self.config)
