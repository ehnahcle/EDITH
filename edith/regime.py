"""
Market regime filter using KOSPI index.

A simple but powerful filter: only allow new long entries when the
KOSPI is in a confirmed uptrend (Close > SMA200 AND SMA50 rising).
This gates new entries without forcing existing positions out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Regime labels
STRONG_BULL = "STRONG_BULL"  # strong up-trend → momentum-friendly
WEAK = "WEAK"                # sideways or weak up-trend → mean-reversion friendly
BEAR = "BEAR"                # downtrend → cash / inverse-ETF


def kospi_regime(start: str, end: str) -> pd.Series:
    """Legacy 2-tier regime (True = allow long entries).

    Kept for backward compatibility with the original Momentum5_tuned engine.
    New code should call kospi_regime_3tier() instead.
    """
    from .data_loader import get_index

    idx = get_index("KS11", start, end)
    if idx.empty:
        return pd.Series(dtype=bool)
    sma50 = idx["Close"].rolling(50).mean()
    sma200 = idx["Close"].rolling(200).mean()
    bullish = (idx["Close"] > sma200) & (sma50 > sma50.shift(5))
    return bullish.fillna(False)


def kospi_regime_3tier(start: str, end: str) -> pd.Series:
    """3-tier market regime classification:

      STRONG_BULL: Close > SMA200 AND SMA50 sharply rising AND ROC60 > 5%
      BEAR:        Close < SMA200 (or SMA50 < SMA200)
      WEAK:        everything else (sideways, weak trend)

    Returns a string-valued Series (NOT bool) indexed by date.
    The momentum engine should only enter on STRONG_BULL days;
    a mean-reversion sub-strategy can fire on WEAK days; BEAR is dormant.
    """
    from .data_loader import get_index

    idx = get_index("KS11", start, end)
    if idx.empty:
        return pd.Series(dtype=object)

    close = idx["Close"]
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    # 60-day rate of change as a momentum-strength proxy
    roc60 = close.pct_change(60)
    # Slope of SMA50 over last 20 days, normalised
    sma50_slope = (sma50 - sma50.shift(20)) / sma50.shift(20)

    above_long = close > sma200
    sma_aligned = sma50 > sma200

    strong = (
        above_long
        & sma_aligned
        & (sma50_slope > 0.02)   # SMA50 up >2% in 20 days
        & (roc60 > 0.03)         # KOSPI itself up >3% in 60 days
    )
    bear = (~above_long) | (~sma_aligned)
    # Everything not strong and not bear is WEAK
    weak = ~strong & ~bear

    out = pd.Series(WEAK, index=idx.index, dtype=object)
    out[strong] = STRONG_BULL
    out[bear] = BEAR
    return out


def regime_summary(series: pd.Series) -> pd.DataFrame:
    """Convenience: return counts + pct of days in each regime."""
    counts = series.value_counts().reindex([STRONG_BULL, WEAK, BEAR]).fillna(0).astype(int)
    pct = counts / counts.sum() * 100
    return pd.DataFrame({"days": counts, "pct": pct.round(1)})
