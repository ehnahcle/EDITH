#!/usr/bin/env python
"""
v2: Same 7 strategies, gated by KOSPI bull-market regime.

Only allow new entries when KOSPI > SMA200 and SMA50 is rising.
Existing positions still managed normally (stop/target/time).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from edith.data_loader import get_top_universe, get_ohlcv_batch
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.strategies import STRATEGIES
from edith.regime import kospi_regime


START = "2020-01-01"
END = "2026-05-20"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def main() -> None:
    print(f"== EDITH v2 (regime-gated): {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    print(f"Loaded {len(data)} tickers")

    print("Computing KOSPI regime...")
    regime = kospi_regime(START, END)
    on_days = int(regime.sum())
    total = len(regime)
    print(f"  Regime ON: {on_days}/{total} days ({on_days/total*100:.1f}%)")

    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=regime,
    )

    rows = []
    for name, fn in STRATEGIES.items():
        print(f"\n--- {name} (v2) ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        if res.equity.empty:
            continue
        m = summarize(res.equity, res.trades)
        m["Strategy"] = name
        rows.append(m)
        res.equity.to_csv(RESULTS_DIR / f"v2_equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"v2_trades_{name}.csv", index=False)
        print(
            f"  CAGR {m['CAGR']*100:.2f}% | Sharpe {m['Sharpe']:.2f} | "
            f"MDD {m['MDD']*100:.2f}% | Trades {m.get('N_trades', 0)} | "
            f"WinRate {m.get('WinRate', 0)*100:.1f}% | PF {m.get('ProfitFactor', 0):.2f}"
        )

    summary = pd.DataFrame(rows).set_index("Strategy")
    cols = ["CAGR", "Sharpe", "Sortino", "Calmar", "MDD", "Final",
            "N_trades", "WinRate", "AvgWin", "AvgLoss", "AvgHoldDays", "ProfitFactor"]
    cols = [c for c in cols if c in summary.columns]
    summary = summary[cols].sort_values("Sharpe", ascending=False)
    summary.to_csv(RESULTS_DIR / "v2_summary.csv")

    print("\n=== v2 Summary (sorted by Sharpe) ===")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 160)
    print(summary)


if __name__ == "__main__":
    main()
