"""Classification v0.1 for Liquidity Microstructure Processing."""

# ruff: noqa: E701, E702

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
from typing import Any

from .liquidity_microstructure_rules import (
    CLASSIFICATION_RULE_VERSION, CLASSIFICATION_VERSION, LARGE_TRADE_WINDOWS, MARKETS, TIMEFRAMES,
    classify_comparison, classify_cross_market_execution, classify_execution, classify_imbalance, classify_impact,
    classify_market_change, classify_pressure_alignment, classify_spread, classify_trade_window, classify_whale, validate_thresholds,
)

STATUSES = {"available", "partial", "unavailable", "invalid"}


def _find_invalid_json_path(value: Any, path: str) -> tuple[str, str] | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return "non_string_key", path
            found = _find_invalid_json_path(item, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            found = _find_invalid_json_path(item, f"{path}[{index}]")
            if found is not None:
                return found
    elif isinstance(value, float):
        if not math.isfinite(value) or (value == 0 and math.copysign(1, value) < 0):
            return "invalid_float", path
    elif value is not None and not isinstance(value, (str, int, bool)):
        return "non_json_value", path
    return None


def _validate_json(value: Any, path: str = "root") -> None:
    """Validate JSON values without materializing a path for every valid node."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            if any(not isinstance(key, str) for key in current):
                kind, found = _find_invalid_json_path(value, path) or ("non_string_key", path)
                raise ValueError(f"{kind}:{found}")
            stack.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            stack.extend(current)
        elif isinstance(current, float):
            if not math.isfinite(current) or (current == 0 and math.copysign(1, current) < 0):
                kind, found = _find_invalid_json_path(value, path) or ("invalid_float", path)
                raise ValueError(f"{kind}:{found}")
        elif current is not None and not isinstance(current, (str, int, bool)):
            kind, found = _find_invalid_json_path(value, path) or ("non_json_value", path)
            raise ValueError(f"{kind}:{found}")


def _timestamp(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid_timestamp:{path}")
    return value


def _status(node: Mapping[str, Any], path: str) -> str:
    status = node.get("status")
    if status not in STATUSES:
        raise ValueError(f"invalid_status:{path}")
    reason = node.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError(f"invalid_reason:{path}")
    return status


def validate_liquidity_microstructure_processing(processing_contract: Mapping[str, Any]) -> None:
    if not isinstance(processing_contract, Mapping) or processing_contract.get("family") != "liquidity_microstructure":
        raise ValueError("invalid_processing_family")
    if processing_contract.get("stage") != "processing" or processing_contract.get("mode") not in {"bootstrap", "incremental", "recovery"}:
        raise ValueError("invalid_processing_stage_or_mode")
    _timestamp(processing_contract.get("reference_timestamp"), "reference_timestamp")
    _timestamp(processing_contract.get("execution_timestamp"), "execution_timestamp")
    for key in ("configuration", "context", "source_selection", "markets", "whale_activity", "market_history", "comparison", "features", "quality"):
        if not isinstance(processing_contract.get(key), Mapping):
            raise ValueError(f"invalid_processing_mapping:{key}")
    markets = processing_contract["markets"]
    if set(markets) != set(MARKETS):
        raise ValueError("invalid_processing_markets")
    for market in MARKETS:
        for feature in ("orderbook", "order_depth", "large_trades"):
            _status(markets[market][feature], f"markets.{market}.{feature}")
        for feature in ("orderbook", "order_depth"):
            nodes = markets[market][feature].get("timeframes")
            if not isinstance(nodes, Mapping) or set(nodes) != set(TIMEFRAMES):
                raise ValueError(f"invalid_processing_timeframes:{market}.{feature}")
            for timeframe, node in nodes.items():
                _status(node, f"markets.{market}.{feature}.{timeframe}")
        windows = markets[market]["large_trades"].get("windows")
        if not isinstance(windows, Mapping) or not set(LARGE_TRADE_WINDOWS).issubset(windows):
            raise ValueError(f"invalid_trade_windows:{market}")
    _status(processing_contract["whale_activity"], "whale_activity")
    _status(processing_contract["market_history"], "market_history")
    if processing_contract["quality"].get("status") not in {"ok", "partial", "invalid"}:
        raise ValueError("invalid_processing_quality")
    _validate_json(processing_contract)


def _classify_orderbook(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    source_status, current = node["status"], node.get("current")
    if not isinstance(current, Mapping):
        node["classification"] = {"spread_condition": classify_spread(None, source_status=source_status, thresholds=thresholds),
                                  "execution_liquidity_state": classify_execution(
                                      classify_spread(None, source_status=source_status, thresholds=thresholds),
                                      classify_impact(None, source_status=source_status, fully_filled=False, thresholds=thresholds), source_timestamp=None)}
        return
    timestamp = current["timestamp"]
    spread = classify_spread(current.get("spread_bps"), source_status=source_status, source_timestamp=timestamp, thresholds=thresholds)
    impact_node = current.get("market_impact", {})
    impacts = {}
    for side in ("buy", "sell"):
        side_node = impact_node.get(side, {})
        impacts[side] = classify_impact(side_node.get("impact_bps"), source_status=side_node.get("status", impact_node.get("status", source_status)),
                                        source_timestamp=timestamp, fully_filled=side_node.get("fully_filled", False), thresholds=thresholds)
    worst = classify_impact(impact_node.get("worst_side_impact_bps"), source_status=impact_node.get("status", source_status),
                            source_timestamp=timestamp, fully_filled=impact_node.get("worst_side_impact_bps") is not None, thresholds=thresholds)
    full = current.get("bands", {}).get("full_visible_book", {})
    balances = {basis: classify_imbalance(full.get(basis, {}).get("imbalance_percent"), source_status=full.get(basis, {}).get("status", source_status),
                                          source_timestamp=timestamp, basis=basis, thresholds=thresholds)
                for basis in ("quote_notional", "base_quantity")}
    node["classification"] = {"spread_condition": spread, "market_impact": {**impacts, "worst_side": worst},
                              "orderbook_balance": {"primary_basis": "quote_notional", **balances},
                              "execution_liquidity_state": classify_execution(spread, worst, source_timestamp=timestamp)}


def _classify_depth(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    for collection in ("direct_ranges", "derived_bands"):
        for row in node[collection]:
            timestamp, source_status = row["timestamp"], row.get("status", node["status"])
            row["classification"] = {basis: classify_imbalance(row.get(basis, {}).get("imbalance_percent"),
                                                                source_status=row.get(basis, {}).get("status", source_status),
                                                                source_timestamp=timestamp, basis=basis, thresholds=thresholds)
                                         for basis in ("quote_notional", "base_quantity")}
    reference = next((row for row in reversed(node["direct_ranges"]) if row["range_percent"] == 10), None)
    node["classification"] = {"primary_depth_basis": "quote_notional", "primary_depth_reference": "range_10",
                              "reference_range_percent": 10,
                              "reference_balance": reference.get("classification", {}).get("quote_notional") if reference else None}


def _classify_trades(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    coverage = node.get("coverage", {})
    node["classification"] = {window: classify_trade_window(node["windows"][window], source_status=node["status"],
                                                             source_timestamp=node["windows"][window].get("last_event_timestamp"),
                                                             coverage_complete=bool(coverage.get("coverage_complete")), thresholds=thresholds)
                              for window in LARGE_TRADE_WINDOWS}


def _classify_whale(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    for timeframe, timeframe_node in node["timeframes"].items():
        statistics = timeframe_node.get("statistics", {})
        timeframe_node["classification"] = classify_whale(statistics.get("rolling_z_score_20"), source_status=statistics.get("status", timeframe_node["status"]),
                                                            source_timestamp=(timeframe_node.get("current") or {}).get("timestamp"),
                                                            reason=statistics.get("reason"), thresholds=thresholds)


def _classify_history(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    for window, change in node["changes"].items():
        change["classification"] = classify_market_change(change.get("change_percent"), source_status=change["status"],
                                                           source_timestamp=change.get("source_timestamp"), thresholds=thresholds)


def _classify_comparisons(node: dict[str, Any], thresholds: Mapping[str, float]) -> None:
    for row in node.get("order_depth", []):
        row["classification"] = {
            "depth_quote": classify_comparison(row.get("perpetual_to_spot_total_depth_ratio_quote"), kind="depth_quote", source_timestamp=row["timestamp"], thresholds=thresholds),
            "depth_base": classify_comparison(row.get("perpetual_to_spot_total_depth_ratio_base"), kind="depth_base", source_timestamp=row["timestamp"], thresholds=thresholds)}
    for row in node.get("orderbook", []):
        row["classification"] = {
            "spread": classify_comparison(row.get("spread_difference_bps"), kind="spread", source_timestamp=row["timestamp"], thresholds=thresholds),
            "buy_impact": classify_comparison(row.get("buy_impact_difference_bps"), kind="buy_impact", source_timestamp=row["timestamp"], thresholds=thresholds),
            "sell_impact": classify_comparison(row.get("sell_impact_difference_bps"), kind="sell_impact", source_timestamp=row["timestamp"], thresholds=thresholds)}


def _invalid_output(source: Mapping[str, Any], thresholds: Mapping[str, float], execution: int) -> dict[str, Any]:
    return {"family": "liquidity_microstructure", "stage": "classification", "mode": source["mode"],
            "reference_timestamp": source["reference_timestamp"], "source_execution_timestamp": source["execution_timestamp"],
            "execution_timestamp": execution, "classification_version": CLASSIFICATION_VERSION,
            "classification_rule_version": CLASSIFICATION_RULE_VERSION, "context": deepcopy(source["context"]),
            "configuration": {"thresholds": dict(thresholds), "calibration_status": "provisional_coinglass_only"},
            "source_selection": deepcopy(source["source_selection"]), "markets": {}, "whale_activity": {}, "market_history": {},
            "comparison": {"spot_perpetual": {}}, "summary": {"observed_liquidity": {}},
            "quality": {"status": "invalid", "reason": "processing_quality_invalid", "required_groups": [], "optional_groups": [],
                        "available_groups": [], "partial_groups": [], "unavailable_groups": [], "invalid_groups": [], "warnings": [], "errors": []}}


def classify_liquidity_microstructure(processing_contract: Mapping[str, Any], *, config: Mapping[str, Any] | None = None,
                                      now_timestamp: int | None = None) -> dict[str, Any]:
    validate_liquidity_microstructure_processing(processing_contract)
    # Processing is an external read-only input.  Copy only the branches that
    # Classification enriches instead of cloning the entire 50+ MB contract and
    # then cloning those same branches a second time below.
    thresholds, source = validate_thresholds(config), processing_contract
    execution = source["execution_timestamp"] if now_timestamp is None else _timestamp(now_timestamp, "now_timestamp")
    if source["quality"]["status"] == "invalid":
        return _invalid_output(source, thresholds, execution)
    markets = deepcopy(source["markets"])
    for market in MARKETS:
        for timeframe in TIMEFRAMES:
            _classify_orderbook(markets[market]["orderbook"]["timeframes"][timeframe], thresholds)
            _classify_depth(markets[market]["order_depth"]["timeframes"][timeframe], thresholds)
        _classify_trades(markets[market]["large_trades"], thresholds)
        summaries = {}
        for timeframe in TIMEFRAMES:
            orderbook = markets[market]["orderbook"]["timeframes"][timeframe]["classification"]
            trade = markets[market]["large_trades"]["classification"][timeframe]
            alignment = classify_pressure_alignment(orderbook.get("orderbook_balance", {}).get("quote_notional", {}).get("state", "unavailable"),
                                                    trade["state"], source_timestamp=trade.get("source_timestamp"))
            summaries[timeframe] = {"market_type": market, "timeframe": timeframe,
                                    "execution_liquidity_state": orderbook["execution_liquidity_state"]["state"],
                                    "balance_state": orderbook.get("orderbook_balance", {}).get("quote_notional", {}).get("state", "unavailable"),
                                    "reference_depth_balance_state": (markets[market]["order_depth"]["timeframes"][timeframe]["classification"].get("reference_balance") or {}).get("state", "unavailable"),
                                    "large_trade_pressure_state": trade["state"], "pressure_alignment_state": alignment["state"],
                                    "pressure_alignment": alignment, "provisional": any(item.get("provisional") for item in (trade, alignment))}
        markets[market]["summary"] = summaries
    whale, history, comparison = deepcopy(source["whale_activity"]), deepcopy(source["market_history"]), deepcopy(source["comparison"])
    _classify_whale(whale, thresholds); _classify_history(history, thresholds); _classify_comparisons(comparison["spot_perpetual"], thresholds)
    cross = {timeframe: classify_cross_market_execution(markets["spot"]["summary"][timeframe]["execution_liquidity_state"],
                                                        markets["perpetual"]["summary"][timeframe]["execution_liquidity_state"],
                                                        source_timestamp=source["reference_timestamp"]) for timeframe in TIMEFRAMES}
    required = {f"markets.{market}.{feature}": markets[market][feature]["status"] for market in MARKETS for feature in ("orderbook", "order_depth", "large_trades")}
    required.update({"whale_activity": whale["status"]})
    optional = {"market_history": history["status"]}
    invalid = [key for key, value in required.items() if value == "invalid"]
    partial = [key for key, value in required.items() if value == "partial"]
    unavailable = [key for key, value in required.items() if value == "unavailable"]
    available = [key for key, value in required.items() if value == "available"]
    quality = "invalid" if invalid else ("partial" if partial or unavailable else "ok")
    output = {"family": "liquidity_microstructure", "stage": "classification", "mode": source["mode"],
              "reference_timestamp": source["reference_timestamp"], "source_execution_timestamp": source["execution_timestamp"],
              "execution_timestamp": execution, "classification_version": CLASSIFICATION_VERSION,
              "classification_rule_version": CLASSIFICATION_RULE_VERSION, "context": deepcopy(source["context"]),
              "configuration": {"thresholds": thresholds, "calibration_status": "provisional_coinglass_only"},
              "source_selection": deepcopy(source["source_selection"]), "markets": markets, "whale_activity": whale,
              "market_history": history, "comparison": comparison,
              "summary": {"observed_liquidity": {"markets": {market: markets[market]["summary"] for market in MARKETS},
                                                   "cross_market": cross, "calibration_status": "provisional_coinglass_only",
                                                   "provider_scope": "coinglass", "limitations": ["coinglass_only", "no_glassnode", "no_cryptoquant",
                                                       "large_trades_may_have_incomplete_coverage", "whale_index_is_proprietary",
                                                       "range_10_is_not_full_book", "observed_conditions_not_absolute_global_liquidity"]}},
              "quality": {"status": quality, "reason": None if quality == "ok" else "one_or_more_required_groups_not_available",
                          "required_groups": list(required), "optional_groups": ["market_history", "execution_liquidity", "comparison.spot_perpetual",
                              "pressure_alignment", "cross_market_execution_liquidity", "whale_rolling_classification"],
                          "available_groups": available, "partial_groups": partial, "unavailable_groups": unavailable,
                          "invalid_groups": invalid, "warnings": [], "errors": []}}
    json.dumps(output, ensure_ascii=False, allow_nan=False)
    return output


run_liquidity_microstructure_classification = classify_liquidity_microstructure


class LiquidityMicrostructureClassifier:
    def __init__(self, processing_contract: Mapping[str, Any], *, config: Mapping[str, Any] | None = None,
                 now_timestamp: int | None = None) -> None:
        self.arguments = {"processing_contract": processing_contract, "config": config, "now_timestamp": now_timestamp}

    def run(self) -> dict[str, Any]:
        return classify_liquidity_microstructure(**self.arguments)
