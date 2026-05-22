"""
Final EDITH strategy: Momentum-5d (tuned).

Selected via grid search with walk-forward validation.

  ret_thresh = 0.10  (5-day return >= 10%)
  stop_pct   = 0.03  (-3% stop)
  target_pct = 0.15  (+15% target)
  max_hold   = 5     (5 bars)

Rationale:
  - Top robust pick: in-sample Sharpe 0.90, out-of-sample Sharpe 1.72,
    combined Sharpe 1.39 (weighted 0.4 IS + 0.6 OOS).
  - Param stability: multiple neighbors of (stop=0.03, target=0.15) cluster
    in the top 10, suggesting it is not a knife-edge optimum.
  - Asymmetric R:R = 5:1; ~29% win rate, ~8-9% avg win, ~3% avg loss, PF ~1.4.

Filters used:
  - 5-day return > 10%
  - Close > SMA20 (uptrend confirm)
  - Volume > MA20(volume) (interest confirm)
  - Engine-level KOSPI regime gate (Close>SMA200 & SMA50 rising)
"""

from __future__ import annotations

import pandas as pd

from .strategies.momentum import momentum_5d
from .strategies.new_high_52w import new_high_52w


FINAL_PARAMS_MOMENTUM = dict(
    ret_thresh=0.10,
    stop_pct=0.03,
    target_pct=0.15,
    max_hold=5,
)

# Secondary (used in ensemble check)
FINAL_PARAMS_NH = dict(
    lookback=252,
    vol_mult=1.2,
    stop_pct=0.05,
    target_pct=0.20,
    max_hold=7,
)


def final_strategy(code: str, df: pd.DataFrame) -> pd.DataFrame:
    return momentum_5d(code, df, **FINAL_PARAMS_MOMENTUM)


def final_nh(code: str, df: pd.DataFrame) -> pd.DataFrame:
    return new_high_52w(code, df, **FINAL_PARAMS_NH)


def ensemble_strategy(code: str, df: pd.DataFrame) -> pd.DataFrame:
    """Union of momentum and 52w-high entries; each row inherits the
    triggering strategy's stop/target/hold. Momentum wins on overlap."""
    m = momentum_5d(code, df, **FINAL_PARAMS_MOMENTUM)
    n = new_high_52w(code, df, **FINAL_PARAMS_NH)
    out = m.copy()
    only_n = n["entry"] & (~m["entry"])
    out.loc[only_n, "entry"] = True
    for col in ["stop_pct", "target_pct", "max_hold", "score"]:
        out.loc[only_n, col] = n.loc[only_n, col]
    return out
