from __future__ import annotations

import copy
import json

import pytest

from processing_signals.main.on_chain_miners import run_on_chain_miners_vertical
from processing_signals.main.screen_contract_export import export_on_chain_miners_screen_json, write_on_chain_miners_screen_json
from processing_signals.runtime.emulator.provider_router import SyntheticProviderRouter

NOW = 1786100400


def _bundle():
    return run_on_chain_miners_vertical(
        fetcher=SyntheticProviderRouter().for_family("on_chain_miners"),
        input_arguments={"requested_mode": "bootstrap", "include_screen_extensions": True,
                         "data_mode": "synthetic", "is_demo": True},
        now_timestamp=NOW,
    )


def test_export_writes_only_current_screen_json(tmp_path):
    vertical = _bundle()
    before = copy.deepcopy(vertical)
    path = export_on_chain_miners_screen_json(vertical_output=vertical, output_path=tmp_path / "on_chain_miners_screen.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == vertical["screen"]
    assert loaded["schema"]["version"] == "2.0.0"
    assert "miner_analysis" in loaded and "technical_analysis" not in loaded
    assert vertical == before


def test_export_rejects_wrong_screen_identity(tmp_path):
    vertical = _bundle()
    invalid = copy.deepcopy(vertical["screen"])
    invalid["screen"]["family"] = "wrong"
    with pytest.raises(ValueError):
        write_on_chain_miners_screen_json(screen_contract=invalid, output_path=tmp_path / "screen.json")
