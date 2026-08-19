from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy

import pytest

from processing_signals.main.cvd_volume_orderflow import build_cvd_volume_orderflow_screen
from processing_signals.main.screen_contract_export import CVD_VOLUME_ORDERFLOW_OUTPUT_PATH, write_cvd_volume_orderflow_screen_json

INPUT_TEST = Path(__file__).with_name("test_cvd_volume_orderflow_input_vertical.py")


@pytest.fixture(scope="session")
def screen():
    helpers = runpy.run_path(str(INPUT_TEST))
    return build_cvd_volume_orderflow_screen(helpers["small_input"](), display_point_limit=1,
        clock=lambda: helpers["REFERENCE"])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_default_atomic_pretty_strict_json(screen):
    assert CVD_VOLUME_ORDERFLOW_OUTPUT_PATH == Path("runtime/contracts/hmi/cvd_volume_orderflow_screen.json")
    result = write_cvd_volume_orderflow_screen_json(screen_contract=screen)
    raw = result.read_bytes()
    assert result == CVD_VOLUME_ORDERFLOW_OUTPUT_PATH and raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8")) == screen
    assert not list(result.parent.glob("*.tmp"))


@pytest.mark.parametrize("value", [None, [], "x", 1, True])
def test_requires_exact_screen(value):
    with pytest.raises(ValueError, match="cvd_export_invalid:screen"):
        write_cvd_volume_orderflow_screen_json(screen_contract=value)


def test_invalid_policy_and_path_boundary(screen):
    invalid = copy.deepcopy(screen)
    invalid["quality"]["status"] = "invalid"
    with pytest.raises(ValueError, match="cvd_export_invalid:screen_invalid"):
        write_cvd_volume_orderflow_screen_json(screen_contract=invalid)
    assert write_cvd_volume_orderflow_screen_json(screen_contract=invalid,
        output_path="runtime/contracts/invalid.json", allow_invalid=True).exists()
    for path in ("../escape.json", "runtime/screen.json", "runtime/contracts/screen.txt"):
        with pytest.raises(ValueError, match="cvd_export_invalid:path"):
            write_cvd_volume_orderflow_screen_json(screen_contract=screen, output_path=path)


def test_deterministic_bytes_and_no_mutation(screen):
    before = copy.deepcopy(screen)
    first = write_cvd_volume_orderflow_screen_json(screen_contract=screen).read_bytes()
    second = write_cvd_volume_orderflow_screen_json(screen_contract=screen).read_bytes()
    assert first == second and screen == before
