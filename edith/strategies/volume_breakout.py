"""
Volume spike + bullish candle. Heavy interest often precedes continuation.

Enter when:
  * Volume > 3 * MA20(volume)
  * Close > Open (bullish candle)
  * Close > MA20(close) (avoid downtrends)
"""

from __future__ import annotations

import pandas as pd


def volume_breakout(
    code: str,
    df: pd.DataFrame,
    vol_mult: float = 3.0,
    stop_pct: float = 0.05,
    target_pct: float = 0.08,
    max_hold: int = 4,
) -> pd.DataFrame:
    df = df.copy()
    vol_ma = df["Volume"].rolling(20).mean()
    sma20 = df["Close"].rolling(20).mean()
    cond = (
        (df["Volume"] > vol_mult * vol_ma)
        & (df["Close"] > df["Open"])
        & (df["Close"] > sma20)
    )
    sig = pd.DataFrame(index=df.index)
    sig["entry"] = cond.fillna(False)
    sig["stop_pct"] = stop_pct
    sig["target_pct"] = target_pct
    sig["max_hold"] = max_hold
    sig["score"] = (df["Volume"] / vol_ma).fillna(0.0)
    return sig
