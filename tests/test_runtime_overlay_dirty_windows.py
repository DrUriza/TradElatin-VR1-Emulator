from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from processing_signals.runtime.acquisition import AcquisitionManager, plan_dirty_windows, recompute_plan
from processing_signals.runtime.emulator.overlay import EmulatorOverlay, merge_overlay_response


def test_overlay_append_revision_clear_and_raw_immutability(tmp_path: Path) -> None:
    raw = tmp_path / "input_raw"
    raw.mkdir()
    fixture = raw / "fixture.json"
    fixture.write_text('{"data":[]}', encoding="utf-8")
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    overlay = EmulatorOverlay(tmp_path / "state" / "overlay")
    selectors = {"interval": "1m", "exchange": "Binance", "symbol": "BTCUSDT"}
    bar = {"time": 1_700_000_040_000, "open": 10, "high": 12, "low": 9, "close": 11, "volume_usd": 5}
    overlay.append_observation(family="prices_ohlcv", provider="coinglass", endpoint_id="spot_ohlcv", selectors=selectors, record=bar)
    assert overlay.records_for(family="prices_ohlcv", provider="coinglass", endpoint_id="spot_ohlcv", params=selectors) == [bar]
    revised = {**bar, "close": 10.5}
    overlay.revise_observation(family="prices_ohlcv", provider="coinglass", endpoint_id="spot_ohlcv",
                               selectors=selectors, timestamp=1_700_000_040, record=revised)
    merged = merge_overlay_response({"code": "0", "data": [bar]}, [revised])
    assert merged["data"] == [revised]
    overlay.clear_overlay()
    assert overlay.families == ()
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before


def test_dirty_propagation_respects_native_and_bucket_closure() -> None:
    # 00:04 is the final source member of [00:00, 00:05).
    closing = 1_700_000_040 - (1_700_000_040 % 300) + 240
    rows = [{"family": "prices_ohlcv", "source_timeframe": "1m", "start_timestamp": closing,
             "end_timestamp": closing, "cause": "NEW"}]
    open_parent = plan_dirty_windows(rows, reference_timestamp=closing + 59)
    assert {item.target_timeframe for item in open_parent} == {"1m"}
    closed_parent = plan_dirty_windows(rows, reference_timestamp=closing + 60)
    assert {item.target_timeframe for item in closed_parent} == {"1m", "5m"}
    assert "15m" not in {item.target_timeframe for item in closed_parent}
    assert recompute_plan(closed_parent)["mode"] == "INCREMENTAL_WINDOWED"


def test_native_only_does_not_propagate() -> None:
    rows = [{"family": "liquidity_microstructure", "source_timeframe": "1h", "start_timestamp": 1_700_000_000,
             "end_timestamp": 1_700_000_000, "cause": "REVISION"}]
    windows = plan_dirty_windows(rows, reference_timestamp=1_800_000_000, endpoint_id="whale_index")
    assert len(windows) == 1 and windows[0].target_timeframe == "1h"


class MutableRouter:
    def __init__(self, record): self.record = record
    def for_family(self, family): return lambda **request: {"code": "0", "data": [self.record]}


def test_dirty_state_survives_until_explicit_success(tmp_path: Path) -> None:
    raw = tmp_path / "raw"; raw.mkdir(); (raw / "x.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "state"
    request = {"provider": "coinglass", "endpoint_id": "spot_ohlcv", "params": {"interval": "1m"}}
    first = {"time": 1_700_000_000_000, "open": 1, "high": 2, "low": 1, "close": 2, "volume_usd": 1}
    cold = AcquisitionManager(state, input_raw_root=raw)
    cold.wrap_router(MutableRouter(first)).for_family("prices_ohlcv")(**request)
    cold.mark_complete(); cold.mark_overlay_processed(0); cold.close()
    changed = {**first, "close": 1.5}
    warm = AcquisitionManager(state, input_raw_root=raw)
    warm.wrap_router(MutableRouter(changed)).for_family("prices_ohlcv")(**request)
    pending = warm.pending_dirty_windows()
    assert len(pending) == 1 and pending[0]["cause"] == "REVISION"
    warm.close()  # controlled failure: no clean acknowledgement
    retry = AcquisitionManager(state, input_raw_root=raw)
    assert retry.pending_dirty_windows() == pending
    retry.mark_dirty_clean([pending[0]["id"]])
    assert retry.pending_dirty_windows() == []
    retry.close()


def test_late_arrival_does_not_move_watermark_backwards(tmp_path: Path) -> None:
    raw = tmp_path / "raw"; raw.mkdir(); (raw / "x.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "state"
    request = {"provider": "coinglass", "endpoint_id": "spot_ohlcv", "params": {"interval": "1m"}}
    latest = {"time": 1_700_000_120_000, "open": 1, "high": 2, "low": 1, "close": 2, "volume_usd": 1}
    cold = AcquisitionManager(state, input_raw_root=raw)
    cold.wrap_router(MutableRouter(latest)).for_family("prices_ohlcv")(**request)
    cold.mark_complete(); cold.close()
    late = {**latest, "time": 1_700_000_060_000}
    warm = AcquisitionManager(state, input_raw_root=raw)
    warm.wrap_router(MutableRouter(late)).for_family("prices_ohlcv")(**request)
    assert warm.summary()["families"]["prices_ohlcv"]["late_arrivals"] == 1
    assert warm.pending_dirty_windows()[0]["cause"] == "LATE_ARRIVAL"
    watermark = warm.connection.execute("SELECT last_seen_timestamp FROM endpoint_state").fetchone()[0]
    assert watermark == 1_700_000_120
    warm.close()
