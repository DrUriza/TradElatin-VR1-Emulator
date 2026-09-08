import pytest

from app.core.simulation_engine import SimulationEngine


@pytest.fixture()
def engine():
    return SimulationEngine(update_seconds=10, history_size=50, seed=1)


def test_advance_increments_timestamp(engine):
    initial = engine.get_state()
    new_state = engine.advance()
    assert new_state.timestamp == initial.timestamp + engine.update_seconds


def test_advance_appends_to_history(engine):
    engine.advance()
    engine.advance()
    history = engine.get_history(limit=10)
    assert len(history) == 3  # estado inicial + 2 avances


def test_history_respects_max_size():
    small_engine = SimulationEngine(update_seconds=1, history_size=3, seed=1)
    for _ in range(10):
        small_engine.advance()

    assert len(small_engine.get_history(limit=100)) == 3


def test_set_regime_valid(engine):
    engine.set_regime("bullish")
    assert engine.get_state().regime == "bullish"


def test_set_regime_invalid_raises(engine):
    with pytest.raises(ValueError):
        engine.set_regime("not_a_real_regime")


def test_reset_restores_initial_state(engine):
    engine.advance()
    engine.advance()
    reset_state = engine.reset()

    assert reset_state.btc_price == 100_000.0
    assert len(engine.get_history(limit=100)) == 1


def test_liquidation_event_forces_regime(engine):
    new_state = engine.trigger_liquidation_event()
    assert new_state.regime == "liquidation_event"


def test_reproducibility_with_same_seed():
    engine_a = SimulationEngine(update_seconds=10, history_size=50, seed=99)
    engine_b = SimulationEngine(update_seconds=10, history_size=50, seed=99)

    for _ in range(5):
        engine_a.advance()
        engine_b.advance()

    assert engine_a.get_state().btc_price == engine_b.get_state().btc_price
