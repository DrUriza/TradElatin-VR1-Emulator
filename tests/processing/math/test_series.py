from __future__ import annotations

import math

import pytest

from processing_signals.processing.math.series import (
    finite,
    rolling_mean_std,
    rolling_percentile_ranks,
    rolling_z_scores,
    safe_percent_change,
)


def test_finite_and_safe_percent_change() -> None:
    assert finite(None) is None
    assert finite(True) is None
    assert finite(float("nan")) is None
    assert finite(2) == 2.0
    assert safe_percent_change(110, 100) == pytest.approx(10.0)
    assert safe_percent_change(10, 0) is None


def test_rolling_mean_std_and_zscore_respect_warmup() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    stats = rolling_mean_std(values, window=3, min_valid=3, ddof=1)
    assert stats[:2] == [(None, None), (None, None)]
    assert stats[2][0] == pytest.approx(2.0)
    assert stats[2][1] == pytest.approx(1.0)
    z = rolling_z_scores(values, window=3, min_valid=3, ddof=1)
    assert z[:2] == [None, None]
    assert z[2] == pytest.approx(1.0)
    assert math.isfinite(z[3])


def test_rolling_percentile_rank_is_deterministic_and_tie_aware() -> None:
    values = [1.0, 2.0, 2.0, 4.0]
    ranks = rolling_percentile_ranks(values, window=4, min_valid=2)
    assert ranks[0] is None
    assert ranks[1] == pytest.approx(0.75)
    assert ranks[2] == pytest.approx(2.0 / 3.0)
    assert ranks[3] == pytest.approx(0.875)
