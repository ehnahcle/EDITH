"""
52-week (252-day) high breakout. Classic Donchian-style momentum.

Default: enter when today's close > max(Close[t-252:t-1]) and volume confirms.

Optional nearness mode: instead of requiring a hard break of the 52w high,
fire when price is *within* `near_pct` of it (e.g. near_pct=0.95 => price is
>= 95% of the prior 52w high). This anticipates breakouts rather than chasing
them. `jan_skip` suppresses entries in January to test a turn-of-year effect.
"""

from __future__ import annotations

import pandas as pd


def new_high_52w(
    code: str,
    df: pd.DataFrame,
    lookback: int = 252,
    vol_mult: float = 1.5,
    stop_pct: float = 0.06,
    target_pct: float = 0.15,
    max_hold: int = 10,
    near_pct: float | None = None,
    jan_skip: bool = False,
) -> pd.DataFrame:
    df = df.copy()
    rolling_high = df["Close"].shift(1).rolling(lookback, min_periods=60).max()
    vol_ma = df["Volume"].rolling(20).mean()
    nearness = df["Close"] / rolling_high

    if near_pct is None:
        price_cond = df["Close"] > rolling_high          # strict break (default)
    else:
        price_cond = nearness >= near_pct                 # nearness percentile

    cond = price_cond & (df["Volume"] > vol_mult * vol_ma)
    if jan_skip:
        cond = cond & (df.index.month != 1)

    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = (nearness - 1).fillna(0.0)
    return sig
