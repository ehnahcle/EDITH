"""
Gap-down reversal (mean reversion).

If a stock gaps down >= 3% but closes above its open (hammer-like),
expect a bounce. Enter next open, tight stop, modest target.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def gap_reversal(
    code: str,
    df: pd.DataFrame,
    gap_pct: float = 0.03,
    stop_pct: float = 0.035,
    target_pct: float = 0.06,
    max_hold: int = 3,
) -> pd.DataFrame:
    df = df.copy()
    prev_close = df["Close"].shift(1)
    gap = (df["Open"] - prev_close) / prev_close
    bullish = df["Close"] > df["Open"]
    cond = (gap <= -gap_pct) & bullish & (df["Volume"] > df["Volume"].rolling(20).mean())

    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = (-gap).fillna(0.0)  # bigger gap-down = stronger bounce candidate
    return sig
