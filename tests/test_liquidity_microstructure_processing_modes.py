from copy import deepcopy

from liquidity_microstructure_processing_helpers import liquidity_input
from processing_signals.processing.liquidity_microstructure.liquidity_microstructure_feature_builder import build_liquidity_microstructure_features
from processing_signals.processing.liquidity_microstructure.liquidity_microstructure_processor import process_liquidity_microstructure


def test_modes_are_deterministic_and_inputs_immutable():
    source = liquidity_input(mode="incremental")
    original = deepcopy(source)
    first = process_liquidity_microstructure(source, now_timestamp=123)
    second = process_liquidity_microstructure(source, now_timestamp=123)
    assert first == second and source == original
    assert first["mode"] == "incremental"


def test_feature_builder_deepcopies_without_recalculation():
    result = process_liquidity_microstructure(liquidity_input())
    features = build_liquidity_microstructure_features(markets=result["markets"], whale_activity=result["whale_activity"],
                                                       market_history=result["market_history"], comparison=result["comparison"])
    features["market_history"]["status"] = "changed"
    assert result["market_history"]["status"] == "available"
