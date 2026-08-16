from __future__ import annotations

import copy
import json

import pytest

import processing_signals.main.screen_contract_export as exporter
from processing_signals.main.on_chain_miners import run_on_chain_miners_vertical
from processing_signals.main.screen_contract_export import export_on_chain_miners_screen_json, write_on_chain_miners_screen_json
from on_chain_miners_test_helpers import NOW, FakeFetcher


@pytest.fixture
def vertical_output():
    return run_on_chain_miners_vertical(fetcher=FakeFetcher(), now_timestamp=NOW,
        input_arguments={"requested_mode": "bootstrap", "include_screen_extensions": True})


def test_export_writes_only_strict_screen_json(tmp_path, vertical_output):
    before = copy.deepcopy(vertical_output)
    path = export_on_chain_miners_screen_json(vertical_output=vertical_output,
                                              output_path=tmp_path / "on_chain_miners_screen.json")
    loaded = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: pytest.fail(value))
    assert loaded == vertical_output["screen"]
    assert not ({"input", "processing", "classification"} & set(loaded))
    assert vertical_output == before
    assert not list(tmp_path.glob("*.tmp"))


def test_export_rejects_wrong_contract_without_touching_destination(tmp_path, vertical_output):
    destination = tmp_path / "screen.json"
    destination.write_text('{"preserved":true}\n', encoding="utf-8")
    invalid = copy.deepcopy(vertical_output["screen"])
    invalid["screen"]["family"] = "wrong"
    with pytest.raises(ValueError):
        write_on_chain_miners_screen_json(screen_contract=invalid, output_path=destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"preserved": True}


def test_atomic_replace_failure_rolls_back_and_cleans_temporary(tmp_path, monkeypatch, vertical_output):
    destination = tmp_path / "screen.json"
    destination.write_text('{"preserved":true}\n', encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    with pytest.raises(OSError):
        export_on_chain_miners_screen_json(vertical_output=vertical_output, output_path=destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"preserved": True}
    assert not list(tmp_path.glob("*.tmp"))
