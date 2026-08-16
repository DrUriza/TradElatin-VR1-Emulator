"""Pure reusable time-series mathematics shared by Processing families."""
from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real
from typing import Any


def finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def rolling_mean_std(values: Sequence[Any], *, window: int, min_valid: int, ddof: int = 1) -> list[tuple[float | None, float | None]]:
    if window <= 0 or min_valid <= 0 or ddof < 0:
        raise ValueError("invalid rolling parameters")
    output: list[tuple[float | None, float | None]] = []
    for index in range(len(values)):
        sample = [finite(v) for v in values[max(0, index - window + 1): index + 1]]
        clean = [v for v in sample if v is not None]
        if len(clean) < min_valid or len(clean) <= ddof:
            output.append((None, None))
            continue
        mean = sum(clean) / len(clean)
        variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - ddof)
        output.append((mean, math.sqrt(max(variance, 0.0))))
    return output


def rolling_z_scores(values: Sequence[Any], *, window: int, min_valid: int, ddof: int = 1) -> list[float | None]:
    stats = rolling_mean_std(values, window=window, min_valid=min_valid, ddof=ddof)
    output: list[float | None] = []
    for value, (mean, std) in zip(values, stats):
        current = finite(value)
        output.append(None if current is None or mean is None or std in (None, 0.0) else (current - mean) / std)
    return output


def rolling_percentile_ranks(values: Sequence[Any], *, window: int, min_valid: int) -> list[float | None]:
    if window <= 0 or min_valid <= 0:
        raise ValueError("invalid percentile parameters")
    output: list[float | None] = []
    for index, value in enumerate(values):
        current = finite(value)
        sample = [finite(v) for v in values[max(0, index - window + 1): index + 1]]
        clean = [v for v in sample if v is not None]
        if current is None or len(clean) < min_valid:
            output.append(None)
            continue
        less = sum(v < current for v in clean)
        equal = sum(v == current for v in clean)
        output.append((less + 0.5 * equal) / len(clean))
    return output


def safe_percent_change(current: Any, previous: Any) -> float | None:
    a, b = finite(current), finite(previous)
    if a is None or b in (None, 0.0):
        return None
    return ((a / b) - 1.0) * 100.0
