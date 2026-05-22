#!/usr/bin/env python
"""
EDITH daily signal generator.

Run after KR market close (after 15:30 KST). It will:
  1. Refresh OHLCV cache for the universe
  2. Check KOSPI regime (bullish?)
  3. Apply Momentum5_tuned on each name; collect today's entry signals
  4. Rank by 5-day return (score)
  5. Print a table: code, name, today's close, suggested entry stop/target,
     position size in KRW assuming a configurable EDITH bankroll

Usage:
    ./venv/bin/python scripts/daily_signal.py --capital 10000000
    ./venv/bin/python scripts/daily_signal.py --capital 10000000 --top 10
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from edith.data_loader import get_top_universe, get_ohlcv_batch
from edith.regime import kospi_regime
from edith.final_strategy import final_strategy, FINAL_PARAMS_MOMENTUM


N_KOSPI = 100
N_KOSDAQ = 50
MAX_POSITIONS = 5
LOOKBACK_DAYS = 400  # enough for SMA200 + buffer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10_000_000.0,
                    help="EDITH bankroll in KRW (default 10M)")
    ap.add_argument("--top", type=int, default=MAX_POSITIONS,
                    help="Number of top signals to surface (default 5)")
    ap.add_argument("--force", action="store_true",
                    help="Force OHLCV re-download")
    args = ap.parse_args()

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    print(f"== EDITH Daily Signals ({end}) ==")
    print(f"Capital: {args.capital:,.0f} KRW | Max positions: {args.top}\n")

    # 1. Regime check
    print("Checking KOSPI regime...")
    regime = kospi_regime(start, end)
    if regime.empty:
        print("  ! Failed to fetch KOSPI index. Aborting.")
        return
    today_regime = bool(regime.iloc[-1])
    last_5 = regime.iloc[-5:].astype(int).tolist()
    print(f"  Last 5 days regime: {last_5}  (1=ON, 0=OFF)")
    print(f"  TODAY: {'BULLISH (entries allowed)' if today_regime else 'BEARISH (no new entries)'}\n")

    if not today_regime:
        print("Regime is OFF -> EDITH does not generate new long signals today.")
        return

    # 2. Universe + data
    print("Loading universe + refreshing OHLCV...")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ, force=args.force)
    name_map = dict(zip(uni["Code"], uni["Name"]))
    board_map = dict(zip(uni["Code"], uni["Board"]))
    data = get_ohlcv_batch(uni["Code"].tolist(), start, end, force=args.force)

    # 3. Compute signals on the latest available bar
    candidates = []
    for code, df in data.items():
        if len(df) < 30:
            continue
        sig = final_strategy(code, df)
        last_idx = sig.index[-1]
        row = sig.loc[last_idx]
        if not bool(row.get("entry", False)):
            continue
        close = float(df["Close"].iloc[-1])
        score = float(row["score"])
        candidates.append({
            "code": code,
            "name": name_map.get(code, ""),
            "board": board_map.get(code, ""),
            "close": close,
            "ret5d": score,
            "stop_pct": float(row["stop_pct"]),
            "target_pct": float(row["target_pct"]),
            "max_hold": int(row["max_hold"]),
            "signal_date": last_idx.strftime("%Y-%m-%d"),
        })

    if not candidates:
        print("\nNo entries triggered today.")
        return

    df = pd.DataFrame(candidates).sort_values("ret5d", ascending=False).head(args.top)

    # 4. Position sizing
    per_slot = args.capital / args.top
    df["alloc_krw"] = per_slot
    df["shares"] = (df["alloc_krw"] / df["close"]).astype(int)
    df["entry_ref"] = df["close"]  # next-day open is what actually fills
    df["stop_price"] = (df["close"] * (1 - df["stop_pct"])).round().astype(int)
    df["target_price"] = (df["close"] * (1 + df["target_pct"])).round().astype(int)

    print(f"\n{len(df)} entry candidate(s) for next trading day:\n")
    display_cols = [
        "code", "name", "board", "close", "ret5d",
        "shares", "alloc_krw", "stop_price", "target_price", "max_hold",
    ]
    out = df[display_cols].copy()
    out["ret5d"] = out["ret5d"].map(lambda x: f"{x*100:6.2f}%")
    out["close"] = out["close"].map(lambda x: f"{x:,.0f}")
    out["alloc_krw"] = out["alloc_krw"].map(lambda x: f"{x:,.0f}")
    out["stop_price"] = out["stop_price"].map(lambda x: f"{x:,}")
    out["target_price"] = out["target_price"].map(lambda x: f"{x:,}")

    pd.set_option("display.width", 180)
    pd.set_option("display.max_columns", 20)
    print(out.to_string(index=False))

    print("\nStrategy parameters:")
    for k, v in FINAL_PARAMS_MOMENTUM.items():
        print(f"  {k}: {v}")
    print(
        "\nOrder placement: enter at NEXT trading day's open. "
        f"Stop -3% / Target +15% / Time-exit after {FINAL_PARAMS_MOMENTUM['max_hold']} bars.\n"
        "Costs reflected in backtest: 0.015% buy + 0.195% sell + 0.10% slippage/side."
    )

    out_path = ROOT / "results" / f"signals_{end}.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
