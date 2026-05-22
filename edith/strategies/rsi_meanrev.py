"""
RSI(14) oversold bounce with trend filter.

Enter when RSI(2) < 10 and Close > 200-SMA (no falling-knife).
Inspired by Connors RSI(2) mean reversion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, n: int = 2) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = -delta.clip(upper=0).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def rsi_oversold(
    code: str,
    df: pd.DataFrame,
    rsi_n: int = 2,
    rsi_thresh: float = 10.0,
    sma_n: int = 200,
    stop_pct: float = 0.05,
    target_pct: float = 0.06,
    max_hold: int = 5,
) -> pd.DataFrame:
    df = df.copy()
    rsi = _rsi(df["Close"], rsi_n)
    sma = df["Close"].rolling(sma_n, min_periods=50).mean()
    cond = (rsi < rsi_thresh) & (df["Close"] > sma)
    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = (-rsi).fillna(0.0)
    return sig
