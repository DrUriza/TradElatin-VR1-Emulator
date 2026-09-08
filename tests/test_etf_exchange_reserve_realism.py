from __future__ import annotations

from app.core.simulation_engine import SimulationEngine
from app.settings import SIMULATION_UPDATE_SECONDS


def test_exchange_reserve_history_is_reanchored_and_locally_realistic():
    engine = SimulationEngine(update_seconds=SIMULATION_UPDATE_SECONDS, history_size=200, seed=1729)
    history = engine.get_synthetic_history(
        interval_seconds=86_400,
        periods=500,
        end_timestamp=engine.get_state().timestamp,
        samples_per_period=1,
    )
    values = [item.exchange_reserve for item in history]
    assert len(values) == 500
    assert abs(values[-1] - engine.get_state().exchange_reserve) < 1e-6
    assert min(values) > 0.90 * values[-1]
    assert max(values) < 1.10 * values[-1]
    assert (max(values) - min(values)) / values[-1] < 0.08

    for previous, current in zip(history[:-1], history[1:], strict=True):
        reserve_delta = current.exchange_reserve - previous.exchange_reserve
        flow_delta = current.exchange_inflow - current.exchange_outflow
        assert abs(reserve_delta - flow_delta) < 1e-6
