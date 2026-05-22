#!/usr/bin/env python
"""
Grid search tuning on top 3 (RSIOversold, NewHigh52w, Momentum5) with
walk-forward split:
  - In-sample (IS):  2020-01-01 -> 2023-12-31
  - Out-of-sample (OOS): 2024-01-01 -> 2026-05-20

Ranks IS by Sharpe. Reports OOS metrics for the top-K configs of each strategy
to detect overfit.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from edith.data_loader import get_top_universe, get_ohlcv_batch
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.regime import kospi_regime

from edith.strategies.rsi_meanrev import rsi_oversold
from edith.strategies.new_high_52w import new_high_52w
from edith.strategies.momentum import momentum_5d


START = "2020-01-01"
END = "2026-05-20"
SPLIT = "2024-01-01"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def make_fn(base_fn, **params):
    def _fn(code, df):
        return base_fn(code, df, **params)
    _fn.__name__ = base_fn.__name__
    return _fn


def grid_search(name, base_fn, grid, data, regime):
    print(f"\n=== Grid: {name} ({len(grid)} combos) ===")
    rows = []
    for i, params in enumerate(grid, 1):
        fn = make_fn(base_fn, **params)
        cfg_is = EngineConfig(max_positions=5, cooldown_days=2, regime=regime)
        res_is = run_backtest(data, fn, cfg=cfg_is, start=START, end=SPLIT)
        m_is = summarize(res_is.equity, res_is.trades)

        cfg_oos = EngineConfig(max_positions=5, cooldown_days=2, regime=regime)
        res_oos = run_backtest(data, fn, cfg=cfg_oos, start=SPLIT, end=END)
        m_oos = summarize(res_oos.equity, res_oos.trades)

        row = {"strategy": name}
        row.update({f"p_{k}": v for k, v in params.items()})
        row["IS_CAGR"] = m_is["CAGR"]
        row["IS_Sharpe"] = m_is["Sharpe"]
        row["IS_MDD"] = m_is["MDD"]
        row["IS_PF"] = m_is.get("ProfitFactor", 0)
        row["IS_N"] = m_is.get("N_trades", 0)
        row["IS_WR"] = m_is.get("WinRate", 0)
        row["OOS_CAGR"] = m_oos["CAGR"]
        row["OOS_Sharpe"] = m_oos["Sharpe"]
        row["OOS_MDD"] = m_oos["MDD"]
        row["OOS_PF"] = m_oos.get("ProfitFactor", 0)
        row["OOS_N"] = m_oos.get("N_trades", 0)
        row["OOS_WR"] = m_oos.get("WinRate", 0)
        rows.append(row)
        if i % 10 == 0 or i == len(grid):
            print(f"  [{i}/{len(grid)}]")
    return pd.DataFrame(rows)


def main() -> None:
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    regime = kospi_regime(START, END)
    print(f"Universe: {len(data)} tickers, regime ON {int(regime.sum())}/{len(regime)} days")

    # ---- RSIOversold grid ----
    rsi_grid = [
        dict(rsi_n=n, rsi_thresh=t, stop_pct=s, target_pct=tg, max_hold=h)
        for n, t, s, tg, h in itertools.product(
            [2, 3],
            [5, 10, 15],
            [0.03, 0.05, 0.07],
            [0.05, 0.07, 0.10],
            [3, 5, 7],
        )
    ]
    df_rsi = grid_search("RSIOversold", rsi_oversold, rsi_grid, data, regime)

    # ---- NewHigh52w grid ----
    nh_grid = [
        dict(lookback=lb, vol_mult=v, stop_pct=s, target_pct=tg, max_hold=h)
        for lb, v, s, tg, h in itertools.product(
            [120, 252],
            [1.2, 1.5, 2.0],
            [0.05, 0.07, 0.10],
            [0.10, 0.15, 0.20],
            [7, 10, 14],
        )
    ]
    df_nh = grid_search("NewHigh52w", new_high_52w, nh_grid, data, regime)

    # ---- Momentum5 grid ----
    mo_grid = [
        dict(ret_thresh=r, stop_pct=s, target_pct=tg, max_hold=h)
        for r, s, tg, h in itertools.product(
            [0.03, 0.05, 0.07, 0.10],
            [0.03, 0.05, 0.07],
            [0.06, 0.10, 0.15],
            [3, 5, 7],
        )
    ]
    df_mo = grid_search("Momentum5", momentum_5d, mo_grid, data, regime)

    all_df = pd.concat([df_rsi, df_nh, df_mo], ignore_index=True)
    all_df.to_csv(RESULTS_DIR / "tuning_results.csv", index=False)

    print("\n========== TOP 5 IS Sharpe per strategy ==========")
    for s in all_df["strategy"].unique():
        sub = all_df[all_df["strategy"] == s].sort_values("IS_Sharpe", ascending=False).head(5)
        print(f"\n--- {s} ---")
        print(sub.to_string(index=False))

    print("\n========== TOP 5 OOS Sharpe per strategy ==========")
    for s in all_df["strategy"].unique():
        sub = all_df[all_df["strategy"] == s].sort_values("OOS_Sharpe", ascending=False).head(5)
        print(f"\n--- {s} ---")
        print(sub.to_string(index=False))

    # Robust pick: rank by combined IS+OOS Sharpe, requiring OOS_PF > 1 and OOS_N >= 30
    robust = all_df[(all_df["OOS_PF"] > 1.0) & (all_df["OOS_N"] >= 30)].copy()
    if not robust.empty:
        robust["Combined"] = robust["IS_Sharpe"] * 0.4 + robust["OOS_Sharpe"] * 0.6
        robust = robust.sort_values("Combined", ascending=False)
        print("\n========== ROBUST PICKS (OOS_PF>1, OOS_N>=30) ==========")
        print(robust.head(10).to_string(index=False))
        robust.head(20).to_csv(RESULTS_DIR / "tuning_robust_top.csv", index=False)


if __name__ == "__main__":
    main()
