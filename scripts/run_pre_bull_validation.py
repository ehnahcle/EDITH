#!/usr/bin/env python
"""
Pre-bull-market validation.

The current dashboard summary covers 2020-01 to 2026-05, but most of the
OOS Sharpe (1.70) comes from the explosive 2025-2026 KOSPI rally.
This script re-runs Momentum5_tuned across a longer window (2010-2024)
that includes multiple regimes:

  - 2010-2011 strong bull
  - 2012-2014 sideways
  - 2015 China shock
  - 2016-2017 mild bull
  - 2018 US-China trade war (-17%)
  - 2019 mild
  - 2020 COVID crash + V-recovery
  - 2021 bull
  - 2022 bear (-25%)
  - 2023 mild recovery
  - 2024 weak (-9.6%)

If the same parameters hold a positive Sharpe across this richer mix
WITHOUT the 2025+ rally, the strategy is genuinely robust.

Caveat: we use the CURRENT top-150 universe (KOSPI100 + KOSDAQ50). Some of
these names didn't exist or were tiny in 2010. That introduces survivorship
bias — newer / explosive winners are over-represented. Treat results as
"upper-bound under survivorship", not pristine.

Outputs:
  results/prebull_summary.csv
  results/prebull_equity.csv
  results/prebull_trades.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from edith.data_loader import get_top_universe, get_ohlcv_batch, get_index
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.regime import kospi_regime
from edith.final_strategy import final_strategy


START = "2010-01-01"
END = "2024-12-31"           # cut OFF before the 2025+ rally
SPLIT_1 = "2019-01-01"       # IS / OOS_A boundary
SPLIT_2 = "2024-01-01"       # OOS_A / OOS_B boundary
RESULTS_DIR = ROOT / "results"
N_KOSPI = 100
N_KOSDAQ = 50


def summarize_period(equity: pd.Series, trades: pd.DataFrame, label: str) -> dict:
    if equity.empty:
        return {"period": label}
    m = summarize(equity, trades)
    m["period"] = label
    m["start"] = str(equity.index[0].date())
    m["end"] = str(equity.index[-1].date())
    return m


def main() -> None:
    print(f"== Pre-bull validation: {START} -> {END} (excludes 2025+ rally) ==")

    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    print(f"Universe: {len(uni)} tickers (note: current top-N, survivorship bias)")

    print("Fetching OHLCV (may take 2-3 min on first run)...")
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    print(f"  Got {len(data)} non-empty frames")
    # Note: many tickers will have shorter history (only data from listing date)

    # Build extended KOSPI regime
    regime = kospi_regime(START, END)
    print(f"Regime ON: {int(regime.sum())}/{len(regime)} days ({regime.mean()*100:.1f}%)")

    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=regime,
    )

    # ---------- Full run ----------
    print("\n--- Running Momentum5_tuned across full extended period ---")
    res = run_backtest(data, final_strategy, cfg=cfg, start=START, end=END)
    if res.equity.empty:
        print("! Empty equity curve")
        return

    res.equity.to_csv(RESULTS_DIR / "prebull_equity.csv")
    res.trades.to_csv(RESULTS_DIR / "prebull_trades.csv", index=False)

    # ---------- Split into segments ----------
    splits = [
        ("FULL  2010-2024", START, END),
        ("IS    2010-2018", START, SPLIT_1),
        ("OOS_A 2019-2023", SPLIT_1, SPLIT_2),
        ("OOS_B 2024     ", SPLIT_2, END),
    ]

    # KOSPI benchmark
    kospi = get_index("KS11", START, END)
    bh_equity = kospi["Close"] / kospi["Close"].iloc[0] * 10_000_000.0
    bh_equity.name = "equity"

    rows = []
    for label, s, e in splits:
        seg_eq = res.equity[(res.equity.index >= s) & (res.equity.index <= e)]
        seg_tr = res.trades[
            (res.trades["entry_date"] >= s) & (res.trades["entry_date"] <= e)
        ] if not res.trades.empty else pd.DataFrame()
        row = summarize_period(seg_eq, seg_tr, "EDITH " + label)
        rows.append(row)

        # KOSPI BH benchmark same window
        bh_seg = bh_equity[(bh_equity.index >= s) & (bh_equity.index <= e)]
        if not bh_seg.empty:
            row_bh = summarize_period(bh_seg, None, "KOSPI " + label)
            rows.append(row_bh)

    df = pd.DataFrame(rows)
    cols = ["period", "start", "end", "CAGR", "Sharpe", "Sortino", "MDD", "Final",
            "N_trades", "WinRate", "AvgWin", "AvgLoss", "ProfitFactor"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    df.to_csv(RESULTS_DIR / "prebull_summary.csv", index=False)

    print("\n=== Pre-bull validation summary ===")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print(df.to_string(index=False))

    # ---------- Side-by-side decision aid ----------
    print("\n=== Same-window EDITH vs KOSPI ===")
    for label, s, e in splits:
        edith_row = df[df["period"] == "EDITH " + label]
        kospi_row = df[df["period"] == "KOSPI " + label]
        if edith_row.empty or kospi_row.empty:
            continue
        es = edith_row.iloc[0]
        ks = kospi_row.iloc[0]
        print(
            f"{label}:  "
            f"EDITH CAGR {es['CAGR']*100:6.1f}% / Sharpe {es['Sharpe']:5.2f} / MDD {es['MDD']*100:6.1f}%  ||  "
            f"KOSPI CAGR {ks['CAGR']*100:6.1f}% / Sharpe {ks['Sharpe']:5.2f} / MDD {ks['MDD']*100:6.1f}%  "
            f"=> alpha {(es['CAGR']-ks['CAGR'])*100:+.1f}%p"
        )


if __name__ == "__main__":
    main()
