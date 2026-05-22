"""
Disparity (이격도) mean reversion.

Disparity = Close / SMA20 - 1. When < -5% and the broader uptrend
(Close > SMA60) holds, buy the dip.
"""

from __future__ import annotations

import pandas as pd


def disparity_meanrev(
    code: str,
    df: pd.DataFrame,
    sma_short: int = 20,
    sma_long: int = 60,
    thresh: float = -0.05,
    stop_pct: float = 0.04,
    target_pct: float = 0.06,
    max_hold: int = 5,
) -> pd.DataFrame:
    df = df.copy()
    sma_s = df["Close"].rolling(sma_short).mean()
    sma_l = df["Close"].rolling(sma_long).mean()
    disparity = df["Close"] / sma_s - 1
    cond = (disparity < thresh) & (df["Close"] > sma_l)
    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = (-disparity).fillna(0.0)
    return sig
