#!/usr/bin/env python
"""
Walk-forward test of STACKING the foreign + institutional score boosters on
the WEAK sleeve, on the production universe (KOSPI top100 + KOSDAQ top50).

The two signals are orthogonal (foreign-z vs inst-z corr ~= -0.08), so summing
their (clipped, weighted) adjustments should add information rather than
double-count. Question: does foreign+inst beat foreign-only (the current
production config)?

  A. Baseline            (no booster)
  B. Foreign only  w0.5  (current production)
  C. Inst only     w0.5
  D. Foreign+Inst  w0.5 each   <- the stack under test
  E. Foreign+Inst  w0.25 each  (de-rated, since two boosters double the tilt)
  F. KOSPI buy-and-hold

Outputs results/combined_booster_summary.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from edith.data_loader import get_top_universe, get_ohlcv_batch, get_index
from edith.investor_flow import get_investor_flow_batch
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.regime import kospi_regime_3tier, STRONG_BULL, WEAK, BEAR
from edith.final_strategy import make_dispatcher
from edith.filters import make_foreign_score_booster, make_inst_score_booster, combine_boosters

START = "2010-01-01"
END = "2024-12-31"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def summarize_period(equity: pd.Series, trades, label: str) -> dict:
    if equity.empty:
        return {"period": label}
    m = summarize(equity, trades)
    m["period"] = label
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
        rows.append(summarize_period(seg_eq, seg_tr if not seg_tr.empty else None, f"{name} {label}"))
    return rows


def main() -> None:
    print(f"== Combined foreign+inst booster: KOSPI {N_KOSPI} + KOSDAQ {N_KOSDAQ}, {START}->{END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    codes = uni["Code"].tolist()
    data = get_ohlcv_batch(codes, START, END)
    flows = get_investor_flow_batch(codes, START, END, sleep=0.0)
    print(f"  OHLCV: {len(data)} | Flows: {len(flows)} tickers")

    regime3 = kospi_regime_3tier(START, END)
    counts = regime3.value_counts().reindex([STRONG_BULL, WEAK, BEAR]).fillna(0).astype(int)
    print(f"  Regime: STRONG_BULL={counts[STRONG_BULL]} / WEAK={counts[WEAK]} / BEAR={counts[BEAR]}")

    cfg = EngineConfig(
        initial_cash=10_000_000.0, max_positions=5, cooldown_days=2,
        regime=pd.Series(True, index=regime3.index),
    )

    f05 = make_foreign_score_booster(flows, lookback=5, zscore_window=60, weight=0.5)
    i05 = make_inst_score_booster(flows, lookback=5, zscore_window=60, weight=0.5)
    f025 = make_foreign_score_booster(flows, lookback=5, zscore_window=60, weight=0.25)
    i025 = make_inst_score_booster(flows, lookback=5, zscore_window=60, weight=0.25)

    runs = {
        "A_Baseline":         make_dispatcher(regime3, enable_bear=True),
        "B_Foreign_w0.5":     make_dispatcher(regime3, enable_bear=True, score_booster_per_regime={WEAK: f05}),
        "C_Inst_w0.5":        make_dispatcher(regime3, enable_bear=True, score_booster_per_regime={WEAK: i05}),
        "D_Fgn+Inst_w0.5":    make_dispatcher(regime3, enable_bear=True,
                                              score_booster_per_regime={WEAK: combine_boosters(f05, i05)}),
        "E_Fgn+Inst_w0.25":   make_dispatcher(regime3, enable_bear=True,
                                              score_booster_per_regime={WEAK: combine_boosters(f025, i025)}),
    }

    all_rows = []
    for name, fn in runs.items():
        print(f"\n--- Running {name} ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        if res.equity.empty:
            continue
        res.equity.to_csv(RESULTS_DIR / f"combined_equity_{name}.csv")
        all_rows.extend(split_metrics(name, res.equity, res.trades))

    kospi = get_index("KS11", START, END)
    bh = kospi["Close"] / kospi["Close"].iloc[0] * 10_000_000.0
    bh.name = "equity"
    all_rows.extend(split_metrics("KOSPI_BH", bh, pd.DataFrame()))

    df = pd.DataFrame(all_rows)
    cols = ["period", "CAGR", "Sharpe", "Sortino", "MDD", "Calmar", "Final",
            "N_trades", "WinRate", "ProfitFactor"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(RESULTS_DIR / "combined_booster_summary.csv", index=False)

    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 200)

    print("\n========== Same-window comparison ==========")
    periods = ["FULL  2010-2024", "IS    2010-2018", "OOS_A 2019-2023", "OOS_B 2024     "]
    names = list(runs.keys()) + ["KOSPI_BH"]
    for p in periods:
        print(f"\n[{p}]")
        for name in names:
            row = df[df["period"] == f"{name} {p}"]
            if row.empty:
                continue
            r = row.iloc[0]
            print(f"  {name:18s}  CAGR {r.get('CAGR',0)*100:7.2f}%  Sharpe {r.get('Sharpe',0):5.2f}  "
                  f"MDD {r.get('MDD',0)*100:6.2f}%  Calmar {r.get('Calmar',0):5.2f}  "
                  f"N_trades {int(r['N_trades']) if pd.notna(r.get('N_trades')) else 0:5d}")


if __name__ == "__main__":
    main()
