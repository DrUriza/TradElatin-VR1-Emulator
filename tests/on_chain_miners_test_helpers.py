import copy
import json
import math
from collections.abc import Mapping, Sequence

from processing_signals.input.on_chain_miners.on_chain_miners_data_raw_extract import (
    COLLECTION_EXTENSION_IDS,
    CORE_METRIC_IDS,
    ENRICHMENT_METRIC_IDS,
    SCREEN_EXTENSION_METRIC_IDS,
    TIME_SERIES_EXTENSION_IDS,
    UTXO_AGE_BANDS,
    build_on_chain_miners_fetch_plan,
    extract_on_chain_miners_raw,
    is_validated_miner_flag,
    resolve_existing_input_state,
    validated_miner_symbols,
)
from processing_signals.input.on_chain_miners.on_chain_miners_data_raw_preprocessing import (
    normalize_optional_finite_number,
    preprocess_miner_entities,
    preprocess_miner_outflow_by_pool,
    preprocess_on_chain_metric,
    run_on_chain_miners_input,
    upsert_on_chain_records,
)


DAY = 86_400
NOW = 1_785_110_400


def responses(null_sopr=False):
    dates = ["2026-07-26", "2026-07-25"]
    utxo_record = {"date": dates[0]}
    for index, band in enumerate(UTXO_AGE_BANDS, 1):
        utxo_record.update({f"range_{band}": float(index * 100), f"range_{band}_usd": float(index * 100_000),
                            f"range_{band}_percent": index / 100})
    payloads = {
        "balance_miners_sum": [{"t": NOW - DAY, "v": 1872611.91}, {"t": NOW, "v": 1874853.91}],
        "hash_rate_mean": [{"t": NOW - DAY, "v": 682900000000000000000}, {"t": NOW, "v": 684200000000000000000}],
        "sopr": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "sopr": None if null_sopr else 1.036, "a_sopr": 1.029, "sth_sopr": 1.018, "lth_sopr": 1.091},
            {"date": dates[1], "sopr": 1.031, "a_sopr": 1.027, "sth_sopr": 1.012, "lth_sopr": 1.087}]}},
        "difficulty": {"status": {"code": "200", "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "difficulty": 94600000000000.0}, {"date": dates[1], "difficulty": 94450000000000.0}]}},
        "mpi": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "mpi": 1.42}, {"date": dates[1], "mpi": -0.18}]}},
        "puell_multiple": {"code": "0", "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "puell_multiple": 1.35}]},
        "bitcoin_sth_sopr": {"code": 0, "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "sth_sopr": 1.02}]},
        "bitcoin_lth_sopr": {"code": 200, "msg": "success", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "lth_sopr": 1.08}]},
        "bitcoin_nupl": {"code": "0", "data": [{"timestamp": NOW * 1000, "price": 119100.5, "net_unpnl": 0.61}]},
        "miner_entity_list": {"status": {"code": 200, "message": "success"}, "result": {"type": "miner", "data": [
            {"name": "F2Pool", "symbol": "f2pool", "is_validated": 1, "market_type": 0},
            {"name": "AntPool", "symbol": "antpool", "is_validated": 1, "market_type": 0},
            {"name": "Unknown", "symbol": "unknown", "is_validated": 0, "market_type": 0}]}},
        "miner_outflow": {symbol: {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [
            {"date": dates[0], "outflow_total": 1245.4, "outflow_top10": 981.2, "outflow_mean": 3.48},
            {"date": dates[1], "outflow_total": 1102.8, "outflow_top10": 810.3, "outflow_mean": 3.11}]}}
                          for symbol in ("antpool", "f2pool")},
        "miners_unspent_supply": [{"t": NOW - DAY, "v": 1_872_611.91}, {"t": NOW, "v": 1_874_853.91}],
        "utxo_age_distribution": {"status": {"code": 200, "message": "success"}, "result": {"window": "day", "data": [utxo_record]}},
        "revenue_sum": [{"t": NOW - DAY, "v": 40_000_000.0}, {"t": NOW, "v": 41_000_000.0}],
        "volume_mined_sum": [{"t": NOW - DAY, "v": 38_000_000.0}, {"t": NOW, "v": 39_000_000.0}],
        "revenue_from_fees": [{"t": NOW - DAY, "v": 0.05}, {"t": NOW, "v": 0.048}],
    }
    return payloads


class FakeFetcher:
    def __init__(self, payloads=None, failing=()):
        self.payloads = payloads or responses()
        self.failing = set(failing)
        self.calls = []

    def __call__(self, **request):
        self.calls.append(request)
        if request["endpoint_id"] in self.failing:
            raise RuntimeError("provider unavailable")
        if request["endpoint_id"] == "miner_outflow":
            return self.payloads["miner_outflow"][request["params"]["miner"]]
        return self.payloads[request["endpoint_id"]]


def plan(**kwargs):
    return build_on_chain_miners_fetch_plan(mode="bootstrap", reference_timestamp=NOW, **kwargs)


def output(**kwargs):
    return run_on_chain_miners_input(fetcher=kwargs.pop("fetcher", FakeFetcher()), reference_timestamp=NOW,
                                     execution_timestamp=kwargs.pop("execution_timestamp", NOW + 3_600), **kwargs)


def existing_contract():
    base = output()
    for series in base["series"].values():
        series["records"].insert(0, {**series["records"][0], "timestamp": series["records"][0]["timestamp"] - 10 * DAY})
    return base


def _daily_glassnode_raw(start, end, missing=()):
    records = [{"t": timestamp, "v": float(index + 1)} for index, timestamp in enumerate(range(start, end + DAY, DAY)) if timestamp not in missing]
    return {"status": "ok", "response": records, "from_timestamp": start, "to_timestamp": end}


def _collect_keys(value):
    keys = set()
    if isinstance(value, Mapping):
        keys.update(value)
        for item in value.values():
            keys.update(_collect_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


class PoolFailingFetcher(FakeFetcher):
    def __call__(self, **request):
        if request["endpoint_id"] == "miner_outflow" and request["params"]["miner"] == "f2pool":
            self.calls.append(request)
            raise RuntimeError("pool unavailable")
        return super().__call__(**request)


