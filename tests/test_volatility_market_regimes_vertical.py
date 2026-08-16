from __future__ import annotations

import copy
import json
import tempfile
from pathlib       import Path
from unittest.mock import patch

import pytest

from processing_signals.main.volatility_market_regimes import (
    VolatilityMarketRegimesVerticalError,
    derive_volatility_market_regimes_recovery_requests,
    run_volatility_market_regimes_vertical,
    serialize_volatility_market_regimes_screen,
    write_volatility_market_regimes_screen_atomic,
)
from test_volatility_market_regimes_input_vertical import NOW, fetcher


RUNTIME = {"data_mode": "synthetic", "is_demo": True, "generated_at": "2027-01-15T08:00:00Z", "updated_at": "2027-01-15T08:01:00Z"}


def _run(**changes):
    arguments = {"mode": "bootstrap", "fetcher": fetcher, "reference_timestamp": NOW, "runtime_context": RUNTIME, "execution_clock": lambda: NOW + 5}
    arguments.update(changes)
    return run_volatility_market_regimes_vertical(**arguments)


@pytest.mark.parametrize("changes", [{"mode": "other"}, {"reference_timestamp": True}, {"reference_timestamp": 1.2}])
def test_invalid_request_identifies_validation_stage(changes):
    with pytest.raises(VolatilityMarketRegimesVerticalError) as error:
        _run(**changes)
    assert error.value.stage == "validation"


def test_bootstrap_root_stages_runtime_and_no_raw():
    output = _run(selected_range="4h")
    assert list(output) == ["input", "processing", "classification", "screen"]
    assert [output[name]["mode"] for name in ("input", "processing", "classification")] == ["bootstrap"] * 3
    assert output["input"]["stage"] == "input"
    assert output["screen"]["schema_version"] == "0.2.0"
    assert output["screen"]["context"]["selected_display_range"] == "4h"
    assert output["screen"]["badges"][0]["badge_id"] == "demo"
    assert "raw" not in output


def test_bootstrap_rejects_previous_and_recovery_requests():
    previous = _run()
    with pytest.raises(VolatilityMarketRegimesVerticalError):
        _run(previous_vertical_output=previous)
    with pytest.raises(VolatilityMarketRegimesVerticalError):
        _run(recovery_requests=[{"provider": "coinglass"}])


def test_incremental_requires_previous_rejects_recovery_and_preserves_previous():
    with pytest.raises(VolatilityMarketRegimesVerticalError):
        _run(mode="incremental")
    previous = _run()
    original = copy.deepcopy(previous)
    output   = _run(mode="incremental", previous_vertical_output=previous, reference_timestamp=NOW + 3600)
    assert previous == original
    assert output["input"]["mode"] == output["processing"]["mode"] == output["classification"]["mode"] == "incremental"
    with pytest.raises(VolatilityMarketRegimesVerticalError):
        _run(mode="incremental", previous_vertical_output=previous, recovery_requests=[{"provider": "coinglass"}])


def test_previous_validation_and_recovery_requires_targets():
    with pytest.raises(VolatilityMarketRegimesVerticalError):
        _run(mode="recovery", previous_vertical_output={})
    previous = _run()
    with pytest.raises(VolatilityMarketRegimesVerticalError) as error:
        _run(mode="recovery", previous_vertical_output=previous)
    assert error.value.stage == "validation"


def test_recovery_requests_are_derived_merged_and_ordered():
    previous = _run()["input"]
    previous["providers"]["coinglass"]["top_position_ratio"]["gap_ranges"] = [
        {"after_timestamp": 10, "before_timestamp": 20}, {"after_timestamp": 20, "before_timestamp": 30}]
    assert derive_volatility_market_regimes_recovery_requests(previous) == [
        {"provider": "coinglass", "endpoint_id": "top_position_long_short_ratio", "start_timestamp": 10, "end_timestamp": 30},
]


def test_output_is_deterministic_immutable_and_strict_json():
    runtime = copy.deepcopy(RUNTIME)
    first   = _run(runtime_context=runtime)
    second  = _run(runtime_context=runtime)
    assert first == second and runtime == RUNTIME
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    assert serialize_volatility_market_regimes_screen(first["screen"]).endswith("\n")


def test_export_false_does_not_write_and_custom_export_is_screen_only():
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "nested" / "screen.json"
        output      = _run(export_screen=False, export_path=destination)
        assert not destination.exists()
        output = _run(export_screen=True, export_path=destination)
        assert destination.exists() and destination.read_bytes().endswith(b"\n")
        exported = json.loads(destination.read_text(encoding="utf-8"))
        assert exported == output["screen"]
        assert not ({"input", "processing", "classification", "raw"} & set(exported))


def test_atomic_second_write_replaces_and_does_not_mutate():
    screen   = _run()["screen"]
    original = copy.deepcopy(screen)
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "screen.json"
        destination.write_text("old", encoding="utf-8")
        write_volatility_market_regimes_screen_atomic(screen, destination)
        first = destination.read_bytes()
        write_volatility_market_regimes_screen_atomic(screen, destination)
        assert destination.read_bytes() == first
        assert not list(destination.parent.glob("*.tmp"))
    assert screen == original


def test_serialization_failure_preserves_destination():
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "screen.json"
        destination.write_text("previous", encoding="utf-8")
        with pytest.raises(VolatilityMarketRegimesVerticalError) as error:
            write_volatility_market_regimes_screen_atomic({"value": float("nan")}, destination)
        assert error.value.stage == "serialization" and destination.read_text(encoding="utf-8") == "previous"


def test_replace_failure_preserves_destination_and_cleans_temporary():
    screen = _run()["screen"]
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "screen.json"
        destination.write_text("previous", encoding="utf-8")
        with patch("processing_signals.main.volatility_market_regimes.volatility_market_regimes_vertical.os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(VolatilityMarketRegimesVerticalError) as error:
                write_volatility_market_regimes_screen_atomic(screen, destination)
        assert error.value.stage == "export" and destination.read_text(encoding="utf-8") == "previous"
        assert not list(destination.parent.glob("*.tmp"))


def test_fetcher_error_is_isolated_by_input_and_screen_remains_serializable():
    def broken(**kwargs):
        del kwargs
        raise RuntimeError("boom")
    output = _run(fetcher=broken)
    assert output["input"]["quality"]["status"] != "ok"
    json.dumps(output["screen"], ensure_ascii=False, allow_nan=False)


def test_module_contains_orchestration_not_financial_calculations():
    source = (Path(__file__).parents[1] / "src/processing_signals/main/volatility_market_regimes/volatility_market_regimes_vertical.py").read_text(encoding="utf-8")
    for forbidden in ("rolling_mean", "z_score", "percentile_rank", "confidence_score", "persistence_days", "spread_volatility_points", "kpis", "charts"):
        assert forbidden not in source
