#!/usr/bin/env python
"""
Walk-forward validation of the foreign-net-buy filter applied to the
triple-mode dispatcher.

Compares 5 runs over 2010-2024 with the same engine + cost + universe:

  A. Baseline           — Triple_full (no filter)               [reference]
  B. Foreign(WEAK only) — filter NewHigh52w entries only        [main hypothesis]
  C. Foreign(all)       — filter all 3 regimes' entries
  D. SmartMoney(WEAK)   — foreign+inst buy + retail sell, WEAK only
  E. KOSPI buy-and-hold benchmark

Outputs per-period (FULL / IS / OOS_A / OOS_B) CAGR/Sharpe/MDD/N_trades to
results/foreign_filter_summary.csv and prints a side-by-side comparison.

Decision rule per memory: keep the filter if Sharpe improves materially
without trade-count collapse (need > ~30 trades per OOS period).
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
from edith.filters import make_foreign_net_buy_filter, make_smart_money_filter


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
    print(f"== Foreign-net-buy filter validation: {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    codes = uni["Code"].tolist()
    data = get_ohlcv_batch(codes, START, END)
    print(f"  OHLCV: {len(data)} tickers")

    # Investor flows — assumes backfill has populated the cache.
    flows = get_investor_flow_batch(codes, START, END, sleep=0.0)
    print(f"  Flows: {len(flows)} tickers with foreign-net-buy data")
    missing = [c for c in codes if c not in flows]
    if missing:
        print(f"  WARN: {len(missing)} tickers missing flow data (will be blocked by filter): {missing[:5]}{' ...' if len(missing)>5 else ''}")

    regime3 = kospi_regime_3tier(START, END)
    counts = regime3.value_counts().reindex([STRONG_BULL, WEAK, BEAR]).fillna(0).astype(int)
    print(f"  Regime: STRONG_BULL={counts[STRONG_BULL]} / WEAK={counts[WEAK]} / BEAR={counts[BEAR]}")

    # Engine config: dispatcher gates internally, so 'always on' regime gate.
    always_on = pd.Series(True, index=regime3.index)
    cfg = EngineConfig(
        initial_cash=10_000_000.0,
        max_positions=5,
        cooldown_days=2,
        regime=always_on,
    )

    # Build filters
    f_foreign = make_foreign_net_buy_filter(flows, lookback=5, min_krw=0.0)
    f_smart = make_smart_money_filter(flows, lookback=5)

    runs = {
        "A_Baseline":          make_dispatcher(regime3, enable_bear=True),
        "B_Foreign_WEAK":      make_dispatcher(regime3, enable_bear=True,
                                               filter_per_regime={WEAK: f_foreign}),
        "C_Foreign_All":       make_dispatcher(regime3, enable_bear=True,
                                               filter_per_regime={
                                                   STRONG_BULL: f_foreign,
                                                   WEAK: f_foreign,
                                                   BEAR: f_foreign,
                                               }),
        "D_SmartMoney_WEAK":   make_dispatcher(regime3, enable_bear=True,
                                               filter_per_regime={WEAK: f_smart}),
    }

    all_rows = []
    equities = {}
    for name, fn in runs.items():
        print(f"\n--- Running {name} ---")
        res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
        if res.equity.empty:
            continue
        equities[name] = res.equity
        res.equity.to_csv(RESULTS_DIR / f"foreignfilter_equity_{name}.csv")
        if not res.trades.empty:
            res.trades.to_csv(RESULTS_DIR / f"foreignfilter_trades_{name}.csv", index=False)
        all_rows.extend(split_metrics(name, res.equity, res.trades))

    # KOSPI benchmark
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
    df.to_csv(RESULTS_DIR / "foreign_filter_summary.csv", index=False)

    print("\n========== Foreign-filter validation summary ==========")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print(df.to_string(index=False))

    print("\n========== Same-window comparison ==========")
    periods = ["FULL  2010-2024", "IS    2010-2018", "OOS_A 2019-2023", "OOS_B 2024     "]
    names = ["A_Baseline", "B_Foreign_WEAK", "C_Foreign_All", "D_SmartMoney_WEAK", "KOSPI_BH"]
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
            print(f"  {name:22s}  CAGR {cagr*100:7.2f}%  Sharpe {sh:5.2f}  MDD {mdd*100:6.2f}%  N_trades {int(ntr) if pd.notna(ntr) else 0:5d}")


if __name__ == "__main__":
    main()
