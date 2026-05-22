#!/usr/bin/env python
"""
Phase 1: Fetch universe + OHLCV, then run all 7 candidate strategies
across KOSPI200 + KOSDAQ150 over the past ~5 years.

Outputs:
  results/summary.csv             - one row per strategy with metrics
  results/equity_<strat>.csv      - daily equity for each strategy
  results/trades_<strat>.csv      - trade log per strategy
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from edith.data_loader import get_top_universe, get_ohlcv_batch
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.strategies import STRATEGIES


START = "2020-01-01"
END = "2026-05-20"
N_KOSPI = 100   # top 100 KOSPI by mcap
N_KOSDAQ = 50   # top 50 KOSDAQ
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def main() -> None:
    print(f"== EDITH backtest: {START} -> {END} ==")
    print(f"Universe: top {N_KOSPI} KOSPI + top {N_KOSDAQ} KOSDAQ by market cap")

    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    print(f"Loaded universe: {len(uni)} names")

    print("Fetching OHLCV (cached after first run)...")
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    print(f"Fetched {len(data)} non-empty frames")

    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
    )

    rows = []
    for name, fn in STRATEGIES.items():
        print(f"\n--- {name} ---")
        try:
            res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name} failed: {e}")
            continue
        if res.equity.empty:
            print(f"  ! {name} produced no equity curve")
            continue
        m = summarize(res.equity, res.trades)
        m["Strategy"] = name
        rows.append(m)
        res.equity.to_csv(RESULTS_DIR / f"equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"trades_{name}.csv", index=False)
        print(
            f"  CAGR {m['CAGR']*100:.2f}% | Sharpe {m['Sharpe']:.2f} | "
            f"MDD {m['MDD']*100:.2f}% | Trades {m.get('N_trades', 0)} | "
            f"WinRate {m.get('WinRate', 0)*100:.1f}%"
        )

    if not rows:
        print("\nNo strategies produced results.")
        return

    summary = pd.DataFrame(rows).set_index("Strategy")
    cols = ["CAGR", "Sharpe", "Sortino", "Calmar", "MDD", "Final",
            "N_trades", "WinRate", "AvgWin", "AvgLoss", "AvgHoldDays", "ProfitFactor"]
    cols = [c for c in cols if c in summary.columns]
    summary = summary[cols].sort_values("Sharpe", ascending=False)
    summary.to_csv(RESULTS_DIR / "summary.csv")

    print("\n=== Summary (sorted by Sharpe) ===")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 160)
    print(summary)
    print(f"\nSaved -> {RESULTS_DIR / 'summary.csv'}")


if __name__ == "__main__":
    main()
