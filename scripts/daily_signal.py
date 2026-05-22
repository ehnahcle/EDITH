#!/usr/bin/env python
"""
EDITH daily signal generator — Triple-mode dispatcher.

Run after KR market close (15:30 KST). Steps:
  1. Refresh OHLCV cache for the universe.
  2. Classify today's KOSPI regime → STRONG_BULL / WEAK / BEAR.
  3. Apply the matching sub-strategy:
       STRONG_BULL → Momentum5 (5-day +10%, stop -3%, target +15%, hold 7d)
       WEAK        → NewHigh52w (52w high break, stop -7%, target +20%, hold 10d)
       BEAR        → Disparity (-10% from SMA20, stop -3%, target +10%, hold 3d)
  4. Rank candidates by the regime's score metric, take top N.
  5. Print + save to results/signals_YYYY-MM-DD.csv.

Usage:
    ./venv/bin/python scripts/daily_signal.py --capital 10000000
    ./venv/bin/python scripts/daily_signal.py --capital 10000000 --no-bear
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
from edith.regime import kospi_regime_3tier, STRONG_BULL, WEAK, BEAR
from edith.final_strategy import make_dispatcher, params_for_regime


N_KOSPI = 100
N_KOSDAQ = 50
MAX_POSITIONS = 5
LOOKBACK_DAYS = 400


REGIME_DESC = {
    STRONG_BULL: ("🟢 STRONG_BULL — 강한 추세장", "Momentum5"),
    WEAK:        ("🟡 WEAK — 박스권/약한 추세장", "NewHigh52w"),
    BEAR:        ("🔴 BEAR — 약세장 (제한 가동)", "Disparity"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10_000_000.0,
                    help="EDITH bankroll in KRW (default 10M)")
    ap.add_argument("--top", type=int, default=MAX_POSITIONS,
                    help="Number of top signals to surface (default 5)")
    ap.add_argument("--no-bear", action="store_true",
                    help="Disable BEAR regime trading (dormant in bear). Default: enabled.")
    ap.add_argument("--force", action="store_true",
                    help="Force OHLCV re-download")
    args = ap.parse_args()

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    print(f"== EDITH Daily Signals — Triple Mode ({end}) ==")
    print(f"Capital: {args.capital:,.0f} KRW | Max positions: {args.top} | "
          f"BEAR trading: {'OFF' if args.no_bear else 'ON'}\n")

    # 1. 3-tier regime check
    print("Classifying KOSPI regime (3-tier)...")
    regime3 = kospi_regime_3tier(start, end)
    if regime3.empty:
        print("  ! Failed to fetch KOSPI index. Aborting.")
        return
    today_reg = str(regime3.iloc[-1])
    last5 = regime3.iloc[-5:].tolist()
    print(f"  Last 5 days: {last5}")
    desc, strategy_name = REGIME_DESC.get(today_reg, ("UNKNOWN", "—"))
    print(f"  TODAY: {desc}  →  Strategy: {strategy_name}\n")

    if today_reg == BEAR and args.no_bear:
        print("BEAR regime with --no-bear → no new entries today.")
        # still write empty signals file so dashboard knows we're dormant
        empty_path = ROOT / "results" / f"signals_{end}.csv"
        pd.DataFrame(columns=["code","name","board","close","ret5d","stop_pct",
                              "target_pct","max_hold","signal_date","alloc_krw",
                              "shares","stop_price","target_price","regime"]).to_csv(empty_path, index=False)
        return

    # 2. Universe + data
    print("Loading universe + refreshing OHLCV...")
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ, force=args.force)
    name_map = dict(zip(uni["Code"], uni["Name"]))
    board_map = dict(zip(uni["Code"], uni["Board"]))
    data = get_ohlcv_batch(uni["Code"].tolist(), start, end, force=args.force)

    # 3. Run dispatcher on latest bar of each ticker
    dispatcher = make_dispatcher(regime3, enable_bear=not args.no_bear)
    candidates = []
    for code, df in data.items():
        if len(df) < 30:
            continue
        sig = dispatcher(code, df)
        last_idx = sig.index[-1]
        row = sig.loc[last_idx]
        if not bool(row.get("entry", False)):
            continue
        close = float(df["Close"].iloc[-1])
        candidates.append({
            "code": code,
            "name": name_map.get(code, ""),
            "board": board_map.get(code, ""),
            "close": close,
            "score": float(row["score"]),
            "stop_pct": float(row["stop_pct"]),
            "target_pct": float(row["target_pct"]),
            "max_hold": int(row["max_hold"]),
            "signal_date": last_idx.strftime("%Y-%m-%d"),
            "regime": today_reg,
        })

    if not candidates:
        print("\nNo entries triggered today (strategy + regime combo found 0 candidates).")
        empty_path = ROOT / "results" / f"signals_{end}.csv"
        pd.DataFrame(columns=["code","name","board","close","score","stop_pct",
                              "target_pct","max_hold","signal_date","regime"]).to_csv(empty_path, index=False)
        return

    df = pd.DataFrame(candidates).sort_values("score", ascending=False).head(args.top)

    # 4. Position sizing
    per_slot = args.capital / args.top
    df["alloc_krw"] = per_slot
    df["shares"] = (df["alloc_krw"] / df["close"]).astype(int)
    df["stop_price"] = (df["close"] * (1 - df["stop_pct"])).round().astype(int)
    df["target_price"] = (df["close"] * (1 + df["target_pct"])).round().astype(int)
    # Keep 'ret5d' alias for backward compat with dashboard
    df["ret5d"] = df["score"]

    print(f"\n{len(df)} entry candidate(s) for next trading day:\n")
    display_cols = [
        "code", "name", "board", "close", "score",
        "shares", "alloc_krw", "stop_price", "target_price", "max_hold",
    ]
    out = df[display_cols].copy()
    out["score"] = out["score"].map(lambda x: f"{x*100:6.2f}%" if abs(x) < 100 else f"{x:.2f}")
    out["close"] = out["close"].map(lambda x: f"{x:,.0f}")
    out["alloc_krw"] = out["alloc_krw"].map(lambda x: f"{x:,.0f}")
    out["stop_price"] = out["stop_price"].map(lambda x: f"{x:,}")
    out["target_price"] = out["target_price"].map(lambda x: f"{x:,}")
    pd.set_option("display.width", 180)
    pd.set_option("display.max_columns", 20)
    print(out.to_string(index=False))

    params = params_for_regime(today_reg)
    print(f"\nActive strategy parameters ({strategy_name}):")
    for k, v in params.items():
        print(f"  {k}: {v}")
    stop_pct = params.get("stop_pct", 0.05)
    target_pct = params.get("target_pct", 0.10)
    max_hold = params.get("max_hold", 5)
    print(
        f"\nOrder placement: enter at NEXT trading day's open. "
        f"Stop -{stop_pct*100:.0f}% / Target +{target_pct*100:.0f}% / "
        f"Time-exit after {max_hold} bars.\n"
        "Costs in backtest: 0.015% buy + 0.195% sell + 0.10% slippage/side."
    )

    out_path = ROOT / "results" / f"signals_{end}.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
