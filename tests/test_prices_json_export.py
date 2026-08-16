from __future__ import annotations

import json
from pathlib import Path
from typing  import Any

import pytest

pytestmark = pytest.mark.skip(reason="legacy synthetic Prices CLI/export retired; current entrypoint is main.py")

from processing_signals.main.main_pipeline import _run_synthetic_vertical, export_prices_screen_json, main, write_prices_screen_json


@pytest.fixture(scope="module")
def vertical_output() -> dict[str, Any]:
    return _run_synthetic_vertical("bootstrap")


def test_normal_export_contains_only_complete_unwrapped_screen(tmp_path: Path, vertical_output: dict[str, Any]) -> None:
    output = export_prices_screen_json(vertical_output=vertical_output, output_path=tmp_path / "prices_screen.json")
    files  = list(tmp_path.iterdir())
    assert files == [output]
    assert output.name == "prices_screen.json"
    with output.open(encoding="utf-8") as stream:
        contract = json.load(stream)
    assert contract["family"] == "prices_ohlcv"
    assert contract["screen"] == "prices"
    assert not (set(contract) == {"screen"} and isinstance(contract["screen"], dict))
    assert len(contract["charts"]) == 10
    assert contract["selectors"]["market"]["selected"] == "general"
    assert set(contract["selectors"]["market"]["options"]) == {"general", "spot", "futures"}
    assert len(contract["selectors"]["timeframe"]["options"]) == 6
    tables = contract["tables"]["indicators_metrics"]
    assert len(tables["indicator_package"]["rows"]) == 11
    assert len(tables["technical_bias"]["rows"]) == 4
    assert len(tables["statistical_performance"]["rows"]) == 17
    tsi = next(row for row in tables["indicator_package"]["rows"] if row["metric_id"] == "tsi")
    assert tsi["parameters"] == {"long_period": 25, "short_period": 13}
    assert contract["context"]["performance_basis"] == "market_returns"
    assert contract["context"]["data_mode"] == "synthetic"
    assert contract["context"]["is_demo"] is True
    assert contract["context"]["provider"] == "synthetic_prices_fetcher"
    assert contract["badges"] == [{"badge_id": "demo", "text": "DEMO"}]
    assert contract["context"]["generated_at"]
    assert contract["context"]["data_as_of"]
    assert contract["context"]["updated_at"]
    assert {item["metric_id"] for item in contract["kpis"]["items"]} == {"last_price", "high_24h", "low_24h", "change_24h", "volume_24h",
                                                                            "market_cap", "volatility_atr_percent", "average_range", "beta"}
    assert next(item for item in contract["kpis"]["items"] if item["metric_id"] == "market_cap")["status"] == "unavailable"
    chart_1h = contract["charts"]["ohlcv"]["markets"]["general"]["timeframes"]["1h"]
    assert chart_1h["metadata"]["bar_interval_seconds"] == 3_600
    assert {"is_closed", "source_timeframe", "resampled", "coverage_complete"} <= set(chart_1h["metadata"])
    assert {"moving_averages", "bollinger_bands", "fibonacci_levels", "pivot_points", "support", "resistance", "vwap"} <= set(chart_1h["overlays"])
    annotations = contract["charts"]["ohlcv"]["annotations"]["general"]["1h"]
    assert all(event_uid in contract["events"]["by_id"] for event_ids in annotations["by_timestamp"].values() for event_uid in event_ids)
    assert contract["widgets"]["most_recent_candle"]["candle"]["timestamp"]
    assert contract["widgets"]["moving_averages_summary"]["values"]["ema_9"] is not None
    assert contract["widgets"]["candlestick_patterns_analysis"]["row_ids"]
    assert contract["widgets"]["volume_analysis"]["total_24h"] > 0
    assert contract["widgets"]["drawdown"]["basis"] == "market_returns"
    assert contract["widgets"]["volume_profile"]["status"] == "unavailable"
    assert contract["widgets"]["price_forecast"]["reason"] == "forecast_model_not_configured"
    assert output.read_bytes().endswith(b"\n")
    assert "NaN" not in output.read_text(encoding="utf-8")
    assert "Infinity" not in output.read_text(encoding="utf-8")


def test_write_uses_temporary_file_and_atomic_replace(tmp_path: Path, vertical_output: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    output           = tmp_path / "prices_screen.json"
    original_replace = Path.replace
    replacements     = []

    def recording_replace(source: Path, target: Path) -> Path:
        replacements.append((source, Path(target), source.exists()))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", recording_replace)
    write_prices_screen_json(screen_contract=vertical_output["screen"], output_path=output)
    assert replacements == [(tmp_path / "prices_screen.json.tmp", output, True)]
    assert not (tmp_path / "prices_screen.json.tmp").exists()


def test_invalid_serialization_does_not_replace_existing_contract(tmp_path: Path, vertical_output: dict[str, Any]) -> None:
    output = tmp_path / "prices_screen.json"
    output.write_text("previous\n", encoding="utf-8")
    invalid = dict(vertical_output["screen"])
    invalid["invalid_number"] = float("nan")
    with pytest.raises(ValueError):
        write_prices_screen_json(screen_contract=invalid, output_path=output)
    assert output.read_text(encoding="utf-8") == "previous\n"
    assert not (tmp_path / "prices_screen.json.tmp").exists()


def test_cli_writes_debug_bundle_only_when_requested(tmp_path: Path) -> None:
    output = tmp_path / "prices_screen.json"
    assert main(["--mode", "bootstrap", "--output", str(output), "--synthetic"]) == 0
    assert list(tmp_path.iterdir()) == [output]
    debug = tmp_path / "prices_debug.json"
    assert main(["--mode", "bootstrap", "--output", str(output), "--debug-output", str(debug), "--synthetic"]) == 0
    assert set(json.loads(debug.read_text(encoding="utf-8"))) == {"input", "processing", "classification", "screen"}
    assert set(json.loads(output.read_text(encoding="utf-8"))) != {"input", "processing", "classification", "screen"}
