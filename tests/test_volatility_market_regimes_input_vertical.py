from __future__ import annotations

import json

from processing_signals.input.volatility_market_regimes.volatility_market_regimes_data_raw_extract import (
    ENDPOINT_MANIFEST,
    VolatilityMarketRegimesRawExtractor,
    build_volatility_market_regimes_fetch_plan,
)
from processing_signals.input.volatility_market_regimes.volatility_market_regimes_data_raw_preprocessing import (
    VolatilityMarketRegimesInputPreprocessor,
)
from processing_signals.runtime.emulator import SyntheticProviderRouter

NOW = 1741618800


def fetcher(**request):
    return SyntheticProviderRouter("runtime/contracts/input_raw").for_family("volatility_market_regimes")(**request)


def test_manifest_and_plan_have_only_coinglass_and_glassnode() -> None:
    assert set(ENDPOINT_MANIFEST) == {("coinglass", "top_position_long_short_ratio"), ("glassnode", "realized_volatility")}
    plan = build_volatility_market_regimes_fetch_plan(mode="bootstrap", reference_timestamp=NOW)
    assert {item["provider"] for item in plan} == {"coinglass", "glassnode"}
    assert len(plan) == 4
    assert "deribit" not in json.dumps(plan).lower()


def test_raw_to_input_is_two_provider_and_quality_ok() -> None:
    raw = VolatilityMarketRegimesRawExtractor(fetcher, clock=lambda: NOW + 5).run(mode="bootstrap", reference_timestamp=NOW)
    assert raw["stage"] == "extracted_raw"
    assert all(item["status"] == "ok" for item in raw["requests"])
    output = VolatilityMarketRegimesInputPreprocessor().run(raw)
    assert output["stage"] == "input"
    assert output["quality"]["status"] == "ok"
    assert set(output["providers"]) == {"coinglass", "glassnode"}
    assert len(output["providers"]["coinglass"]["top_position_ratio"]["records"]) == 2881
    assert len(output["providers"]["glassnode"]["realized_volatility"]["records"]) == 2881
    text = json.dumps(output).lower()
    assert "deribit" not in text
    assert "implied" not in text
