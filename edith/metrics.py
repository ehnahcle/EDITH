"""Performance metrics for equity curves and trade lists."""

from __future__ import annotations

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def equity_to_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().fillna(0.0)


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    return float(equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0


def sharpe(equity: pd.Series, rf: float = 0.0) -> float:
    r = equity_to_returns(equity)
    if r.std() == 0 or len(r) < 2:
        return 0.0
    excess = r - rf / TRADING_DAYS
    return float(excess.mean() / r.std() * np.sqrt(TRADING_DAYS))


def sortino(equity: pd.Series, rf: float = 0.0) -> float:
    r = equity_to_returns(equity)
    downside = r[r < 0]
    if downside.std() == 0 or len(downside) < 2:
        return 0.0
    excess = r.mean() - rf / TRADING_DAYS
    return float(excess / downside.std() * np.sqrt(TRADING_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def calmar(equity: pd.Series) -> float:
    mdd = abs(max_drawdown(equity))
    if mdd == 0:
        return 0.0
    return cagr(equity) / mdd


def summarize(equity: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    out = {
        "CAGR": cagr(equity),
        "Sharpe": sharpe(equity),
        "Sortino": sortino(equity),
        "MDD": max_drawdown(equity),
        "Calmar": calmar(equity),
        "Final": float(equity.iloc[-1] / equity.iloc[0]) if len(equity) else 1.0,
    }
    if trades is not None and not trades.empty:
        wins = trades[trades["pnl_pct"] > 0]
        losses = trades[trades["pnl_pct"] <= 0]
        out.update({
            "N_trades": int(len(trades)),
            "WinRate": float(len(wins) / len(trades)) if len(trades) else 0.0,
            "AvgWin": float(wins["pnl_pct"].mean()) if len(wins) else 0.0,
            "AvgLoss": float(losses["pnl_pct"].mean()) if len(losses) else 0.0,
            "AvgHoldDays": float(trades["hold_days"].mean()) if "hold_days" in trades else 0.0,
            "ProfitFactor": (
                float(wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum()))
                if len(losses) and losses["pnl_pct"].sum() != 0
                else float("inf")
            ),
        })
    return out
