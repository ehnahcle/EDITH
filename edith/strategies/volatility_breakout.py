"""
Larry Williams style volatility breakout, adapted to KR daily bars.

Idea: today's range R = High - Low. If tomorrow's intraday move from open
exceeds k * R, momentum is breaking out -> enter.

Daily-bar approximation: we cannot intraday-enter, so we approximate by
looking at today's Close vs today's Open + k * yesterday's range. If
Close > Open + k * prev_range AND volume > MA20(volume), buy at next open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def volatility_breakout(
    code: str,
    df: pd.DataFrame,
    k: float = 0.5,
    vol_mult: float = 1.2,
    stop_pct: float = 0.04,
    target_pct: float = 0.08,
    max_hold: int = 3,
) -> pd.DataFrame:
    df = df.copy()
    rng = (df["High"] - df["Low"]).shift(1)
    threshold = df["Open"] + k * rng
    vol_ma = df["Volume"].rolling(20).mean()
    entry = (df["Close"] > threshold) & (df["Volume"] > vol_mult * vol_ma)

    score = (df["Close"] - threshold) / df["Open"]  # how much above breakout
    sig = pd.DataFrame(index=df.index)
    sig["entry"] = entry.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = score.fillna(0.0)
    return sig
