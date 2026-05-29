#!/usr/bin/env python
"""
Multicollinearity GATE pre-check for the institutional-flow booster.

Before building an institutional booster, verify it carries information the
existing foreign booster doesn't already capture. We compute, per ticker,
the same standardized 5-day flow signal the booster would use:

    z = clip( zscore_60d( rolling_5d_sum( flow ) ), -2, +2 )

for FOREIGN (raw KRW, the existing booster's exact recipe) and for
INSTITUTIONAL in two forms:

    inst_raw   = z of rolling-5d inst_net (KRW)             (same recipe as foreign)
    inst_mcap  = z of rolling-5d (inst_net / Close)         (mcap-normalized*)

(*) mcap_t is approximately proportional to price_t when shares outstanding
are roughly constant, and any ticker-constant factor cancels inside the
z-score. So inst_net/Close is a zero-extra-infra proxy for inst_net/mcap.
This deliberately differs from foreign's volume/level normalization so the
two signals are not constructed identically.

We then correlate foreign_z against each institutional_z, pooled across all
ticker-days in the KOSPI top-100, and also report the median per-ticker
correlation. GATE: if corr > 0.60, institutional adds little beyond foreign
-> abandon the booster. Otherwise proceed to the weight grid.
"""

from __future__ import annotations

import glob
import pickle
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from edith.data_loader import get_ohlcv_batch

START = "2010-01-01"
END = "2024-12-31"
GATE = 0.60
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
FLOW_CACHE = ROOT / "data" / "cache_investor"


def discover_flow_tickers() -> dict[str, pd.DataFrame]:
    """Load every populated full-history (2010-2024) investor-flow cache.

    We key off the cache rather than the current top-100 universe: the
    multicollinearity question is a structural property of KR large-cap flows,
    not of today's exact constituents, and the current universe's flow caches
    are unavailable. Any broad set of populated large-caps answers the gate.
    """
    out: dict[str, pd.DataFrame] = {}
    for f in glob.glob(str(FLOW_CACHE / "flow_*_20100101_20241231.pkl")):
        m = re.match(r"flow_(\d+)_", Path(f).name)
        if not m:
            continue
        try:
            df = pickle.load(open(f, "rb"))
        except Exception:  # noqa: BLE001
            continue
        if df is not None and len(df) > 0:
            out[m.group(1)] = df
    return out


def flow_z(series: pd.Series, lookback: int = 5, window: int = 60,
           clip: tuple[float, float] = (-2.0, 2.0)) -> pd.Series:
    """Booster's exact standardization: 5-day sum -> trailing-60d z -> clip."""
    cum = series.rolling(lookback, min_periods=lookback).sum()
    mu = cum.rolling(window, min_periods=window).mean()
    sd = cum.rolling(window, min_periods=window).std()
    return ((cum - mu) / sd).clip(*clip)


def main() -> None:
    print("== Institutional-booster multicollinearity gate ==")
    flows = discover_flow_tickers()
    codes = sorted(flows.keys())
    data = get_ohlcv_batch(codes, START, END)
    print(f"  Populated flow caches: {len(flows)} | OHLCV: {len(data)} tickers")

    fz_all, iz_raw_all, iz_mcap_all = [], [], []
    per_ticker = []

    for code in codes:
        flow = flows.get(code)
        ohlcv = data.get(code)
        if flow is None or flow.empty or ohlcv is None or ohlcv.empty:
            continue
        if "foreign_net" not in flow.columns or "inst_net" not in flow.columns:
            continue

        fz = flow_z(flow["foreign_net"])
        iz_raw = flow_z(flow["inst_net"])

        close = ohlcv["Close"].reindex(flow.index)
        inst_mcap_norm = flow["inst_net"] / close.replace(0, np.nan)
        iz_mcap = flow_z(inst_mcap_norm)

        df = pd.DataFrame({"fz": fz, "iz_raw": iz_raw, "iz_mcap": iz_mcap}).dropna()
        if len(df) < 60:
            continue

        fz_all.append(df["fz"])
        iz_raw_all.append(df["iz_raw"])
        iz_mcap_all.append(df["iz_mcap"])
        per_ticker.append({
            "code": code,
            "n": len(df),
            "corr_raw": df["fz"].corr(df["iz_raw"]),
            "corr_mcap": df["fz"].corr(df["iz_mcap"]),
        })

    if not per_ticker:
        print("  No usable tickers — cannot run gate.")
        return

    fz_all = pd.concat(fz_all, ignore_index=True)
    iz_raw_all = pd.concat(iz_raw_all, ignore_index=True)
    iz_mcap_all = pd.concat(iz_mcap_all, ignore_index=True)

    pooled_raw = float(np.corrcoef(fz_all, iz_raw_all)[0, 1])
    pooled_mcap = float(np.corrcoef(fz_all, iz_mcap_all)[0, 1])

    pt = pd.DataFrame(per_ticker)
    pt.to_csv(RESULTS_DIR / "inst_gate_per_ticker.csv", index=False)
    med_raw = float(pt["corr_raw"].median())
    med_mcap = float(pt["corr_mcap"].median())

    print(f"\n  Tickers used: {len(pt)}  |  pooled ticker-days: {len(fz_all):,}")
    print("\n  ---- foreign_z  vs  institutional_z  correlation ----")
    print(f"    inst RAW  (KRW, same recipe as foreign):  pooled {pooled_raw:+.3f}   median/ticker {med_raw:+.3f}")
    print(f"    inst MCAP (inst_net/Close, normalized):   pooled {pooled_mcap:+.3f}   median/ticker {med_mcap:+.3f}")
    print(f"\n  GATE threshold: corr > {GATE:.2f}  ->  abandon (redundant with foreign)")

    chosen = "inst_mcap"  # the user-specified definition
    chosen_pooled = pooled_mcap
    verdict = "FAIL (abandon)" if abs(chosen_pooled) > GATE else "PASS (proceed to weight grid)"
    print(f"\n  >>> Decision on chosen definition ({chosen}, pooled {chosen_pooled:+.3f}): {verdict}")
    if abs(pooled_raw) > GATE >= abs(pooled_mcap):
        print("      Note: mcap-normalization meaningfully lowered collinearity vs the raw-KRW recipe.")

    pd.DataFrame([{
        "pooled_corr_raw": pooled_raw,
        "pooled_corr_mcap": pooled_mcap,
        "median_corr_raw": med_raw,
        "median_corr_mcap": med_mcap,
        "gate": GATE,
        "verdict_mcap": verdict,
    }]).to_csv(RESULTS_DIR / "inst_gate_summary.csv", index=False)


if __name__ == "__main__":
    main()
