from __future__ import annotations

from app.core.simulation_engine import SimulationEngine
from app.settings import SIMULATION_UPDATE_SECONDS


def test_onchain_miners_daily_history_is_nontrivial_and_reanchored():
    engine = SimulationEngine(update_seconds=SIMULATION_UPDATE_SECONDS, history_size=500, seed=1729)
    history = engine.get_synthetic_history(
        interval_seconds=86_400,
        periods=30,
        end_timestamp=engine.get_state().timestamp,
        samples_per_period=1,
    )
    assert len(history) == 30

    reserve = [item.miner_balance for item in history]
    reserve_changes = [b - a for a, b in zip(reserve[:-1], reserve[1:], strict=True)]
    assert any(value > 0 for value in reserve_changes)
    assert any(value < 0 for value in reserve_changes)
    assert max(abs(value) for value in reserve_changes) > 100.0
    assert abs(reserve[-1] - engine.get_state().miner_balance) < 1e-6

    sopr = [item.sopr for item in history]
    assert min(sopr) < 1.0 < max(sopr)
    assert max(sopr) - min(sopr) > 0.04
    assert abs(sopr[-1] - engine.get_state().sopr) < 1e-9

    hashrate = [item.hash_rate for item in history]
    hashrate_span = (max(hashrate) - min(hashrate)) / hashrate[-1]
    assert 0.03 < hashrate_span < 0.20
    assert abs(hashrate[-1] - engine.get_state().hash_rate) / engine.get_state().hash_rate < 1e-12

    difficulty = [item.difficulty for item in history]
    unique_difficulty = len(set(round(value, 2) for value in difficulty))
    assert 2 <= unique_difficulty <= 4
    assert abs(difficulty[-1] - engine.get_state().difficulty) / engine.get_state().difficulty < 1e-12

    transfers = [item.miner_transfer_volume for item in history]
    assert max(transfers) > 2.0 * min(transfers)
