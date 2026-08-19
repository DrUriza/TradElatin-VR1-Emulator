"""Final Screens VR1 projections for the seven integrated families.

This module is intentionally part of Contract Builder.  It contains no market
calculations and never imports the Screens repository.  Processing remains the
owner of dynamic values; this module only maps precomputed outputs into the
frozen HMI consumer surface.

Volatility is intentionally excluded from this integration phase.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

DISPLAY_TAIL = 220
RANGE_POINTS = {"30D": 30, "90D": 90, "180D": 180, "360D": 360}
FINAL_PRICE_PANELS = ["macd", "rsi", "tsi", "adx", "stochastic", "williams_r", "cci", "atr", "mfi", "wasserstein_distance", "bollinger_band_width"]


def _tail(values: Sequence[Any] | None, n: int = DISPLAY_TAIL) -> list[Any]:
    return list(values or [])[-n:]


def _tail_package(package: Mapping[str, Any], n: int = DISPLAY_TAIL) -> dict[str, Any]:
    result = deepcopy(dict(package))
    ts = list(result.get("timestamps", []) or [])
    if ts:
        n = min(n, len(ts))
        result["timestamps"] = ts[-n:]
        series = result.get("series")
        if isinstance(series, Mapping):
            result["series"] = {k: _tail(v, n) if isinstance(v, Sequence) and not isinstance(v, (str, bytes, bytearray)) else deepcopy(v) for k, v in series.items()}
    return result


def _current(series_map: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, values in series_map.items():
        value = next((v for v in reversed(list(values or [])) if v is not None), None)
        out[key] = value
    return out


def project_prices(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """The family Contract Builder already emits the frozen Screens surface."""
    del processing
    return deepcopy(contract)


def project_cvd(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """The family Contract Builder already emits the frozen Screens surface."""
    del processing
    return deepcopy(contract)


def project_oi(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """OI v1.12 is already projected by its canonical Contract Builder adapter.

    Keep this hook as an explicit no-op so the shared final projection layer does
    not mutate or widen the frozen Screens surface a second time.
    """
    del processing
    return deepcopy(contract)


def project_etf(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """ETF v1.4 is already projected by its canonical Contract Builder adapter."""
    del processing
    return deepcopy(contract)


def _range_packages(timestamps: Sequence[Any], series_map: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    result={}
    for label,count in RANGE_POINTS.items():
        n=min(count,len(timestamps))
        result[label]={"timestamps":list(timestamps)[-n:],"series":{k:list(v)[-n:] for k,v in series_map.items()}}
    return result


def project_miners(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """On-Chain v2.0 is already projected by its canonical Contract Builder adapter."""
    del processing
    return deepcopy(contract)


def project_liquidations(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """Liquidations v1.3 is already canonical; never project positioning twice."""
    del processing
    return deepcopy(contract)

def _executed_operations(records: Sequence[Mapping[str, Any]], n:int=240) -> dict[str, Any]:
    rows=list(records or [])[-n:]
    return {"chart_id":"executed_operations","status":"available" if rows else "unavailable","timestamps":[r.get("timestamp") for r in rows],
            "buy_executed":[r.get("large_trade_buy_notional",0.0) or 0.0 for r in rows],
            "sell_executed":[r.get("large_trade_sell_notional",0.0) or 0.0 for r in rows],
            "net_pressure":[r.get("large_trade_delta",0.0) or 0.0 for r in rows]}


def _liquidity_analysis_payload(native: Mapping[str, Any], n:int=730) -> dict[str, Any]:
    timestamps=list(native.get("timestamps", []) or []); n=min(n,len(timestamps)); ts=timestamps[-n:]
    indicators=native.get("indicators", {})
    records=[]
    for i,t in enumerate(ts):
        source_index=len(timestamps)-n+i; row={"timestamp":t}
        for package in indicators.values():
            if not isinstance(package,Mapping): continue
            for key,values in package.items():
                if isinstance(values,list) and source_index < len(values): row[key]=values[source_index]
        hmi=row.get("liquidity_hmi_score")
        row["liquidity_regime"]="DEEP / BALANCED" if hmi is None or abs(float(hmi))<0.5 else ("ROBUST" if float(hmi)>0 else "STRESSED")
        records.append(row)
    return {"status":native.get("status","available" if records else "unavailable"),"data_mode":"runtime_processing","processing_contract_target":True,
            "real_market_calculation":True,"hmi_recalculate":False,"history_resolution":"1h","record_count":len(records),"display_tail_records":240,
            "records":records,"current":deepcopy(native.get("current", {})),
            "selector_contract":{"indicator_order":["depth_imbalance_pressure","spread_market_impact_stress","liquidity_wall_concentration_vacuum","whale_persistence_cancellation","executed_liquidity_absorption","liquidity_regime_hmi"]}}


def project_liquidity(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    """Liquidity v1.4 is already canonical in its family Contract Builder.

    Keep this shared hook as an explicit no-op so the final projection layer
    cannot mutate market views or native Screen-B analysis a second time.
    """
    del processing
    return deepcopy(contract)


PROJECTORS = {
    "prices_ohlcv": project_prices,
    "cvd_volume_orderflow": project_cvd,
    "open_interest_and_funding": project_oi,
    "etf_exchange_flows": project_etf,
    "on_chain_miners": project_miners,
    "long_short_liquidations": project_liquidations,
    "liquidity_microstructure": project_liquidity,
}


def project_final_screen_contract(family: str, contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    projector=PROJECTORS.get(family)
    return projector(contract,processing) if projector else contract
