"""
Short-term momentum: 5-day return rank, with 20-day SMA trend filter.

Enter when 5-day return > threshold, price above 20-SMA, and volume rising.
"""

from __future__ import annotations

import pandas as pd


def momentum_5d(
    code: str,
    df: pd.DataFrame,
    ret_thresh: float = 0.05,
    stop_pct: float = 0.05,
    target_pct: float = 0.10,
    max_hold: int = 5,
) -> pd.DataFrame:
    df = df.copy()
    ret5 = df["Close"].pct_change(5)
    sma20 = df["Close"].rolling(20).mean()
    vol_ma = df["Volume"].rolling(20).mean()
    cond = (
        (ret5 > ret_thresh)
        & (df["Close"] > sma20)
        & (df["Volume"] > vol_ma)
    )
    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = ret5.fillna(0.0)
    return sig
