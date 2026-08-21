from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from processing_signals.main.prices_ohlcv import run_prices_vertical
from processing_signals.runtime.emulator import SyntheticProviderRouter


TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
IDENTITY_KEYS = (
    "metric_id", "kpi_id", "widget_id", "chart_id", "table_id", "badge_id",
    "id", "role", "family", "group", "indicator_id", "first_series", "event_type",
)


def _vertical() -> dict[str, Any]:
    return run_prices_vertical(
        fetcher=SyntheticProviderRouter("runtime/contracts/input_raw").for_family("prices_ohlcv"),
        input_arguments={"requested_mode": "bootstrap", "bootstrap_limit": 500},
        now_timestamp=1786147140,
        runtime_metadata={"data_mode": "synthetic", "is_demo": True},
    )


def _reference_item(reference: list[Any], item: Any, index: int) -> Any:
    if isinstance(item, Mapping):
        for key in IDENTITY_KEYS:
            if key not in item:
                continue
            for ref_item in reference:
                if isinstance(ref_item, Mapping) and ref_item.get(key, object()) == item[key]:
                    return ref_item
    return reference[index] if index < len(reference) else reference[0]


def _assert_same_structure(reference: Any, candidate: Any, path: str = "$") -> None:
    if isinstance(reference, Mapping):
        assert isinstance(candidate, Mapping), path
        assert set(reference) == set(candidate), path
        for key in reference:
            if path == "$.events" and key == "by_id":
                prototypes = list(reference[key].values())
                for uid, event in candidate[key].items():
                    prototype = next(
                        (
                            row for row in prototypes
                            if row.get("event_type") == event.get("event_type")
                            and row.get("event_group") == event.get("event_group")
                        ),
                        None,
                    ) or next((row for row in prototypes if row.get("event_type") == event.get("event_type")), None)
                    assert prototype is not None, f"{path}.by_id[{uid}]"
                    _assert_same_structure(prototype, event, f"{path}.by_id[{uid}]")
                continue
            _assert_same_structure(reference[key], candidate[key], f"{path}.{key}")
        return
    if isinstance(reference, list):
        assert isinstance(candidate, list), path
        if not reference or all(not isinstance(item, (dict, list)) for item in reference):
            return
        for index, item in enumerate(candidate):
            _assert_same_structure(_reference_item(reference, item, index), item, f"{path}[{index}]")
        return
    if reference is None or candidate is None:
        return
    if isinstance(reference, (int, float)) and not isinstance(reference, bool):
        assert isinstance(candidate, (int, float)) and not isinstance(candidate, bool), path
        return
    assert type(reference) is type(candidate), path


def test_processing_uses_real_spot_and_futures_markets_only() -> None:
    vertical = _vertical()
    for timeframe in TIMEFRAMES:
        assert vertical["processing"]["markets"]["spot"]["timeframes"][timeframe]["records"]
        assert vertical["processing"]["markets"]["futures"]["timeframes"][timeframe]["records"]


def test_glassnode_market_cap_reaches_screen_contract_without_requiring_duplicate_price_confirmation() -> None:
    vertical = _vertical()
    # Spot OHLC from Prices is canonical. The duplicate Glassnode price OHLC
    # confirmation was removed from the default 52-endpoint acquisition plan.
    assert vertical["input"]["confirmations"]["glassnode"]["price_ohlc"]["status"] == "unavailable"
    market_cap_input = vertical["input"]["provider_features"]["market_cap"]
    assert market_cap_input["status"] == "available"
    market_cap = next(item for item in vertical["screen"]["kpis"]["items"] if item["metric_id"] == "market_cap")
    assert market_cap["status"] == "available"
    assert market_cap["value"] == market_cap_input["current"]["value"]
    assert market_cap["source"]["provider"] == "glassnode"


def test_screen_contract_matches_final_sp_structure() -> None:
    vertical = _vertical()
    reference_path = Path(__file__).parents[1] / "src" / "processing_signals" / "classification" / "prices_ohlcv" / "prices_screen_sp_v1_9.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    candidate = vertical["screen"]
    assert candidate["schema_version"] == "1.9.0"
    assert candidate["context"]["default_market"] == "spot"
    assert candidate["context"]["available_markets"] == ["spot"]
    assert candidate["selectors"]["market"]["selected"] == "spot"
    assert candidate["selectors"]["market"]["visible"] is False
    _assert_same_structure(reference, candidate)
    json.dumps(candidate, allow_nan=False)


def test_event_registry_uses_runtime_event_ids_not_sp_fixture_ids() -> None:
    vertical = _vertical()
    screen = vertical["screen"]
    assert screen["events"]["by_id"]
    assert all(event["source"]["market"] == "spot" for event in screen["events"]["by_id"].values())
    assert all(uid == event["event_uid"] for uid, event in screen["events"]["by_id"].items())
