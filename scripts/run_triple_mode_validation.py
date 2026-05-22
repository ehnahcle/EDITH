#!/usr/bin/env python
"""
Triple-mode dispatcher validation (2010-2024).

The dispatcher uses different sub-strategies per regime:
  STRONG_BULL → Momentum5
  WEAK        → NewHigh52w (main alpha)
  BEAR        → Disparity (defensive, optional)

Runs:
  1. Dispatcher with BEAR enabled (full triple-mode)
  2. Dispatcher with BEAR disabled (bull+weak only, dormant in bear)
  3. Legacy Momentum5_tuned single-mode (for comparison)
  4. KOSPI buy-and-hold benchmark

Reports CAGR / Sharpe / MDD per period:
  - FULL 2010-2024
  - IS 2010-2018 (no rally)
  - OOS_A 2019-2023
  - OOS_B 2024
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
from edith.regime import kospi_regime_3tier, STRONG_BULL, WEAK, BEAR
from edith.final_strategy import make_dispatcher, final_strategy


START = "2010-01-01"
END = "2024-12-31"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"


def summarize_period(equity: pd.Series, trades, label: str) -> dict:
    if equity.empty:
        return {"period": label}
    m = summarize(equity, trades)
    m["period"] = label
    m["start"] = str(equity.index[0].date())
    m["end"] = str(equity.index[-1].date())
    return m


def split_metrics(name: str, equity: pd.Series, trades: pd.DataFrame) -> list:
    splits = [
        ("FULL  2010-2024", START, END),
        ("IS    2010-2018", START, "2019-01-01"),
        ("OOS_A 2019-2023", "2019-01-01", "2024-01-01"),
        ("OOS_B 2024     ", "2024-01-01", END),
    ]
    rows = []
    for label, s, e in splits:
        seg_eq = equity[(equity.index >= s) & (equity.index <= e)]
        if trades is not None and not trades.empty:
            seg_tr = trades[(trades["entry_date"] >= s) & (trades["entry_date"] <= e)]
        else:
            seg_tr = pd.DataFrame()
        rows.append(summarize_period(seg_eq, seg_tr if not seg_tr.empty else None,
                                     f"{name} {label}"))
    return rows


def main() -> None:
    print(f"== Triple-mode dispatcher validation: {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    print(f"  {len(data)} tickers")

    regime3 = kospi_regime_3tier(START, END)
    counts = regime3.value_counts().reindex([STRONG_BULL, WEAK, BEAR]).fillna(0).astype(int)
    print(f"  Regime: STRONG_BULL={counts[STRONG_BULL]} / WEAK={counts[WEAK]} / BEAR={counts[BEAR]}")

    # ---- Engine config (dispatcher uses 'always allow' since it gates internally) ----
    always_on = pd.Series(True, index=regime3.index)
    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=always_on,
    )

    runs = {
        "Triple_full":   make_dispatcher(regime3, enable_bear=True),
        "Triple_no_bear": make_dispatcher(regime3, enable_bear=False),
        "Legacy_Momentum5_tuned": final_strategy,
    }

    all_rows = []
    equities = {}
    for name, fn in runs.items():
        print(f"\n--- Running {name} ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        if res.equity.empty:
            continue
        equities[name] = res.equity
        res.equity.to_csv(RESULTS_DIR / f"triple_equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"triple_trades_{name}.csv", index=False)
        all_rows.extend(split_metrics(name, res.equity, res.trades))

    # ---- KOSPI benchmark ----
    print("\n--- KOSPI buy-and-hold benchmark ---")
    kospi = get_index("KS11", START, END)
    bh = kospi["Close"] / kospi["Close"].iloc[0] * 10_000_000.0
    bh.name = "equity"
    equities["KOSPI_BH"] = bh
    all_rows.extend(split_metrics("KOSPI_BH", bh, pd.DataFrame()))

    df = pd.DataFrame(all_rows)
    cols = ["period", "start", "end", "CAGR", "Sharpe", "Sortino", "MDD", "Final",
            "N_trades", "WinRate", "ProfitFactor"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    df.to_csv(RESULTS_DIR / "triple_summary.csv", index=False)

    print("\n========== Triple-mode validation summary ==========")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print(df.to_string(index=False))

    print("\n========== Same-window comparison ==========")
    periods = ["FULL  2010-2024", "IS    2010-2018", "OOS_A 2019-2023", "OOS_B 2024     "]
    for p in periods:
        print(f"\n[{p}]")
        for name in ["Triple_full", "Triple_no_bear", "Legacy_Momentum5_tuned", "KOSPI_BH"]:
            row = df[df["period"] == f"{name} {p}"]
            if row.empty:
                continue
            r = row.iloc[0]
            cagr = r.get("CAGR", 0)
            sh = r.get("Sharpe", 0)
            mdd = r.get("MDD", 0)
            print(f"  {name:30s}  CAGR {cagr*100:7.2f}%  Sharpe {sh:5.2f}  MDD {mdd*100:6.2f}%")


if __name__ == "__main__":
    main()
