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
    contract = deepcopy(contract)
    # Ensure only the final MA package is exposed in Screen A.
    ohlcv = contract.get("charts", {}).get("ohlcv", {})
    for market in ("spot", "futures", "general"):
        timeframes = ohlcv.get("markets", {}).get(market, {}).get("timeframes", {})
        for tf, payload in timeframes.items():
            ma = payload.get("overlays", {}).get("moving_averages", {})
            if isinstance(ma.get("series"), Mapping):
                ma["series"] = {k: v for k, v in ma["series"].items() if k in {"ema_9","ema_21","sma_20","sma_50","wma_20","wma_50"}}
            if isinstance(ma.get("parameters"), Mapping):
                ma["parameters"].update({"ema_periods":[9,21],"sma_periods":[20,50],"wma_periods":[20,50]})
            # Dynamic derived panels are supplied by Processing, never fixture values.
            indicator = processing.get("features", {}).get("indicators", {}).get(market, {}).get(tf, {})
            records = processing.get("features", {}).get("main_ohlcv", {}).get(market, {}).get("timeframes", {}).get(tf, {}).get("records", [])
            timestamps = [r.get("timestamp") for r in records]
            ta = payload.setdefault("technical_fundamental_analysis", {})
            panels = ta.setdefault("panels", {})
            for source_id, target_id, field in (("wasserstein_distance","wasserstein_distance","distance"), ("bollinger_band_width","bollinger_band_width","width")):
                src = indicator.get(source_id, {})
                values = src.get("series", {}).get(field, []) if isinstance(src, Mapping) else []
                n = min(DISPLAY_TAIL, len(timestamps), len(values))
                panels[target_id] = {"status":"available" if n else "unavailable", "timestamps":timestamps[-n:] if n else [],
                                     "series":{target_id:list(values)[-n:] if n else []}, "current":{target_id: next((v for v in reversed(list(values)) if v is not None), None)},
                                     "hmi_recalculate":False, "calculation_owner":"Processing"}
    ta_root = contract.setdefault("technical_analysis", {})
    ta_root["panel_order"] = list(FINAL_PRICE_PANELS)
    ta_root["supported_indicators"] = list(FINAL_PRICE_PANELS)
    if isinstance(ta_root.get("panels"), Mapping):
        ta_root["panels"] = {k:v for k,v in ta_root["panels"].items() if k in FINAL_PRICE_PANELS}
    # Price-vs-VWAP and retired MA values are not part of the final HMI contract.
    return contract


def project_cvd(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    contract = deepcopy(contract)
    source = processing.get("technical_analysis", {})
    ta = deepcopy(source)
    for market in ("spot","futures"):
        for tf, payload in ta.get("markets", {}).get(market, {}).get("timeframes", {}).items():
            timestamps = list(payload.get("timestamps", []) or [])
            n = min(DISPLAY_TAIL, len(timestamps))
            payload["timestamps"] = timestamps[-n:]
            ma = payload.get("overlays", {}).get("moving_averages", {})
            if isinstance(ma.get("series"), Mapping): ma["series"] = {k:_tail(v,n) for k,v in ma["series"].items() if k in {"ema_9","ema_21","sma_20","sma_50","wma_20","wma_50"}}
            payload["events"] = [e for e in payload.get("events", []) if e.get("timestamp") in set(payload["timestamps"])]
            payload["indicators"] = {k:_tail_package(v,n) for k,v in payload.get("indicators", {}).items()}
    ta["analysis_id"] = "cvd_native_orderflow_analysis"
    ta["recalculate_in_hmi"] = False
    contract["technical_analysis"] = ta
    return contract


def project_oi(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    contract = deepcopy(contract)
    contract["schema_version"] = "1.12.0-oi-native-screen-b-demo"
    native = processing.get("native_analysis", {})
    titles = {
        "oi_dynamics":"OI ROC / Slope / Acceleration", "oi_zscore_percentile":"OI Z-Score / Percentile",
        "price_oi_regime":"Price × OI Regime", "price_oi_divergence":"Price ↔ OI Divergence",
        "funding_oi_crowding":"Funding × OI Crowding", "wasserstein_distance":"Wasserstein / Regime Shift",
    }
    charts = contract.setdefault("charts", {})
    for indicator_id, title in titles.items():
        tf_map = {}
        for tf, all_indicators in native.items():
            pkg = _tail_package(all_indicators.get(indicator_id, {}))
            pkg.setdefault("status", "available" if pkg.get("timestamps") else "unavailable")
            pkg.setdefault("reference_lines", [{"value":0.0,"role":"neutral"}])
            tf_map[tf] = pkg
        charts[indicator_id] = {"chart_id":indicator_id,"title":title,"status":"available" if tf_map else "unavailable",
                                "unit":"score","selected_market":"all_exchanges","selected_timeframe":"1h",
                                "markets":{"all_exchanges":tf_map}}
    # Final Screen-A MA surface only.
    for chart_id in ("open_interest_candlestick","open_interest_ohlc"):
        for market in charts.get(chart_id, {}).get("markets", {}).values():
            for payload in market.get("timeframes", {}).values():
                ma = payload.get("overlays", {}).get("moving_averages", {})
                if isinstance(ma.get("series"), Mapping):
                    ma["series"]={k:v for k,v in ma["series"].items() if k in {"ema_9","ema_21","sma_20","sma_50","wma_20","wma_50"}}
    return contract


def project_etf(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    contract = deepcopy(contract)
    contract.setdefault("schema", {})["version"] = "1.4.1-exchange-reserve-realism-v2"
    series = processing.get("series", {})
    # Final Screen A: explicit inflow/outflow/netflow, and reserve as a line rather than fabricated OHLC.
    inflow = {int(r["timestamp"]):r.get("inflow_total") for r in series.get("exchange_inflow", {}).get("day", [])}
    outflow = {int(r["timestamp"]):r.get("outflow_total") for r in series.get("exchange_outflow", {}).get("day", [])}
    netflow = {int(r["timestamp"]):r.get("netflow_total") for r in series.get("exchange_netflow", {}).get("day", [])}
    ts = sorted(set(inflow)|set(outflow)|set(netflow))
    contract.setdefault("charts", {})["exchange_net_flow"] = {"chart_id":"exchange_net_flow","chart_type":"multi_series","status":"available" if ts else "unavailable",
        "series":[{"id":"inflow","label":"Exchange Inflow","points":[{"timestamp":t,"value":inflow.get(t)} for t in ts]},
                  {"id":"outflow","label":"Exchange Outflow","points":[{"timestamp":t,"value":outflow.get(t)} for t in ts]},
                  {"id":"netflow","label":"Exchange Net Flow","points":[{"timestamp":t,"value":netflow.get(t)} for t in ts]}],
        "technical_analysis_allowed":False}
    reserve = series.get("exchange_reserve", {}).get("day", []) or series.get("exchange_reserve", {}).get("hour", [])
    contract["charts"]["exchange_balance"]={"chart_id":"exchange_balance","chart_type":"line","status":"available" if reserve else "unavailable",
        "points":[{"timestamp":r.get("timestamp"),"value":r.get("reserve")} for r in reserve], "technical_analysis_allowed":False}
    contract.pop("technical_analysis", None)
    contract["capital_flow_analysis"] = deepcopy(processing.get("capital_flow_analysis", {"status":"unavailable","indicators":{}}))
    return contract


def _range_packages(timestamps: Sequence[Any], series_map: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    result={}
    for label,count in RANGE_POINTS.items():
        n=min(count,len(timestamps))
        result[label]={"timestamps":list(timestamps)[-n:],"series":{k:list(v)[-n:] for k,v in series_map.items()}}
    return result


def project_miners(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    contract=deepcopy(contract); contract.setdefault("schema", {})["version"]="2.0.0"; native=processing.get("miner_analysis", {}); ts=native.get("timestamps", [])
    titles={
        "miner_reserve_change_zscore":"Miner Reserve Change / Z-Score", "miner_selling_pressure":"MPI / Miner-to-Exchange Pressure",
        "puell_revenue_stress":"Puell Multiple / Revenue Stress", "hashrate_momentum_hash_ribbon":"Hashrate Momentum / Hash Ribbon",
        "hashrate_difficulty_stress":"Hashrate × Difficulty Stress", "miner_capitulation_recovery_regime":"Miner Capitulation / Recovery Regime",
    }
    indicators={}
    for iid,series_map in native.get("indicators", {}).items():
        ranges=_range_packages(ts,series_map); current=_current(series_map)
        indicators[iid]={"indicator_id":iid,"title":titles.get(iid,iid),"status":"available" if ts else "unavailable","unit":"mixed",
                         "hmi_recalculate":False,"series_by_range":ranges,
                         "summary":{"label":titles.get(iid,iid),"display_value":next((v for v in current.values() if v is not None),None),"signal":"neutral","secondary":current}}
    contract["miner_analysis"]={"analysis_id":"native_miner_analysis_vr1","status":"available" if indicators else "unavailable",
                                "hmi_computes_market_indicators":False,"processing_outputs_required":list(titles),
                                "default_selected":"miner_reserve_change_zscore","indicators":indicators,
                                "weights":deepcopy(native.get("weights", {}))}
    return contract


def project_liquidations(contract: dict[str, Any], processing: Mapping[str, Any]) -> dict[str, Any]:
    contract=deepcopy(contract); contract["contract_version"]="1.3.0-native-liquidations-b"; contract.setdefault("screen_layout", {}); native=processing.get("liquidation_analysis", {}); timestamps=native.get("timestamps", [])
    pos=native.get("positioning", {}); n=min(DISPLAY_TAIL,len(timestamps)); ts=list(timestamps)[-n:]
    contract.setdefault("charts", {})["long_short_positioning"]={"chart_id":"long_short_positioning","title":"Long / Short Positioning","status":"available" if ts else "partial",
        "points":[{"timestamp":t,"top_position_ratio":_tail(pos.get("top_position_ratio",[]),n)[i],
                   "top_account_ratio":_tail(pos.get("top_account_ratio",[]),n)[i],"global_account_ratio":_tail(pos.get("global_account_ratio",[]),n)[i]} for i,t in enumerate(ts)],
        "neutral_line":1.0}
    points_by_indicator={}
    for iid,series_map in native.get("indicators", {}).items():
        arrays={k:_tail(v,n) for k,v in series_map.items()}
        points=[]
        for i,t in enumerate(ts): points.append({"timestamp":t, **{k:(v[i] if i<len(v) else None) for k,v in arrays.items()}})
        points_by_indicator[iid]={"title":iid.replace('_',' ').title(),"status":"available" if points else "unavailable","points":points,
                                  "hmi_recalculate":False}
    contract["liquidation_analysis"]={"status":"available" if points_by_indicator else "unavailable","data_mode":"runtime_processing",
                                      "processing_contract_target":True,"real_market_calculation":True,"hmi_recalculate":False,
                                      "indicator_order":["liquidation_intensity_zscore","long_short_liquidation_imbalance","cascade_acceleration","price_liquidation_regime","crowding_liquidation_pressure","liquidation_regime_hmi"],
                                      "indicators":points_by_indicator}
    # Side-panel current values without inventing missing auxiliary positioning sources.
    current=native.get("current", {})
    items=contract.setdefault("side_panel", {}).setdefault("items", [])
    by_id={item.get("id"):item for item in items if isinstance(item,Mapping)}
    for iid,value in (("top_position_ls_ratio",current.get("top_position_ratio")),("liquidation_regime",current.get("liquidation_regime_score"))):
        row=by_id.get(iid,{"id":iid,"label":iid.replace('_',' ').upper()}); row.update({"value":value,"status":"available" if value is not None else "partial"})
        if iid not in by_id: items.append(row)
    return contract


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
    contract=deepcopy(contract); contract.setdefault("schema", {})["version"]="1.4.0"; views={}
    prebuilt=contract.pop("_prebuilt_market_views", {})
    for market in ("spot","perpetual"):
        hist=processing.get("market_histories", {}).get(market, {}).get("records", [])
        native=processing.get("liquidity_analysis", {}).get(market, {})
        prepared=deepcopy(prebuilt.get(market, {})) if isinstance(prebuilt, Mapping) else {}
        view={"kpis":prepared.get("kpis", {}),"charts":prepared.get("charts", {}),"tables":prepared.get("tables", {}),"widgets":prepared.get("widgets", {}),
              "liquidity_analysis":_liquidity_analysis_payload(native),"context":{"market":market}}
        # Prefer already built market-specific raw components when present in Processing.
        # Executed Operations is deliberately independent from Large Trades.
        view["charts"]["executed_operations"]=_executed_operations(hist)
        views[market]=view
    contract["market_views"]=views
    contract.setdefault("selectors", {}).setdefault("market", {}).update({"visible":True,"options":["spot","perpetual"],"behavior":"runtime_market_view"})
    contract["technical_analysis"]={"enabled":False,"applies":False,"screen_b":True,"reason":"native_liquidity_analysis_is_used_instead_of_generic_technical_analysis",
                                     "hmi_must_not_render_generic_technical_indicators":True,"hmi_recalculation":False}
    # Root native analysis is a fallback only; selected market_views are canonical to Screens.
    contract["liquidity_analysis"]=_liquidity_analysis_payload(processing.get("liquidity_analysis", {}).get("spot", {}))
    return contract


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
