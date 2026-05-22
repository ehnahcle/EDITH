"""
Signal filters — wrappers that further constrain when an entry fires.

A filter is a callable: filter_fn(code, df) -> pd.Series[bool] aligned to df.index.
True at date t means: allow the underlying strategy's entry signal to fire at t.

Filters are intended to be ANDed with strategy signals via `with_signal_filter` or
passed into `make_dispatcher(..., filter_per_regime={...})`.
"""

from __future__ import annotations

import pandas as pd


def with_signal_filter(signal_fn, filter_fn):
    """Wrap signal_fn so its `entry` column is ANDed with filter_fn(code, df)."""
    def wrapped(code: str, df: pd.DataFrame) -> pd.DataFrame:
        sig = signal_fn(code, df)
        if sig is None or sig.empty:
            return sig
        allow = filter_fn(code, df).reindex(sig.index).fillna(False)
        sig = sig.copy()
        sig["entry"] = sig["entry"] & allow
        return sig
    return wrapped


def make_foreign_net_buy_filter(
    flows_by_code: dict[str, pd.DataFrame],
    lookback: int = 5,
    min_krw: float = 0.0,
):
    """Allow entries on day t when rolling-`lookback`-day cumulative
    foreign net buy (KRW) at t is > min_krw.

    Why rolling sum (not raw day-t): single-day flows are noisy. 5-day cumulative
    captures sustained accumulation, which the literature consistently links to
    short-term continuation.

    Tickers missing from `flows_by_code` are blocked (conservative — we'd rather
    skip an entry than enter on stale assumption).
    """

    def f(code: str, df: pd.DataFrame) -> pd.Series:
        flow = flows_by_code.get(code)
        if flow is None or flow.empty or "foreign_net" not in flow.columns:
            return pd.Series(False, index=df.index)
        cum = flow["foreign_net"].rolling(lookback, min_periods=lookback).sum()
        allow = cum > min_krw
        return allow.reindex(df.index).fillna(False)

    return f


def make_foreign_score_booster(
    flows_by_code: dict[str, pd.DataFrame],
    lookback: int = 5,
    zscore_window: int = 60,
    weight: float = 1.0,
    clip: tuple[float, float] = (-2.0, 2.0),
):
    """Additive score adjustment based on standardized foreign net buying.

    For each ticker, compute z-score of rolling-`lookback`-day foreign net buy
    against trailing `zscore_window`-day distribution. Clip to `clip` and scale
    by `weight`. The dispatcher adds this to the strategy's score column, which
    is what the engine ranks simultaneous candidates by (we only have
    max_positions=5 slots, so re-ranking is decisive).

    Tickers missing from flows → return 0 (neutral, no boost or penalty).

    Why additive (not multiplicative): original scores can be negative or near
    zero, and multiplication propagates sign in surprising ways. Additive
    keeps the same units as the strategy's own score.
    """

    def f(code: str, df: pd.DataFrame) -> pd.Series:
        flow = flows_by_code.get(code)
        if flow is None or flow.empty or "foreign_net" not in flow.columns:
            return pd.Series(0.0, index=df.index)
        cum = flow["foreign_net"].rolling(lookback, min_periods=lookback).sum()
        mu = cum.rolling(zscore_window, min_periods=zscore_window).mean()
        sd = cum.rolling(zscore_window, min_periods=zscore_window).std()
        z = ((cum - mu) / sd).clip(*clip)
        adj = (weight * z).fillna(0.0)
        return adj.reindex(df.index).fillna(0.0)

    return f


def make_smart_money_filter(
    flows_by_code: dict[str, pd.DataFrame],
    lookback: int = 5,
):
    """Stricter: foreign AND institutional both net-buying over `lookback` days,
    AND retail net-selling (the "쌍끌이 + 개미 매도" pattern)."""

    def f(code: str, df: pd.DataFrame) -> pd.Series:
        flow = flows_by_code.get(code)
        if flow is None or flow.empty:
            return pd.Series(False, index=df.index)
        f5 = flow["foreign_net"].rolling(lookback, min_periods=lookback).sum()
        i5 = flow["inst_net"].rolling(lookback, min_periods=lookback).sum()
        r5 = flow["retail_net"].rolling(lookback, min_periods=lookback).sum()
        allow = (f5 > 0) & (i5 > 0) & (r5 < 0)
        return allow.reindex(df.index).fillna(False)

    return f
