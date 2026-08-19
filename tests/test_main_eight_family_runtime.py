from __future__ import annotations

import json
from pathlib import Path

from processing_signals.main.runtime_orchestrator import (
    FAMILY_ORDER,
    SCREEN_FILENAMES,
    run_all,
    export_all_runtime_json,
)


def test_main_runs_and_exports_all_eight_families(tmp_path: Path) -> None:
    output = run_all(source="emulator", input_raw_root=Path("runtime/contracts/input_raw"))

    assert tuple(output["input"]) == FAMILY_ORDER
    assert tuple(output["processing"]) == FAMILY_ORDER
    assert tuple(output["classification"]) == FAMILY_ORDER
    assert tuple(output["hmi"]) == FAMILY_ORDER

    liquidations_processing = output["processing"]["long_short_liquidations"]
    reference = liquidations_processing["maps"]["reference_price"]
    assert reference["status"] == "available"
    assert reference["source_family"] == "prices_ohlcv"
    assert reference["source_market"] == "spot"
    assert reference["source_timeframe"] == "1m"
    assert reference["price_field"] == "close"
    assert reference["timestamp_alignment"] == "synthetic_fixture_rebased_to_target_reference"
    assert liquidations_processing["realized"]["windows"]["24h"]["window_end"] <= liquidations_processing["reference_timestamp"]

    liquidations_screen = output["hmi"]["long_short_liquidations"]
    current = next(item for item in liquidations_screen["kpis"] if item["id"] == "current_price")
    assert current["status"] == "available"
    assert current["value"] == reference["value"]

    publication = export_all_runtime_json(output, contracts_root=tmp_path / "contracts")
    assert Path(publication["manifest"]).exists()
    manifest = json.loads(Path(publication["manifest"]).read_text(encoding="utf-8"))
    assert tuple(manifest["families"]) == FAMILY_ORDER

    assert (tmp_path / "contracts" / "hmi").is_dir()
    assert not (tmp_path / "contracts" / "hmi_contract").exists()

    for family in FAMILY_ORDER:
        assert (tmp_path / "contracts" / "input" / f"{family}.json").exists()
        assert (tmp_path / "contracts" / "processing" / f"{family}.json").exists()
        assert (tmp_path / "contracts" / "classification" / f"{family}.json").exists()
        assert (tmp_path / "contracts" / "hmi" / SCREEN_FILENAMES[family]).exists()
