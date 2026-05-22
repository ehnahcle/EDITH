#!/usr/bin/env python
"""
Final-config backtest with diagnostics and KOSPI buy-and-hold comparison.

Compares:
  - Momentum5 (tuned)
  - NewHigh52w (tuned)
  - Ensemble (union of both)
  - KOSPI buy-and-hold
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np

from edith.data_loader import get_top_universe, get_ohlcv_batch, get_index
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.regime import kospi_regime
from edith.final_strategy import final_strategy, final_nh, ensemble_strategy


START = "2020-01-01"
END = "2026-05-20"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"


def kospi_buyhold(start: str, end: str) -> pd.Series:
    idx = get_index("KS11", start, end)
    eq = idx["Close"] / idx["Close"].iloc[0] * 10_000_000.0
    eq.name = "equity"
    return eq


def main() -> None:
    print(f"== EDITH FINAL: {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    regime = kospi_regime(START, END)

    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=regime,
    )

    runs = {
        "Momentum5_tuned": final_strategy,
        "NewHigh52w_tuned": final_nh,
        "Ensemble_M+NH": ensemble_strategy,
    }

    summary_rows = []
    equities = {}
    for name, fn in runs.items():
        print(f"\n--- {name} ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        m = summarize(res.equity, res.trades)
        m["Strategy"] = name
        summary_rows.append(m)
        equities[name] = res.equity
        res.equity.to_csv(RESULTS_DIR / f"final_equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"final_trades_{name}.csv", index=False)
        print(
            f"  CAGR {m['CAGR']*100:6.2f}% | Sharpe {m['Sharpe']:.2f} | "
            f"Sortino {m['Sortino']:.2f} | MDD {m['MDD']*100:.2f}% | "
            f"Trades {m.get('N_trades', 0)} | WR {m.get('WinRate', 0)*100:.1f}% | "
            f"AvgHold {m.get('AvgHoldDays', 0):.1f}d | PF {m.get('ProfitFactor', 0):.2f}"
        )

    # KOSPI buy-and-hold benchmark
    bh = kospi_buyhold(START, END)
    bh_aligned = bh.reindex(equities[list(equities)[0]].index, method="ffill")
    m_bh = summarize(bh_aligned)
    m_bh["Strategy"] = "KOSPI_BuyHold"
    summary_rows.append(m_bh)
    equities["KOSPI_BuyHold"] = bh_aligned

    print(
        f"\n--- KOSPI_BuyHold ---\n"
        f"  CAGR {m_bh['CAGR']*100:6.2f}% | Sharpe {m_bh['Sharpe']:.2f} | "
        f"MDD {m_bh['MDD']*100:.2f}%"
    )

    summary = pd.DataFrame(summary_rows).set_index("Strategy")
    cols = ["CAGR", "Sharpe", "Sortino", "Calmar", "MDD", "Final",
            "N_trades", "WinRate", "AvgWin", "AvgLoss", "AvgHoldDays", "ProfitFactor"]
    cols = [c for c in cols if c in summary.columns]
    summary = summary[cols]
    summary.to_csv(RESULTS_DIR / "final_summary.csv")

    print("\n=== FINAL SUMMARY ===")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 160)
    print(summary)

    # Yearly returns table
    print("\n=== Yearly returns ===")
    yearly = pd.DataFrame({k: v.resample("YE").last().pct_change() for k, v in equities.items()})
    yearly.index = yearly.index.year
    print(yearly.applymap(lambda x: f"{x*100:6.2f}%" if pd.notna(x) else "n/a"))

    # IS vs OOS split breakdown
    print("\n=== IS (2020-2023) vs OOS (2024-2026.5) ===")
    split_rows = []
    for name, eq in equities.items():
        is_eq = eq[eq.index < "2024-01-01"]
        oos_eq = eq[eq.index >= "2024-01-01"]
        if is_eq.empty or oos_eq.empty:
            continue
        is_m = summarize(is_eq)
        oos_m = summarize(oos_eq)
        split_rows.append({
            "Strategy": name,
            "IS_CAGR": is_m["CAGR"], "IS_Sharpe": is_m["Sharpe"], "IS_MDD": is_m["MDD"],
            "OOS_CAGR": oos_m["CAGR"], "OOS_Sharpe": oos_m["Sharpe"], "OOS_MDD": oos_m["MDD"],
        })
    print(pd.DataFrame(split_rows).set_index("Strategy"))


if __name__ == "__main__":
    main()
