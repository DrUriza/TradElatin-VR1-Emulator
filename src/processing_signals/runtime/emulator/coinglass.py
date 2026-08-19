from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .common import filter_scalar_records
from .fixture_store import FixtureStore


def _cg_data(payload: Mapping[str, Any], rows: list[Any]) -> dict[str, Any]:
    out = deepcopy(dict(payload))
    out["data"] = rows
    return out


def fetch(store: FixtureStore, family: str, *, endpoint_id: str, path: str,
          params: Mapping[str, Any], request: Mapping[str, Any]) -> Any:
    root = store.family_root("coinglass", family)

    if family == "prices_ohlcv":
        rel = "spot_ohlcv" if endpoint_id == "spot_ohlcv" else "futures_ohlcv"
        tf = str(params["interval"])
        payload = store.load("coinglass", family, rel, f"{tf}_raw.json")
        rows = filter_scalar_records(list(payload.get("data", [])), start=params.get("start_time"),
                                     end=params.get("end_time"), time_key="time", time_is_ms=True,
                                     limit=int(params.get("limit", len(payload.get("data", [])))))
        return _cg_data(payload, rows)

    if family == "etf_exchange_flows":
        return store.load("coinglass", family, endpoint_id, "raw.json")

    if family == "liquidity_microstructure":
        dims = request.get("dimensions") or {}
        market = str(dims.get("market_type", ""))
        tf = str(dims.get("timeframe", params.get("interval", "")))
        if endpoint_id in {"spot_orderbook_heatmap", "perpetual_orderbook_heatmap"}:
            payload = store.load("coinglass", family, "orderbook_conventional", market, f"{tf}_raw.json")
            rows = list(payload.get("data", []))
            limit = int(params.get("limit", len(rows)))
            return _cg_data(payload, rows[-limit:])
        if endpoint_id in {"spot_order_depth", "perpetual_order_depth"}:
            rp = int(dims.get("range_percent", params.get("range", 1)))
            payload = store.load("coinglass", family, "market_depth", market, tf, f"range_{rp}_raw.json")
            rows = filter_scalar_records(list(payload.get("data", [])), start=params.get("start_time"),
                                         end=params.get("end_time"), time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", len(payload.get("data", [])))))
            return _cg_data(payload, rows)
        if endpoint_id in {"spot_footprint", "perpetual_footprint"}:
            payload = store.load("coinglass", family, "footprint", market, str(params["exchange"]), f"{params['interval']}_raw.json")
            rows = list(payload.get("data", []))
            start = params.get("start_time"); end = params.get("end_time")
            if start is not None:
                rows = [row for row in rows if int(row[0]) * 1000 >= int(start)]
            if end is not None:
                rows = [row for row in rows if int(row[0]) * 1000 <= int(end)]
            return _cg_data(payload, rows[-int(params.get("limit", len(rows))):])
        if endpoint_id in {"spot_large_limit_orders", "perpetual_large_limit_orders"}:
            return store.load("coinglass", family, "large_limit_orders", market, "raw.json")
        if endpoint_id == "whale_index":
            payload = store.load("coinglass", family, "whale_index", f"{tf}_raw.json")
            rows = filter_scalar_records(list(payload.get("data", [])), start=params.get("start_time"),
                                         end=params.get("end_time"), time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", len(payload.get("data", [])))))
            return _cg_data(payload, rows)
        raise ValueError(f"unsupported CoinGlass liquidity endpoint: {endpoint_id}")

    if family == "long_short_liquidations":
        if endpoint_id in {"top_position_long_short_ratio", "top_account_long_short_ratio", "global_account_long_short_ratio"}:
            folder = {
                "top_position_long_short_ratio": "top_position_long_short_ratio",
                "top_account_long_short_ratio": "top_account_long_short_ratio",
                "global_account_long_short_ratio": "global_account_long_short_ratio",
            }[endpoint_id]
            by_time: dict[int, dict[str, Any]] = {}
            for file in store.glob("coinglass", family, f"{folder}/1h/page_*_raw.json"):
                payload = store.load("coinglass", family, *file.relative_to(root).parts)
                for row in payload.get("data", []):
                    by_time[int(row["time"])] = row
            rows = [by_time[key] for key in sorted(by_time)]
            rows = filter_scalar_records(rows, start=params.get("start_time"), end=params.get("end_time"),
                                         time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", len(rows))), newest=False)
            return {"code": "0", "msg": "success", "data": rows}
        if endpoint_id == "supported_exchange_pairs":
            return store.load("coinglass", family, "supported_exchange_pairs", "raw.json")
        if endpoint_id == "aggregated_liquidation_history":
            return store.load("coinglass", family, "aggregated_liquidation_history", f"{params['interval']}_raw.json")
        if endpoint_id == "liquidation_exchange_list":
            return store.load("coinglass", family, "liquidation_exchange_list", f"{params['range']}_raw.json")
        if endpoint_id == "pair_liquidation_history":
            return store.load("coinglass", family, "pair_liquidation_history", str(params["exchange"]), f"{params['interval']}_raw.json")
        if endpoint_id == "liquidation_order_events":
            payload = store.load("coinglass", family, "liquidation_order_events", str(params["exchange"]), "24h_raw.json")
            rows = filter_scalar_records(list(payload.get("data", [])), start=params.get("start_time"),
                                         end=params.get("end_time"), time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", len(payload.get("data", [])))))
            return _cg_data(payload, rows)
        if endpoint_id == "aggregated_liquidation_map":
            return store.load("coinglass", family, "aggregated_liquidation_map", "1d_raw.json")
        if endpoint_id == "pair_liquidation_map":
            return store.load("coinglass", family, "pair_liquidation_map", str(params["exchange"]), "1d_raw.json")
        if endpoint_id == "liquidation_max_pain":
            return store.load("coinglass", family, "liquidation_max_pain", "24h_raw.json")
        raise ValueError(f"unsupported CoinGlass liquidation endpoint: {endpoint_id}")

    if family == "on_chain_miners":
        if endpoint_id != "bitcoin_nupl":
            raise ValueError(f"unsupported CoinGlass on-chain endpoint: {endpoint_id}")
        return store.load("coinglass", family, "bitcoin_nupl", "day_raw.json")

    if family == "open_interest_and_funding":
        if endpoint_id in {"aggregated_open_interest_ohlc", "oi_weighted_funding_rate_ohlc"}:
            tf = str(params["interval"])
            payload = store.load("coinglass", family, endpoint_id, f"{tf}_raw.json")
            rows = filter_scalar_records(list(payload.get("data", [])), start=params.get("start_time"),
                                         end=params.get("end_time"), time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", len(payload.get("data", [])))))
            return _cg_data(payload, rows)
        if endpoint_id in {"open_interest_exchange_list", "funding_rate_exchange_list", "options_info"}:
            return store.load("coinglass", family, endpoint_id, "raw.json")
        raise ValueError(f"unsupported CoinGlass open-interest endpoint: {endpoint_id}")

    if family == "cvd_volume_orderflow":
        if endpoint_id in {"spot_aggregated_cvd", "futures_aggregated_cvd"}:
            market = "spot" if endpoint_id.startswith("spot_") else "futures"
            tf = str(params["interval"])
            by_time: dict[int, dict[str, Any]] = {}
            pattern = f"aggregated_cvd/{market}/{tf}/page_*_raw.json"
            for file in store.glob("coinglass", family, pattern):
                payload = store.load("coinglass", family, *file.relative_to(root).parts)
                for row in payload.get("data", []):
                    by_time[int(row["time"])] = row
            rows = [by_time[key] for key in sorted(by_time)]
            rows = filter_scalar_records(rows, start=params.get("start_time"), end=params.get("end_time"),
                                         time_key="time", time_is_ms=True,
                                         limit=int(params.get("limit", 1000)))
            return {"code": "0", "data": rows}
        if endpoint_id in {"spot_footprint", "futures_footprint"}:
            market = "spot" if endpoint_id.startswith("spot_") else "futures"
            payload = store.load("coinglass", family, "footprint", market, str(params["exchange"]), f"{params['interval']}_raw.json")
            rows = list(payload.get("data", []))
            start = params.get("start_time")
            end = params.get("end_time")
            if start is not None:
                rows = [row for row in rows if int(row[0]) * 1000 >= int(start)]
            if end is not None:
                rows = [row for row in rows if int(row[0]) * 1000 <= int(end)]
            return _cg_data(payload, rows[-int(params.get("limit", len(rows))):])
        raise ValueError(f"unsupported CoinGlass CVD endpoint: {endpoint_id}")

    raise ValueError(f"unsupported CoinGlass family: {family}")
