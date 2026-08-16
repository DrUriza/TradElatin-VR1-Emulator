from __future__ import annotations

import pandas as pd


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
    smooth: int = 3,
    *,
    k_smoothing: int = 1,
    d_period: int | None = None,
) -> pd.DataFrame:
    high    = pd.to_numeric(high, errors="coerce")
    low     = pd.to_numeric(low, errors="coerce")
    close   = pd.to_numeric(close, errors="coerce")
    lowest  = low.rolling(window).min()
    highest = high.rolling(window).max()
    denominator = (highest - lowest).mask((highest - lowest) == 0, float("nan"))
    raw_k   = 100 * (close - lowest) / denominator
    k       = raw_k.rolling(k_smoothing).mean() if k_smoothing > 1 else raw_k
    d       = k.rolling(d_period or smooth).mean()
    return pd.DataFrame({"stoch_k_14": k, "stoch_d_14": d})
