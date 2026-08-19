from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime        import datetime, timezone
from typing          import Any


ON_CHAIN_MINERS_FAMILY   = "on_chain_miners"
GLASSNODE_PROVIDER       = "glassnode"
CRYPTOQUANT_PROVIDER     = "cryptoquant"
COINGLASS_PROVIDER       = "coinglass"
VALID_MODES              = {"bootstrap", "incremental", "recovery"}
CORE_METRIC_IDS          = ("miner_reserve", "sopr", "hashrate", "difficulty", "miner_net_position_change", "mpi")
SCREEN_EXTENSION_METRIC_IDS = ("miner_entities", "miner_outflow_by_pool", "miner_outflow_total", "miners_unspent_supply", "utxo_age_distribution",
                               "miner_revenue_total_usd", "miner_revenue_from_fees", "nupl")
TIME_SERIES_EXTENSION_IDS   = ("miner_outflow_total", "miners_unspent_supply", "utxo_age_distribution", "miner_revenue_total_usd",
                               "miner_revenue_from_fees", "nupl")
COLLECTION_EXTENSION_IDS    = ("miner_entities", "miner_outflow_by_pool")
UTXO_AGE_BANDS = ("0d_1d", "1d_1w", "1w_1m", "1m_3m", "3m_6m", "6m_12m", "12m_18m", "18m_2y", "2y_3y", "3y_5y", "5y_7y", "7y_10y", "10y_inf")
DEFAULT_INCLUDE_SCREEN_EXTENSIONS = True
ENRICHMENT_METRIC_IDS    = ("puell_multiple", "sth_sopr", "lth_sopr")
BOOTSTRAP_HISTORY_DAYS   = 730
BOOTSTRAP_LIMIT          = 740
INCREMENTAL_OVERLAP_DAYS = 7
INCREMENTAL_LIMIT        = 14
SECONDS_PER_DAY          = 86_400
RECOVERY_WARMUP_DAYS     = {"miner_reserve": 31, "sopr": 7, "hashrate": 2, "difficulty": 2, "miner_net_position_change": 2, "mpi": 2,
                            **{metric_id: 2 for metric_id in (*TIME_SERIES_EXTENSION_IDS, "miner_outflow_by_pool")}}

ENDPOINTS = {
    "miner_reserve": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "balance_miners_sum", "path": "/v1/metrics/distribution/balance_miners_sum",
                      "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "1h"},
    "sopr": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "sopr", "path": "/v1/metrics/indicators/sopr", "source_field": "v",
             "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "1h"},
    "hashrate": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "hash_rate_mean", "path": "/v1/metrics/mining/hash_rate_mean", "source_field": "v",
                 "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "1h"},
    "difficulty": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "difficulty_latest", "path": "/v1/metrics/mining/difficulty_latest", "source_field": "v",
                   "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "1h"},
    "miner_net_position_change": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "balance_miners_change", "path": "/v1/metrics/distribution/balance_miners_change",
                                  "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "24h"},
    "mpi": {"provider": CRYPTOQUANT_PROVIDER, "endpoint_id": "mpi", "path": "/btc/flow-indicator/mpi", "source_field": "mpi",
            "raw_shape": "cryptoquant_status_result_data", "required": True},
    "puell_multiple": {"provider": COINGLASS_PROVIDER, "endpoint_id": "puell_multiple", "path": "/api/index/puell-multiple", "source_field": "puell_multiple",
                       "raw_shape": "coinglass_code_msg_data", "required": False},
    "sth_sopr": {"provider": COINGLASS_PROVIDER, "endpoint_id": "bitcoin_sth_sopr", "path": "/api/index/bitcoin-sth-sopr", "source_field": "sth_sopr",
                 "raw_shape": "coinglass_code_msg_data", "required": False},
    "lth_sopr": {"provider": COINGLASS_PROVIDER, "endpoint_id": "bitcoin_lth_sopr", "path": "/api/index/bitcoin-lth-sopr", "source_field": "lth_sopr",
                 "raw_shape": "coinglass_code_msg_data", "required": False},
    "miner_entities": {"provider": CRYPTOQUANT_PROVIDER, "endpoint_id": "miner_entity_list", "path": "/btc/status/entity-list",
                       "raw_shape": "cryptoquant_status_result_data", "required": True, "request_kind": "entity_catalog"},
    "miner_outflow_by_pool": {"provider": CRYPTOQUANT_PROVIDER, "endpoint_id": "miner_outflow", "path": "/btc/miner-flows/outflow",
                              "raw_shape": "cryptoquant_status_result_data", "required": True, "request_kind": "dynamic_fanout"},
    "miner_outflow_total": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "transfers_volume_from_miners_sum", "path": "/v1/metrics/transactions/transfers_volume_from_miners_sum",
                            "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True, "interval": "24h"},
    "miners_unspent_supply": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "miners_unspent_supply", "path": "/v1/metrics/mining/miners_unspent_supply",
                              "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True},
    "utxo_age_distribution": {"provider": CRYPTOQUANT_PROVIDER, "endpoint_id": "utxo_age_distribution", "path": "/btc/network-indicator/utxo-age-distribution",
                              "raw_shape": "cryptoquant_status_result_data", "required": True},
    "miner_revenue_total_usd": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "revenue_sum", "path": "/v1/metrics/mining/revenue_sum",
                                "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True},
    "miner_revenue_from_fees": {"provider": GLASSNODE_PROVIDER, "endpoint_id": "revenue_from_fees", "path": "/v1/metrics/mining/revenue_from_fees",
                                "source_field": "v", "raw_shape": "glassnode_list_t_v_scalar", "required": True},
    "nupl": {"provider": COINGLASS_PROVIDER, "endpoint_id": "bitcoin_nupl", "path": "/api/index/bitcoin-net-unrealized-profit-loss", "source_field": "net_unpnl",
             "raw_shape": "coinglass_code_data", "required": True},
}

OnChainMinersFetcher = Callable[..., Mapping[str, Any] | Sequence[Any]]


def is_validated_miner_flag(value: Any) -> bool:
    return type(value) is int and value == 1

def _valid_timestamp(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or value < 0:
        raise ValueError(f"{name} must be a non-negative Unix timestamp")
    return int(value)

def _utc_day(timestamp: Any) -> int:
    timestamp = _valid_timestamp(timestamp, "timestamp")
    return timestamp - timestamp % SECONDS_PER_DAY


def _iso_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(_valid_timestamp(timestamp, "execution_timestamp"), timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_existing_input_state(existing_state: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Resolve either a direct Input output or a complete vertical bundle."""
    if existing_state is None:
        return {}
    if not isinstance(existing_state, Mapping):
        raise ValueError("existing_state must be a mapping, None, or an empty mapping")
    if not existing_state:
        return {}
    if "input" in existing_state:
        input_state = existing_state.get("input")
        if not isinstance(input_state, Mapping):
            raise ValueError("existing_state.input must be an on_chain_miners Input mapping")
        return resolve_existing_input_state(input_state)
    if existing_state.get("family") == ON_CHAIN_MINERS_FAMILY and existing_state.get("stage") == "input" and isinstance(existing_state.get("series"), Mapping):
        return existing_state
    raise ValueError("existing_state must be an on_chain_miners Input output or a vertical bundle containing it")

def build_cryptoquant_daily_params(*, from_timestamp: int, to_timestamp: int, limit: int) -> dict[str, Any]:
    start = datetime.fromtimestamp(_valid_timestamp(from_timestamp, "from_timestamp"), timezone.utc).strftime("%Y%m%d")
    end   = datetime.fromtimestamp(_valid_timestamp(to_timestamp, "to_timestamp"), timezone.utc).strftime("%Y%m%d")
    if limit <= 0:
        raise ValueError("limit must be positive")
    return {"window": "day", "from": start, "to": end, "limit": int(limit), "format": "json"}

def build_glassnode_daily_params(*, asset: str, from_timestamp: int, to_timestamp: int, native_currency: bool = False, interval: str = "24h") -> dict[str, Any]:
    params = {"a": str(asset).upper(), "i": str(interval), "s": _valid_timestamp(from_timestamp, "from_timestamp"), "u": _valid_timestamp(to_timestamp, "to_timestamp")}
    if native_currency:
        params["c"] = "NATIVE"
    return params

def _existing_records(existing_contract: Mapping[str, Any] | None, metric_id: str) -> Sequence[Any]:
    series  = (existing_contract or {}).get("series", {})
    payload = series.get(metric_id, {}) if isinstance(series, Mapping) else {}
    records = payload.get("records", []) if isinstance(payload, Mapping) else []
    return records if isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)) else []


def _existing_pool_last_timestamp(existing_contract: Mapping[str, Any] | None) -> int | None:
    collection = (existing_contract or {}).get("collections", {}).get("miner_outflow_by_pool", {})
    pools = collection.get("pools", {}) if isinstance(collection, Mapping) else {}
    timestamps = [record.get("timestamp") for pool in pools.values() if isinstance(pool, Mapping) for record in pool.get("records", []) if isinstance(record, Mapping)]
    valid = [int(value) for value in timestamps if isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) == value]
    return max(valid) if valid else None


def _persisted_active_miner_symbols(existing_contract: Mapping[str, Any] | None) -> list[str]:
    collections = (existing_contract or {}).get("collections", {})
    if not isinstance(collections, Mapping):
        return []
    pools = collections.get("miner_outflow_by_pool", {}).get("pools", {})
    if isinstance(pools, Mapping):
        symbols = [symbol for symbol, pool in pools.items() if isinstance(symbol, str) and symbol.strip()
                   and isinstance(pool, Mapping) and pool.get("active") is True]
        if symbols:
            return sorted(set(symbols))
    records = collections.get("miner_entities", {}).get("records", [])
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        return []
    return sorted({item["symbol"].strip() for item in records if isinstance(item, Mapping)
                   and is_validated_miner_flag(item.get("is_validated"))
                   and isinstance(item.get("symbol"), str) and item["symbol"].strip()})

def _last_existing_timestamp(existing_contract: Mapping[str, Any] | None, metric_id: str) -> int | None:
    timestamps = [record.get("timestamp") for record in _existing_records(existing_contract, metric_id) if isinstance(record, Mapping)]
    valid      = [int(value) for value in timestamps if isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) == value]
    return max(valid) if valid else None

def _build_request(metric_id: str, start: int, end: int, limit: int, asset: str = "BTC") -> dict[str, Any]:
    endpoint = ENDPOINTS[metric_id]
    if metric_id == "miner_entities":
        params = {"type": "miner", "format": "json"}
    elif metric_id == "miner_outflow_by_pool":
        params = build_cryptoquant_daily_params(from_timestamp=start, to_timestamp=end, limit=limit)
    elif endpoint["provider"] == CRYPTOQUANT_PROVIDER:
        params = build_cryptoquant_daily_params(from_timestamp=start, to_timestamp=end, limit=limit)
    elif endpoint["provider"] == GLASSNODE_PROVIDER:
        params = build_glassnode_daily_params(asset=asset, from_timestamp=start, to_timestamp=end,
                                              native_currency=metric_id in {"miner_reserve", "miners_unspent_supply"},
                                              interval=str(endpoint.get("interval", "24h")))
        if metric_id == "miner_revenue_total_usd":
            params["c"] = "USD"
    else:
        params = {}
    return {"metric_id": metric_id, "provider": endpoint["provider"], "endpoint_id": endpoint["endpoint_id"], "path": endpoint["path"],
            "from_timestamp": start, "to_timestamp": end, "limit": limit, "params": params, "required": endpoint["required"]}

def _source_bucket(timestamp: int, endpoint: Mapping[str, Any]) -> int:
    interval = str(endpoint.get("interval", "24h"))
    seconds = 3_600 if interval == "1h" else SECONDS_PER_DAY
    return timestamp - timestamp % seconds


def _metric_due(*, metric_id: str, reference_timestamp: int, existing_contract: Mapping[str, Any]) -> bool:
    endpoint = ENDPOINTS[metric_id]
    last = _existing_pool_last_timestamp(existing_contract) if metric_id == "miner_outflow_by_pool" else _last_existing_timestamp(existing_contract, metric_id)
    if last is None:
        return True
    return int(last) < _source_bucket(reference_timestamp, endpoint)


def build_on_chain_miners_fetch_plan(*, mode: str, reference_timestamp: int, existing_contract: Mapping[str, Any] | None = None,
                                     recovery_requests: Sequence[Mapping[str, Any]] | None = None, include_enrichment: bool = False,
                                     include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS,
                                     refresh_catalog: bool = False) -> list[dict[str, Any]]:
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported on_chain_miners input mode: {mode}")
    reference_day = _utc_day(reference_timestamp)
    existing_contract = resolve_existing_input_state(existing_contract)
    metric_ids = CORE_METRIC_IDS + (SCREEN_EXTENSION_METRIC_IDS if include_screen_extensions else ()) + (ENRICHMENT_METRIC_IDS if include_enrichment else ())
    requests: list[dict[str, Any]] = []
    if mode == "recovery":
        if not recovery_requests:
            raise ValueError("recovery mode requires at least one recovery request")
        for item in recovery_requests:
            if not isinstance(item, Mapping):
                raise ValueError("recovery request must be a mapping")
            metric_id = item.get("metric_id")
            if metric_id not in ENDPOINTS:
                raise ValueError(f"Invalid recovery metric_id: {metric_id}")
            start = _utc_day(item.get("start_timestamp"))
            end   = _utc_day(item.get("end_timestamp"))
            if start > end:
                raise ValueError("recovery range must not be inverted")
            warmup = RECOVERY_WARMUP_DAYS.get(str(metric_id), 0)
            fetch_start = max(0, start - warmup * SECONDS_PER_DAY)
            limit = max(1, (end - fetch_start) // SECONDS_PER_DAY + 1)
            request = _build_request(str(metric_id), fetch_start, end, limit)
            if metric_id == "miner_outflow_by_pool" and item.get("miner_symbols") is not None:
                symbols = item.get("miner_symbols")
                if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes, bytearray)):
                    raise ValueError("miner_symbols must be a sequence")
                request["miner_symbols"] = sorted({symbol.strip() for symbol in symbols if isinstance(symbol, str) and symbol.strip()})
                request["catalog_source"] = "existing_state" if existing_contract else "explicit_request"
                request["catalog_refresh_succeeded"] = None
            elif metric_id == "miner_outflow_by_pool":
                persisted = _persisted_active_miner_symbols(existing_contract)
                if persisted:
                    request["miner_symbols"] = persisted
                    request["catalog_source"] = "existing_state"
                    request["catalog_refresh_succeeded"] = False
                elif not any(candidate["metric_id"] == "miner_entities" for candidate in requests):
                    requests.append(_build_request("miner_entities", fetch_start, end, limit))
            requests.append(request)
        return requests

    persisted_symbols = _persisted_active_miner_symbols(existing_contract) if mode == "incremental" else []
    for metric_id in metric_ids:
        # The miner entity catalog is effectively static. Reuse the persisted validated
        # symbols during normal incremental cycles and refresh it only explicitly.
        if mode == "incremental" and metric_id == "miner_entities" and persisted_symbols and not refresh_catalog:
            continue
        # Provider cadence gate: do not pay repeatedly for a source bucket that is
        # already persisted. Hourly Glassnode primitives refresh once per hour; all
        # daily metrics and drilldowns refresh once per UTC day.
        if mode == "incremental" and metric_id != "miner_entities" and not _metric_due(
                metric_id=metric_id, reference_timestamp=reference_timestamp, existing_contract=existing_contract):
            continue
        last = (_existing_pool_last_timestamp(existing_contract) if metric_id == "miner_outflow_by_pool" else _last_existing_timestamp(existing_contract, metric_id)) if mode == "incremental" else None
        if last is None:
            extra_warmup_days = 6 if metric_id == "sopr" else 0
            start = reference_day - (BOOTSTRAP_HISTORY_DAYS + extra_warmup_days) * SECONDS_PER_DAY
            limit = BOOTSTRAP_LIMIT + extra_warmup_days
        else:
            start = max(0, _utc_day(last) - INCREMENTAL_OVERLAP_DAYS * SECONDS_PER_DAY)
            limit = INCREMENTAL_LIMIT
        endpoint = ENDPOINTS[metric_id]
        request_end = reference_timestamp if endpoint.get("provider") == GLASSNODE_PROVIDER and str(endpoint.get("interval", "24h")) != "24h" else reference_day
        requests.append(_build_request(metric_id, start, request_end, limit))
    return requests

def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    for marker in ("api_key", "apikey", "token", "authorization", "bearer"):
        if marker in message.lower():
            return "provider request failed; sensitive details redacted"
    return message

def _execute_metric_request(*, fetcher: OnChainMinersFetcher, request: Mapping[str, Any]) -> dict[str, Any]:
    base = {key: copy.deepcopy(request[key]) for key in ("metric_id", "provider", "endpoint_id", "path", "required", "params", "from_timestamp", "to_timestamp")}
    try:
        response = fetcher(provider=request["provider"], endpoint_id=request["endpoint_id"], path=request["path"], params=copy.deepcopy(request["params"]))
        return {**base, "status": "ok", "response": copy.deepcopy(response), "error": None}
    except Exception as exc:  # Each provider endpoint has an independent failure contract.
        return {**base, "status": "error", "response": None, "error": {"type": type(exc).__name__, "message": _safe_error_message(exc)}}


def validated_miner_symbols(response: Any) -> list[str]:
    if not isinstance(response, Mapping) or not isinstance(response.get("result"), Mapping):
        return []
    data = response["result"].get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return []
    return sorted({item["symbol"].strip() for item in data if isinstance(item, Mapping) and is_validated_miner_flag(item.get("is_validated"))
                   and isinstance(item.get("symbol"), str) and item["symbol"].strip()})


def _entity_catalog_state(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping) or payload.get("status") != "ok":
        return "request_failed"
    response = payload.get("response")
    if not isinstance(response, Mapping) or not isinstance(response.get("status"), Mapping) \
            or response["status"].get("code") not in (200, "200") or not isinstance(response.get("result"), Mapping):
        return "invalid"
    data = response["result"].get("data")
    return "valid" if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)) else "invalid"


def _execute_outflow_fanout(*, fetcher: OnChainMinersFetcher, request: Mapping[str, Any], entity_payload: Mapping[str, Any] | None,
                            existing_contract: Mapping[str, Any] | None) -> dict[str, Any]:
    catalog_state = _entity_catalog_state(entity_payload) if entity_payload is not None else None
    explicit_symbols = request.get("miner_symbols")
    if explicit_symbols is not None:
        symbols = list(explicit_symbols)
        catalog_source = request.get("catalog_source", "explicit_request")
        refresh_succeeded = request.get("catalog_refresh_succeeded")
    elif catalog_state == "valid":
        symbols = validated_miner_symbols(entity_payload.get("response"))
        catalog_source = "live"
        refresh_succeeded = True
    else:
        symbols = _persisted_active_miner_symbols(existing_contract)
        catalog_source = "existing_state" if symbols else None
        refresh_succeeded = False
    results = []
    for symbol in symbols:
        params = {**copy.deepcopy(request["params"]), "miner": symbol}
        try:
            response = fetcher(provider=CRYPTOQUANT_PROVIDER, endpoint_id="miner_outflow", path=ENDPOINTS["miner_outflow_by_pool"]["path"], params=copy.deepcopy(params))
            results.append({"miner_symbol": symbol, "status": "ok", "params": params, "response": copy.deepcopy(response), "error": None})
        except Exception as exc:
            results.append({"miner_symbol": symbol, "status": "error", "params": params, "response": None,
                            "error": {"type": type(exc).__name__, "message": _safe_error_message(exc)}})
    successes = sum(item["status"] == "ok" for item in results)
    status = "ok" if results and successes == len(results) else "partial" if successes else "error"
    return {"metric_id": "miner_outflow_by_pool", "provider": CRYPTOQUANT_PROVIDER, "status": status,
            "entity_symbols": list(symbols), "requests": results, "from_timestamp": request["from_timestamp"], "to_timestamp": request["to_timestamp"],
            "catalog_state": catalog_state, "catalog_source": catalog_source, "catalog_refresh_succeeded": refresh_succeeded,
            "fanout_skipped_no_symbols": not symbols}

class OnChainMinersRawExtractor:
    def __init__(self, *, fetcher: OnChainMinersFetcher, asset: str = "BTC", data_mode: str = "live", is_demo: bool = False) -> None:
        if str(asset).upper() != "BTC":
            raise ValueError("on_chain_miners currently supports asset BTC only")
        if data_mode not in {"live", "synthetic"}:
            raise ValueError("data_mode must be live or synthetic")
        if data_mode == "synthetic" and not is_demo:
            raise ValueError("data_mode=synthetic requires is_demo=True")
        self.fetcher   = fetcher
        self.asset     = "BTC"
        self.data_mode = data_mode
        self.is_demo   = bool(is_demo)

    def build_fetch_plan(self, **kwargs: Any) -> list[dict[str, Any]]:
        return build_on_chain_miners_fetch_plan(**kwargs)

    def run(self, *, mode: str, reference_timestamp: int, existing_contract: Mapping[str, Any] | None = None,
            recovery_requests: Sequence[Mapping[str, Any]] | None = None, include_enrichment: bool = False,
            include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS,
            refresh_catalog: bool = False,
            execution_timestamp: int | None = None) -> dict[str, Any]:
        reference_timestamp = _valid_timestamp(reference_timestamp, "reference_timestamp")
        execution_timestamp = _valid_timestamp(int(time.time()) if execution_timestamp is None else execution_timestamp, "execution_timestamp")
        plan = self.build_fetch_plan(mode=mode, reference_timestamp=reference_timestamp, existing_contract=existing_contract,
                                     recovery_requests=recovery_requests, include_enrichment=include_enrichment,
                                     include_screen_extensions=include_screen_extensions, refresh_catalog=refresh_catalog)
        raw: dict[str, Any] = {}
        entity_payload = None
        for request in plan:
            if request["metric_id"] == "miner_outflow_by_pool":
                raw["miner_outflow_by_pool"] = _execute_outflow_fanout(
                    fetcher=self.fetcher, request=request, entity_payload=entity_payload,
                    existing_contract=resolve_existing_input_state(existing_contract))
            else:
                payload = _execute_metric_request(fetcher=self.fetcher, request=request)
                raw[request["metric_id"]] = payload
                if request["metric_id"] == "miner_entities":
                    entity_payload = payload
        return {"schema": {"id": "trad_elatin.on_chain_miners.extracted_raw.v1", "version": "1.0.0"},
                "family": ON_CHAIN_MINERS_FAMILY, "stage": "extracted_raw", "mode": mode,
                "context": {"asset": self.asset, "data_mode": self.data_mode, "is_demo": self.is_demo, "reference_timestamp": reference_timestamp,
                            "execution_timestamp": execution_timestamp, "requested_at": _iso_utc(execution_timestamp),
                            "include_enrichment": bool(include_enrichment), "include_screen_extensions": bool(include_screen_extensions)}, "raw": raw}

def extract_on_chain_miners_raw(*, fetcher: OnChainMinersFetcher, mode: str, reference_timestamp: int, asset: str = "BTC",
                                existing_contract: Mapping[str, Any] | None = None, recovery_requests: Sequence[Mapping[str, Any]] | None = None,
                                data_mode: str = "live", is_demo: bool = False, include_enrichment: bool = False,
                                include_screen_extensions: bool = DEFAULT_INCLUDE_SCREEN_EXTENSIONS,
                                refresh_catalog: bool = False,
                                execution_timestamp: int | None = None) -> dict[str, Any]:
    extractor = OnChainMinersRawExtractor(fetcher=fetcher, asset=asset, data_mode=data_mode, is_demo=is_demo)
    return extractor.run(mode=mode, reference_timestamp=reference_timestamp, existing_contract=existing_contract,
                         recovery_requests=recovery_requests, include_enrichment=include_enrichment,
                         include_screen_extensions=include_screen_extensions, refresh_catalog=refresh_catalog,
                         execution_timestamp=execution_timestamp)
