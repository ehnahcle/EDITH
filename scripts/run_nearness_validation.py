#!/usr/bin/env python
"""
Walk-forward validation of the WEAK-sleeve 52w trigger redesign.

The dispatcher's main alpha comes from the WEAK regime (NewHigh52w). The
current trigger requires a *hard break* of the prior 52-week high. This
study asks two questions:

  1. Does entering when price is merely *near* the 52w high (a nearness
     percentile) beat waiting for the break? Anticipation vs. chasing.
  2. Does skipping January entries help (turn-of-year seasonality)?

Four trigger families for the WEAK sleeve, all with the foreign-score
booster ON (weight=0.5, the production setting), so we isolate the trigger:

  A  break               price > 52w_high                  (current behavior)
  B  nearness 0.95/0.97/0.99   price/52w_high >= pct
  C  nearness + Jan skip  B and entries not in January
  D  break + Jan skip     A and entries not in January      (control: isolates
                          the January effect from the nearness effect)

STRONG_BULL (Momentum5) and BEAR (Disparity) sleeves are untouched.
Walk-forward 2010-2024. Outputs results/nearness_summary.csv.
"""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from edith.data_loader import get_top_universe, get_ohlcv_batch, get_index
from edith.investor_flow import get_investor_flow_batch
from edith.backtest import run_backtest, EngineConfig
from edith.metrics import summarize
from edith.regime import kospi_regime_3tier, STRONG_BULL, WEAK, BEAR
from edith.final_strategy import make_dispatcher, PARAMS_WEAK
from edith.filters import make_foreign_score_booster
from edith.strategies.new_high_52w import new_high_52w


START = "2010-01-01"
END = "2024-12-31"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def weak_variant(near_pct: float | None, jan_skip: bool):
    """Bind a WEAK-sleeve NewHigh52w with the frozen PARAMS_WEAK plus the
    nearness / January-skip overrides under test."""
    return partial(
        new_high_52w,
        lookback=PARAMS_WEAK["lookback"],
        vol_mult=PARAMS_WEAK["vol_mult"],
        stop_pct=PARAMS_WEAK["stop_pct"],
        target_pct=PARAMS_WEAK["target_pct"],
        max_hold=PARAMS_WEAK["max_hold"],
        near_pct=near_pct,
        jan_skip=jan_skip,
    )


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
        rows.append(
            summarize_period(
                seg_eq,
                seg_tr if not seg_tr.empty else None,
                f"{name} {label}",
            )
        )
    return rows


def main() -> None:
    print(f"== Nearness / Jan-skip validation: {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    codes = uni["Code"].tolist()
    data = get_ohlcv_batch(codes, START, END)
    print(f"  OHLCV: {len(data)} tickers")

    flows = get_investor_flow_batch(codes, START, END, sleep=0.0)
    print(f"  Flows: {len(flows)} tickers")

    regime3 = kospi_regime_3tier(START, END)
    counts = regime3.value_counts().reindex([STRONG_BULL, WEAK, BEAR]).fillna(0).astype(int)
    print(f"  Regime: STRONG_BULL={counts[STRONG_BULL]} / WEAK={counts[WEAK]} / BEAR={counts[BEAR]}")

    always_on = pd.Series(True, index=regime3.index)
    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=always_on,
    )

    # Foreign booster ON for WEAK (production setting), held constant across runs.
    boost_weak = make_foreign_score_booster(flows, lookback=5, zscore_window=60, weight=0.5)
    booster = {WEAK: boost_weak}

    def disp(near_pct, jan_skip):
        return make_dispatcher(
            regime3,
            enable_bear=True,
            score_booster_per_regime=booster,
            weak_fn=weak_variant(near_pct, jan_skip),
        )

    runs = {
        "A_break":            disp(None, False),
        "B_near0.95":         disp(0.95, False),
        "B_near0.97":         disp(0.97, False),
        "B_near0.99":         disp(0.99, False),
        "C_near0.95_janskip": disp(0.95, True),
        "C_near0.97_janskip": disp(0.97, True),
        "C_near0.99_janskip": disp(0.99, True),
        "D_break_janskip":    disp(None, True),
    }

    all_rows = []
    for name, fn in runs.items():
        print(f"\n--- Running {name} ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        if res.equity.empty:
            print("  (empty equity, skipped)")
            continue
        res.equity.to_csv(RESULTS_DIR / f"nearness_equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"nearness_trades_{name}.csv", index=False)
        all_rows.extend(split_metrics(name, res.equity, res.trades))

    print("\n--- KOSPI buy-and-hold benchmark ---")
    kospi = get_index("KS11", START, END)
    bh = kospi["Close"] / kospi["Close"].iloc[0] * 10_000_000.0
    bh.name = "equity"
    all_rows.extend(split_metrics("KOSPI_BH", bh, pd.DataFrame()))

    df = pd.DataFrame(all_rows)
    cols = ["period", "start", "end", "CAGR", "Sharpe", "Sortino", "MDD", "Calmar",
            "Final", "N_trades", "WinRate", "ProfitFactor"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    df.to_csv(RESULTS_DIR / "nearness_summary.csv", index=False)

    print("\n========== Nearness validation summary ==========")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    print(df.to_string(index=False))

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
            cagr = r.get("CAGR", 0)
            sh = r.get("Sharpe", 0)
            mdd = r.get("MDD", 0)
            ntr = r.get("N_trades", 0)
            print(f"  {name:20s}  CAGR {cagr*100:7.2f}%  Sharpe {sh:5.2f}  "
                  f"MDD {mdd*100:6.2f}%  N_trades {int(ntr) if pd.notna(ntr) else 0:5d}")


if __name__ == "__main__":
    main()
