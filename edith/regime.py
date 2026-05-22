"""
Market regime filter using KOSPI index.

A simple but powerful filter: only allow new long entries when the
KOSPI is in a confirmed uptrend (Close > SMA200 AND SMA50 rising).
This gates new entries without forcing existing positions out.
"""

from __future__ import annotations

import pandas as pd

from .data_loader import get_index


def kospi_regime(start: str, end: str) -> pd.Series:
    """Return bool Series indexed by date: True when bullish regime active."""
    idx = get_index("KS11", start, end)
    if idx.empty:
        return pd.Series(dtype=bool)
    sma50 = idx["Close"].rolling(50).mean()
    sma200 = idx["Close"].rolling(200).mean()
    bullish = (idx["Close"] > sma200) & (sma50 > sma50.shift(5))
    return bullish.fillna(False)
