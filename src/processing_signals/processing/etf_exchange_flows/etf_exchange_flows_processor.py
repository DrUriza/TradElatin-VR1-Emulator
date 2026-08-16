"""Processing contract assembly for ETF and exchange flows."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from typing import Any

from .etf_exchange_flows_feature_builder import (
    FAMILY,
    MIN_COVERAGE_RATIO,
    RANGE_SECONDS,
    PRESSURE_WINDOW,
    build_etf_exchange_flows_features,
)

STAGE   = "processing"
VERSION = "0.1"

REQUIRED_FEATURES = ("etf_net_flow_usd_latest", "reported_total_aum_usd", "gbtc_premium_latest", "exchange_inflow_24h",
                     "exchange_outflow_24h", "exchange_netflow_24h_reported", "cryptoquant_reserve_latest")
OPTIONAL_FEATURES = ("etf_net_flow_btc_latest", "etf_period_flow_usd", "etf_period_flow_btc", "etf_cumulative_flow_usd",
    "etf_cumulative_flow_btc", "fund_period_flow_usd", "fund_period_signed_flow_share", "fund_aum_usd", "fund_aum_share",
    "calculated_fund_aum_usd", "aum_difference_usd", "aum_difference_percent", "exchange_netflow_24h_calculated",
    "netflow_difference", "coinglass_balance_total", "exchange_flow_pressure_24h")
SECONDARY_FEATURES = ("glassnode_balance_secondary", "balance_provider_spread")


def _timestamp(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        if isinstance(value, float) and not value.is_integer():
            return None
        return int(value) if value > 0 else None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = int(parsed.timestamp())
            return result if result > 0 else None
        except ValueError:
            return None
    return None


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def validate_etf_exchange_flows_input(input_contract: Any) -> None:
    if not isinstance(input_contract, Mapping):
        raise ValueError("invalid_processing_input")
    if input_contract.get("family") != FAMILY or input_contract.get("stage") != "input":
        raise ValueError("invalid_processing_input")
    if not isinstance(input_contract.get("datasets"), Mapping):
        raise ValueError("invalid_processing_input")
    if input_contract.get("data_mode") not in {"live", "synthetic"} or not isinstance(input_contract.get("is_demo"), bool):
        raise ValueError("invalid_processing_input")
    if not isinstance(input_contract.get("quality"), Mapping) or not isinstance(input_contract.get("provenance"), Mapping):
        raise ValueError("invalid_processing_input")
    if _timestamp(input_contract.get("requested_at")) is None:
        raise ValueError("invalid_processing_input")
    if input_contract.get("data_as_of") is not None and _timestamp(input_contract.get("data_as_of")) is None:
        raise ValueError("invalid_processing_input")


def _feature_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    features = payload["features"]
    return {"etf_net_flow_usd_latest": features["etf"]["net_flow_usd_latest"],
        "reported_total_aum_usd": features["etf"]["reported_total_aum_usd"],
        "gbtc_premium_latest": features["premium_discount"]["gbtc_latest"],
        "exchange_inflow_24h": features["exchange_flows"]["inflow_24h"],
        "exchange_outflow_24h": features["exchange_flows"]["outflow_24h"],
        "exchange_netflow_24h_reported": features["exchange_flows"]["netflow_24h_reported"],
        "cryptoquant_reserve_latest": features["exchange_balances"]["cryptoquant_reserve"],
        "etf_net_flow_btc_latest": features["etf"]["net_flow_btc_latest"],
        "calculated_fund_aum_usd": features["etf"]["calculated_fund_aum_usd"],
        "exchange_netflow_24h_calculated": features["exchange_flows"]["netflow_24h_calculated"],
        "coinglass_balance_total": features["exchange_balances"]["coinglass_total"],
        "exchange_flow_pressure_24h": features["pressure"]["flow_24h"],
        "glassnode_balance_secondary": features["exchange_balances"]["glassnode_secondary"]}


def is_non_isolatable_required_error(feature: Mapping[str, Any]) -> bool:
    return feature.get("status") == "invalid" and feature.get("reason") in {"nonfinite_result", "future_timestamp", "invalid_processing_input"}


def _future_records_by_dataset(input_contract: Mapping[str, Any], generated_timestamp: int) -> dict[str, int]:
    datasets = input_contract.get("datasets", {})
    if not isinstance(datasets, Mapping):
        return {}
    result: dict[str, int] = {}

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, list):
            future = [item for item in value if isinstance(item, Mapping) and
                      (_timestamp(item.get("timestamp")) or 0) > generated_timestamp]
            endpoint_values = [item.get("endpoint_id") for item in future]
            valid_endpoint_values = [item.strip() for item in endpoint_values
                                     if isinstance(item, str) and item.strip()]
            endpoints = set(valid_endpoint_values)
            secondary = path[:1] == ("secondary_sources",)
            if secondary and len(endpoints) > 1 and len(valid_endpoint_values) == len(future):
                for endpoint_id in sorted(endpoints):
                    count = sum(item.get("endpoint_id") == endpoint_id for item in future)
                    endpoint_path = (*path[:-1], endpoint_id.strip(), path[-1])
                    result[".".join(endpoint_path)] = int(count)
            elif future:
                result[".".join(path)] = len(future)
            return
        if isinstance(value, Mapping):
            for name, child in value.items():
                walk(child, (*path, str(name)))

    for name, value in datasets.items():
        walk(value, (str(name),))
    return dict(sorted(result.items()))


def _apply_input_quality(payload: dict[str, Any], input_contract: Mapping[str, Any]) -> None:
    endpoints = input_contract.get("quality", {}).get("endpoints", {})
    if not isinstance(endpoints, Mapping):
        return
    dependencies = {
        "coinglass.bitcoin_etf_flows": payload["features"]["etf"]["net_flow_usd_latest"],
        "coinglass.bitcoin_etf_net_assets_history": payload["features"]["etf"]["reported_total_aum_usd"],
        "coinglass.bitcoin_etf_premium_discount_history": payload["features"]["premium_discount"]["gbtc_latest"],
        "cryptoquant.exchange_inflow.hour": payload["features"]["exchange_flows"]["inflow_24h"],
        "cryptoquant.exchange_outflow.hour": payload["features"]["exchange_flows"]["outflow_24h"],
        "cryptoquant.exchange_netflow.hour": payload["features"]["exchange_flows"]["netflow_24h_reported"],
        "cryptoquant.exchange_reserve.hour": payload["features"]["exchange_balances"]["cryptoquant_reserve"],
    }
    for endpoint, feature in dependencies.items():
        source_status = endpoints.get(endpoint, {}).get("status") if isinstance(endpoints.get(endpoint), Mapping) else None
        if source_status not in {"invalid", "unavailable"}:
            continue
        reason = "source_invalid" if source_status == "invalid" else "source_unavailable"
        if feature.get("value") is None:
            feature.update(status="unavailable", reason=reason)
        else:
            feature.update(status="partial", reason=reason, warnings=sorted(set([*feature.get("warnings", []), reason])))


def evaluate_etf_exchange_flows_processing_quality(payload: Mapping[str, Any], input_contract: Mapping[str, Any]) -> dict[str, Any]:
    feature_map = _feature_map(payload)
    statuses = [item.get("status") for item in feature_map.values()]
    required = {name: feature_map[name] for name in REQUIRED_FEATURES}
    usable = [item for item in required.values() if item.get("status") in {"available", "partial"} and isinstance(item.get("value"), (int, float))]
    nonisolatable = any(is_non_isolatable_required_error(item) for item in required.values())
    if nonisolatable or not usable:
        status = "invalid"
    elif all(item.get("status") == "available" for item in required.values()):
        status = "ok"
    else:
        status = "partial"
    anchors = [int(item["data_as_of"]) for item in usable if isinstance(item.get("data_as_of"), int)]
    timestamps = [item.get("timestamp") for item in feature_map.values() if isinstance(item.get("timestamp"), int)]
    input_datasets = input_contract.get("datasets", {})
    coverage_by_dataset = {name: len(value) if isinstance(value, list) else
        sum(len(rows) for rows in value.values() if isinstance(rows, list)) if isinstance(value, Mapping) else 0 for name, value in input_datasets.items()}
    warnings = sorted(set(payload.get("warnings", [])))
    return {"status": status, "features_available": statuses.count("available"), "features_partial": statuses.count("partial"),
        "features_unavailable": statuses.count("unavailable"), "features_invalid": statuses.count("invalid"),
        "required_available": sum(item.get("status") == "available" for item in required.values()),
        "required_partial": sum(item.get("status") == "partial" for item in required.values()),
        "required_unavailable": sum(item.get("status") == "unavailable" for item in required.values()),
        "required_invalid": sum(item.get("status") == "invalid" for item in required.values()), "required_usable": len(usable),
        "required_degraded": sum(item.get("status") != "available" for item in required.values()), "coverage_by_dataset": coverage_by_dataset,
        "first_timestamp": min(timestamps) if timestamps else None, "last_timestamp": max(timestamps) if timestamps else None,
        "data_as_of": min(anchors) if anchors else None, "warnings": warnings, "errors": []}


def _provenance(payload: Mapping[str, Any], input_contract: Mapping[str, Any], exchange_scope: str | None) -> dict[str, Any]:
    netflow_reconciliation = payload["features"]["provider_reconciliation"]["netflow"]
    generated_timestamp = int(payload["generated_timestamp"])
    future_by_dataset = _future_records_by_dataset(input_contract, generated_timestamp)
    flows = payload["features"]["exchange_flows"]
    negative_by_feature = {name: int(flows[name].get("coverage", {}).get("invalid_observations", 0))
                           for name in ("inflow_24h", "outflow_24h")}
    negative_by_feature = {name: count for name, count in negative_by_feature.items() if count}
    return {"input_family": FAMILY, "input_data_as_of": input_contract.get("data_as_of"),
        "datasets_used": sorted(input_contract.get("datasets", {})),
        "providers": deepcopy(input_contract.get("provenance", {}).get("providers", {})),
        "feature_sources": {name: {"provider": item.get("provider"), "endpoint_id": item.get("endpoint_id"),
            "data_as_of": item.get("data_as_of"), "scope": item.get("exchange_scope")} for name, item in _feature_map(payload).items()},
        "formulas": {"etf_net_flow_btc_latest": "flow_usd/price_usd_same_row", "etf_period_flow_btc": "sum(flow_usd_i/price_usd_i)",
            "exchange_netflow_24h_calculated": "inflow_24h-outflow_24h", "exchange_flow_pressure_24h": "(inflow_24h-outflow_24h)/(inflow_24h+outflow_24h)",
            "fund_period_signed_flow_share": "fund_flow/sum(abs(fund_flow))"},
        "parameters": {"ranges": deepcopy(RANGE_SECONDS), "pressure_window_seconds": PRESSURE_WINDOW,
            "minimum_coverage_ratio": MIN_COVERAGE_RATIO, "exchange_scope": exchange_scope},
        "reconciliations": {"netflow": {name: deepcopy(netflow_reconciliation.get(name)) for name in
            ("calculated_anchor", "reported_anchor", "timestamp_distance", "window_seconds", "scope", "alignment_required")}},
        "anomalies": {"warnings": sorted(set(payload.get("warnings", []))),
            "future_records_excluded": sum(future_by_dataset.values()),
            "future_records_by_dataset": future_by_dataset,
            "negative_observations_rejected": sum(negative_by_feature.values()),
            "negative_observations_by_feature": negative_by_feature}}


def process_etf_exchange_flows(*, input_contract: Mapping[str, Any], generated_at: Any = None,
                               exchange_scope: str | None = None) -> dict[str, Any]:
    validate_etf_exchange_flows_input(input_contract)
    source = deepcopy(dict(input_contract))
    generated_timestamp = _timestamp(generated_at if generated_at is not None else source.get("generated_at"))
    if generated_timestamp is None:
        raise ValueError("invalid_processing_input")
    payload = build_etf_exchange_flows_features(input_contract=source, generated_at=generated_timestamp, exchange_scope=exchange_scope)
    _apply_input_quality(payload, source)
    quality = evaluate_etf_exchange_flows_processing_quality(payload, source)
    output = {"family": FAMILY, "stage": STAGE, "version": VERSION, "mode": source.get("mode"), "data_mode": source.get("data_mode"),
        "is_demo": source.get("is_demo"), "generated_at": _iso(generated_timestamp), "data_as_of": quality["data_as_of"],
        "features": deepcopy(payload["features"]), "series": deepcopy(payload["series"]),
        "technical_analysis": deepcopy(payload.get("technical_analysis", {})),
        "series_metadata": deepcopy(payload.get("series_metadata", {})), "snapshots": deepcopy(payload["snapshots"]),
        "provenance": _provenance(payload, source, exchange_scope), "quality": quality}
    json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=False)
    return output


def run_etf_exchange_flows_processing(*, input_contract: Mapping[str, Any], generated_at: Any = None,
                                      exchange_scope: str | None = None) -> dict[str, Any]:
    return process_etf_exchange_flows(input_contract=input_contract, generated_at=generated_at, exchange_scope=exchange_scope)


class EtfExchangeFlowsProcessor:
    def process(self, *, input_contract: Mapping[str, Any], generated_at: Any = None, exchange_scope: str | None = None) -> dict[str, Any]:
        return process_etf_exchange_flows(input_contract=input_contract, generated_at=generated_at, exchange_scope=exchange_scope)
