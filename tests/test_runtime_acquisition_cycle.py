from __future__ import annotations

import json
from pathlib import Path

from processing_signals.runtime.acquisition import AcquisitionManager, endpoint_policy


class Router:
    def for_family(self, family):
        def fetcher(**request):
            return {"code": "0", "data": [
                {"time": 1_700_000_000_000, "value": 1.0},
                {"time": 1_700_003_600_000, "value": 2.0},
            ]}
        return fetcher


def _raw_root(path: Path) -> Path:
    root = path / "raw"
    root.mkdir()
    (root / "fixture.json").write_text(json.dumps({"data": []}), encoding="utf-8")
    return root


def test_cold_then_warm_start_and_watermark_dedup(tmp_path: Path) -> None:
    raw = _raw_root(tmp_path)
    state = tmp_path / "state"
    request = {"provider": "coinglass", "endpoint_id": "dvol_ohlc", "path": "/x",
               "params": {"interval": "1h"}, "dimensions": {"market_type": "futures", "timeframe": "1h"}}

    cold = AcquisitionManager(state, input_raw_root=raw)
    assert cold.cold_start and cold.mode == "bootstrap"
    cold.wrap_router(Router()).for_family("volatility_market_regimes")(**request)
    cold.mark_complete()
    assert cold.summary()["families"]["volatility_market_regimes"]["records_new"] == 2
    cold.close()

    warm = AcquisitionManager(state, input_raw_root=raw)
    assert not warm.cold_start and warm.mode == "incremental"
    warm.wrap_router(Router()).for_family("volatility_market_regimes")(**request)
    summary = warm.summary()
    assert summary["records_persisted"] == 2
    assert summary["families"]["volatility_market_regimes"]["records_new"] == 0
    assert summary["families"]["volatility_market_regimes"]["records_deduplicated"] == 2
    warm.close()


def test_native_only_policies_are_explicit() -> None:
    assert endpoint_policy("funding_rates").resampling_class.value == "NATIVE_ONLY"
    assert endpoint_policy("whale_index").resampling_class.value == "NATIVE_ONLY"
    assert endpoint_policy("realized_volatility_1_week").resampling_class.value == "ROLLING_METRIC"
