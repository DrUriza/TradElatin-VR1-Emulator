"""Atomic JSON publication tests for the ETF exchange-flow screen contract."""
from copy import deepcopy
import json
import os

import pytest

from etf_exchange_flows_helpers import Fetcher, NOW
import processing_signals.main.screen_contract_export as exporter
from processing_signals.main.etf_exchange_flows import (
    DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH,
    run_etf_exchange_flows_vertical,
)
from processing_signals.main.screen_contract_export import (
    export_etf_exchange_flows_screen_json,
    write_etf_exchange_flows_screen_json,
)
from processing_signals.main.main_pipeline import run_main_pipeline

FAMILY = "etf_exchange_flows"


def arguments(**extra):
    return {"fetcher": Fetcher(), "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "exchange_scope": "all_exchange",
                            "data_mode": "synthetic", "is_demo": True}, **extra}


def test_etf_export_default_path_is_canonical():
    assert DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH.as_posix() == "runtime/contracts/hmi_contract/etf_exchange_flows_screen.json"


def test_etf_vertical_remains_in_memory_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = run_etf_exchange_flows_vertical(**arguments())
    assert output["screen"]["stage"] == "screen_contract"
    assert not (tmp_path / DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH).exists()


def test_etf_main_chain_publishes_requested_screen_json(tmp_path):
    destination = tmp_path / "runtime" / "contracts" / "etf_exchange_flows_screen.json"
    output = run_main_pipeline(enabled_families=(FAMILY,), family_arguments={FAMILY: arguments(
        publish_screen=True, output_path=destination)})[FAMILY]
    loaded = json.loads(destination.read_text(encoding="utf-8"),
                        parse_constant=lambda value: pytest.fail(value))
    assert loaded == output["screen"]
    assert loaded["screen"]["family"] == FAMILY
    assert destination.read_bytes().endswith(b"\n")
    assert not list(destination.parent.glob("*.tmp"))


def test_etf_export_writes_only_screen_and_does_not_mutate_vertical(tmp_path):
    output = run_etf_exchange_flows_vertical(**arguments())
    before = deepcopy(output)
    destination = export_etf_exchange_flows_screen_json(
        vertical_output=output, output_path=tmp_path / "screen.json")
    assert json.loads(destination.read_text(encoding="utf-8")) == output["screen"]
    assert output == before
    assert not ({"input", "processing", "classification"} & set(json.loads(destination.read_text())))


@pytest.mark.parametrize("field", ("schema", "screen", "stage", "version", "quality"))
def test_etf_writer_rejects_invalid_contract_without_touching_destination(tmp_path, field):
    screen = run_etf_exchange_flows_vertical(**arguments())["screen"]
    invalid = deepcopy(screen)
    invalid.pop(field)
    destination = tmp_path / "screen.json"
    destination.write_text('{"preserved":true}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        write_etf_exchange_flows_screen_json(screen_contract=invalid, output_path=destination)
    assert json.loads(destination.read_text()) == {"preserved": True}


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -float("inf")))
def test_etf_export_rejects_nonfinite_before_replace(tmp_path, value):
    screen = run_etf_exchange_flows_vertical(**arguments())["screen"]
    screen["hostile"] = value
    destination = tmp_path / "screen.json"
    destination.write_text("previous\n", encoding="utf-8")
    with pytest.raises(ValueError):
        write_etf_exchange_flows_screen_json(screen_contract=screen, output_path=destination)
    assert destination.read_text(encoding="utf-8") == "previous\n"


def test_etf_atomic_replace_failure_preserves_previous_and_cleans_temp(tmp_path, monkeypatch):
    screen = run_etf_exchange_flows_vertical(**arguments())["screen"]
    destination = tmp_path / "screen.json"
    destination.write_text('{"preserved":true}\n', encoding="utf-8")
    monkeypatch.setattr(exporter.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        write_etf_exchange_flows_screen_json(screen_contract=screen, output_path=destination)
    assert json.loads(destination.read_text()) == {"preserved": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_etf_fsync_failure_preserves_previous_and_cleans_temp(tmp_path, monkeypatch):
    screen = run_etf_exchange_flows_vertical(**arguments())["screen"]
    destination = tmp_path / "screen.json"
    destination.write_text('{"preserved":true}\n', encoding="utf-8")
    monkeypatch.setattr(os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        write_etf_exchange_flows_screen_json(screen_contract=screen, output_path=destination)
    assert json.loads(destination.read_text()) == {"preserved": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_etf_publish_screen_requires_boolean():
    with pytest.raises(ValueError, match="publish_screen must be a boolean"):
        run_etf_exchange_flows_vertical(**arguments(publish_screen=1))
