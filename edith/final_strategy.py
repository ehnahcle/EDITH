"""
Final EDITH strategy — Triple-Mode Dispatcher.

Selected via 2010-2024 per-regime grid search (444 runs).
Each market regime gets its own optimised sub-strategy:

  STRONG_BULL  → Momentum5 (ret=0.10, s=0.03, t=0.15, h=7)
                 Sharpe 0.27 within regime. Modest, but matches regime nature.

  WEAK         → NewHigh52w (vol=1.2, s=0.07, t=0.20, h=10)  ⭐ main alpha
                 Sharpe 0.72 within regime. Sideways markets still produce
                 a few real breakouts; this catches them.

  BEAR         → Disparity (d=-0.10, s=0.03, t=0.10, h=3)
                 Sharpe 0.40 within regime. Short, defensive — buys -10%
                 dips, quick exits. MDD only -8.8%.

Legacy single-strategy mode (Momentum5_tuned) is kept under
`final_strategy` for backward compatibility with old backtest scripts.
The new dispatcher is `dispatched_strategy(regime3_series)`.
"""

from __future__ import annotations

import pandas as pd

from .strategies.momentum import momentum_5d
from .strategies.new_high_52w import new_high_52w
from .strategies.disparity import disparity_meanrev
from .regime import STRONG_BULL, WEAK, BEAR


# ---- Per-regime parameter tables (frozen from 2010-2024 search) ----
PARAMS_STRONG_BULL = dict(
    ret_thresh=0.10,
    stop_pct=0.03,
    target_pct=0.15,
    max_hold=7,
)

PARAMS_WEAK = dict(
    lookback=252,
    vol_mult=1.2,
    stop_pct=0.07,
    target_pct=0.20,
    max_hold=10,
)

PARAMS_BEAR = dict(
    thresh=-0.10,
    stop_pct=0.03,
    target_pct=0.10,
    max_hold=3,
)

# ---- Legacy aliases (kept so existing dashboard/scripts don't break) ----
FINAL_PARAMS_MOMENTUM = dict(
    ret_thresh=0.10,
    stop_pct=0.03,
    target_pct=0.15,
    max_hold=5,
)
FINAL_PARAMS_NH = dict(
    lookback=252,
    vol_mult=1.2,
    stop_pct=0.05,
    target_pct=0.20,
    max_hold=7,
)


# ---------------------------------------------------------------------------
# Legacy single-strategy entries
# ---------------------------------------------------------------------------

def final_strategy(code: str, df: pd.DataFrame) -> pd.DataFrame:
    """Legacy: Momentum5 with the v1 parameters. Use make_dispatcher() for new code."""
    return momentum_5d(code, df, **FINAL_PARAMS_MOMENTUM)


def final_nh(code: str, df: pd.DataFrame) -> pd.DataFrame:
    return new_high_52w(code, df, **FINAL_PARAMS_NH)


def ensemble_strategy(code: str, df: pd.DataFrame) -> pd.DataFrame:
    m = momentum_5d(code, df, **FINAL_PARAMS_MOMENTUM)
    n = new_high_52w(code, df, **FINAL_PARAMS_NH)
    out = m.copy()
    only_n = n["entry"] & (~m["entry"])
    out.loc[only_n, "entry"] = True
    for col in ["stop_pct", "target_pct", "max_hold", "score"]:
        out.loc[only_n, col] = n.loc[only_n, col]
    return out


# ---------------------------------------------------------------------------
# New: 3-mode dispatcher
# ---------------------------------------------------------------------------

def make_dispatcher(regime3: pd.Series, enable_bear: bool = True):
    """Return a signal_fn(code, df) that selects the per-regime sub-strategy
    based on the value of `regime3` on each date.

    regime3 must be a string-valued Series (values STRONG_BULL / WEAK / BEAR)
    indexed by date.  Days missing from the series are treated as no-entry.

    enable_bear: if False, the BEAR regime stays dormant (no entries).
                 Set False for the cautious profile (mainly STRONG_BULL+WEAK).
    """
    regime3 = regime3.copy()

    def _dispatch(code: str, df: pd.DataFrame) -> pd.DataFrame:
        # Build all 3 sub-signals upfront, then mask by regime.
        sig_m = momentum_5d(code, df, **PARAMS_STRONG_BULL)
        sig_n = new_high_52w(code, df, **PARAMS_WEAK)
        sig_d = disparity_meanrev(code, df, **PARAMS_BEAR)

        # Align regime to this ticker's dates
        reg = regime3.reindex(df.index).fillna("NONE")

        # Start with an empty entry mask, then fill in by regime
        out = pd.DataFrame(index=df.index)
        out["entry"] = False
        out["stop_pct"] = 0.05
        out["target_pct"] = 0.10
        out["max_hold"] = 5
        out["score"] = 0.0

        # STRONG_BULL → momentum
        m_bull = (reg == STRONG_BULL) & sig_m["entry"]
        if m_bull.any():
            out.loc[m_bull, "entry"] = True
            out.loc[m_bull, "stop_pct"] = PARAMS_STRONG_BULL["stop_pct"]
            out.loc[m_bull, "target_pct"] = PARAMS_STRONG_BULL["target_pct"]
            out.loc[m_bull, "max_hold"] = PARAMS_STRONG_BULL["max_hold"]
            out.loc[m_bull, "score"] = sig_m.loc[m_bull, "score"]

        # WEAK → new high 52w
        m_weak = (reg == WEAK) & sig_n["entry"]
        if m_weak.any():
            out.loc[m_weak, "entry"] = True
            out.loc[m_weak, "stop_pct"] = PARAMS_WEAK["stop_pct"]
            out.loc[m_weak, "target_pct"] = PARAMS_WEAK["target_pct"]
            out.loc[m_weak, "max_hold"] = PARAMS_WEAK["max_hold"]
            out.loc[m_weak, "score"] = sig_n.loc[m_weak, "score"]

        # BEAR → disparity (optional)
        if enable_bear:
            m_bear = (reg == BEAR) & sig_d["entry"]
            if m_bear.any():
                out.loc[m_bear, "entry"] = True
                out.loc[m_bear, "stop_pct"] = PARAMS_BEAR["stop_pct"]
                out.loc[m_bear, "target_pct"] = PARAMS_BEAR["target_pct"]
                out.loc[m_bear, "max_hold"] = PARAMS_BEAR["max_hold"]
                out.loc[m_bear, "score"] = sig_d.loc[m_bear, "score"]

        return out

    return _dispatch


def regime_for_today(regime3: pd.Series) -> str:
    """Helper: return today's regime label (or 'NONE' if no data)."""
    if regime3.empty:
        return "NONE"
    return str(regime3.iloc[-1])


def params_for_regime(regime: str) -> dict:
    """Return the params dict + strategy name for a regime label."""
    if regime == STRONG_BULL:
        return {"strategy": "Momentum5", **PARAMS_STRONG_BULL}
    if regime == WEAK:
        return {"strategy": "NewHigh52w", **PARAMS_WEAK}
    if regime == BEAR:
        return {"strategy": "Disparity", **PARAMS_BEAR}
    return {"strategy": "DORMANT"}
