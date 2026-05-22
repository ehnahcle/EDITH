#!/usr/bin/env python
"""
Per-regime strategy search (2010-2024).

For each of the 3 regimes (STRONG_BULL / WEAK / BEAR), find which
strategy + parameter combo produces the best risk-adjusted return.

The engine is run with a "filter regime" — a boolean series that masks
new entries to ONLY the target regime.  This lets us isolate each
strategy's contribution per market environment.

Outputs:
  results/per_regime_search.csv  — full grid
  + console summary of top 3 per regime
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
from edith.regime import kospi_regime_3tier, STRONG_BULL, WEAK, BEAR

from edith.strategies.momentum import momentum_5d
from edith.strategies.rsi_meanrev import rsi_oversold
from edith.strategies.disparity import disparity_meanrev
from edith.strategies.new_high_52w import new_high_52w
from edith.strategies.volume_breakout import volume_breakout
from edith.strategies.gap_reversal import gap_reversal


START = "2010-01-01"
END = "2024-12-31"
N_KOSPI = 100
N_KOSDAQ = 50
RESULTS_DIR = ROOT / "results"


def make_fn(base_fn, **params):
    def _fn(code, df):
        return base_fn(code, df, **params)
    _fn.__name__ = base_fn.__name__
    return _fn


def main() -> None:
    print(f"== Per-regime strategy search: {START} -> {END} ==")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    data = get_ohlcv_batch(uni["Code"].tolist(), START, END)
    print(f"  {len(data)} tickers")

    regime3 = kospi_regime_3tier(START, END)
    print("  Regime mix:")
    print(regime3.value_counts())

    # 3 boolean masks
    masks = {
        STRONG_BULL: (regime3 == STRONG_BULL),
        WEAK: (regime3 == WEAK),
        BEAR: (regime3 == BEAR),
    }

    # Candidate strategies + small grids
    candidates = []

    # Momentum (tuned + nearby variants)
    for ret_t, stop, tgt, hold in itertools.product(
        [0.05, 0.10],
        [0.03, 0.05],
        [0.10, 0.15],
        [3, 5, 7],
    ):
        candidates.append((
            f"Momentum5(r={ret_t},s={stop},t={tgt},h={hold})",
            make_fn(momentum_5d, ret_thresh=ret_t, stop_pct=stop, target_pct=tgt, max_hold=hold),
        ))

    # RSI(2) oversold variants
    for thresh, stop, tgt, hold in itertools.product(
        [5, 10, 15],
        [0.03, 0.05],
        [0.05, 0.07, 0.10],
        [3, 5, 7],
    ):
        candidates.append((
            f"RSI2(t={thresh},s={stop},tg={tgt},h={hold})",
            make_fn(rsi_oversold, rsi_n=2, rsi_thresh=thresh, stop_pct=stop, target_pct=tgt, max_hold=hold),
        ))

    # Disparity mean reversion
    for disp, stop, tgt, hold in itertools.product(
        [-0.05, -0.07, -0.10],
        [0.03, 0.05],
        [0.05, 0.07, 0.10],
        [3, 5, 7],
    ):
        candidates.append((
            f"Disparity(d={disp},s={stop},tg={tgt},h={hold})",
            make_fn(disparity_meanrev, thresh=disp, stop_pct=stop, target_pct=tgt, max_hold=hold),
        ))

    # 52-week high breakout
    for vol_m, stop, tgt, hold in itertools.product(
        [1.2, 1.5],
        [0.05, 0.07],
        [0.15, 0.20],
        [7, 10],
    ):
        candidates.append((
            f"NH52(v={vol_m},s={stop},tg={tgt},h={hold})",
            make_fn(new_high_52w, vol_mult=vol_m, stop_pct=stop, target_pct=tgt, max_hold=hold),
        ))

    print(f"\n  {len(candidates)} candidate configs × 3 regimes = {len(candidates)*3} runs")

    rows = []
    for regime_name, mask in masks.items():
        if mask.sum() < 100:
            continue
        cfg = EngineConfig(
            initial_cash=10_000_000.0,
            max_positions=5,
            cooldown_days=2,
            regime=mask,
        )
        print(f"\n--- Regime: {regime_name} ({int(mask.sum())} days) ---")
        for i, (label, fn) in enumerate(candidates, 1):
            res = run_backtest(data, fn, cfg=cfg, start=START, end=END)
            if res.equity.empty:
                continue
            m = summarize(res.equity, res.trades)
            rows.append({
                "regime": regime_name,
                "strategy": label,
                "CAGR": m["CAGR"],
                "Sharpe": m["Sharpe"],
                "MDD": m["MDD"],
                "PF": m.get("ProfitFactor", 0),
                "N_trades": m.get("N_trades", 0),
                "WinRate": m.get("WinRate", 0),
                "Final": m["Final"],
            })
            if i % 30 == 0 or i == len(candidates):
                print(f"  [{i}/{len(candidates)}]")

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "per_regime_search.csv", index=False)

    # Top 5 per regime (Sharpe with N_trades >= 30)
    print("\n========== TOP 5 per regime (N_trades >= 30) ==========")
    pd.set_option("display.float_format", "{:.3f}".format)
    pd.set_option("display.width", 180)
    for reg in [STRONG_BULL, WEAK, BEAR]:
        sub = df[(df["regime"] == reg) & (df["N_trades"] >= 30)].copy()
        if sub.empty:
            print(f"\n--- {reg}: no qualifying configs ---")
            continue
        sub = sub.sort_values("Sharpe", ascending=False).head(5)
        print(f"\n--- {reg} ---")
        print(sub.to_string(index=False))


if __name__ == "__main__":
    main()
