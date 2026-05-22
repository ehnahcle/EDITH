#!/usr/bin/env python
"""
Generate cache snapshots that the dashboard reads when running on Streamlit
Cloud (where direct KRX access may fail).

Outputs to results/:
  - regime_series.csv      : KOSPI bullish-regime bool series
  - kospi_buyhold.csv      : KOSPI buy-and-hold equity curve (normalised to 10M)

Run locally before `git push` so the cloud dashboard always has fresh data.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from edith.data_loader import get_index
from edith.regime import kospi_regime, kospi_regime_3tier

START = "2020-01-01"
END = datetime.today().strftime("%Y-%m-%d")
RESULTS = ROOT / "results"


def main() -> None:
    print(f"== Snapshot {START} -> {END} ==")

    # 1. Legacy 2-tier regime series (for backward compat with old dashboard)
    regime = kospi_regime(START, END)
    if regime.empty:
        print("  ! Could not fetch regime")
    else:
        pd.DataFrame({"regime": regime}).to_csv(RESULTS / "regime_series.csv")
        on = int(regime.sum())
        print(f"  ✓ regime_series.csv  ({on}/{len(regime)} ON)")

    # 2. NEW 3-tier regime series (used by Triple-mode dispatcher)
    regime3 = kospi_regime_3tier(START, END)
    if regime3.empty:
        print("  ! Could not fetch 3-tier regime")
    else:
        pd.DataFrame({"regime3": regime3}).to_csv(RESULTS / "regime3_series.csv")
        counts = regime3.value_counts()
        print(f"  ✓ regime3_series.csv  ({dict(counts)})")

    # 3. KOSPI buy-and-hold benchmark
    idx = get_index("KS11", START, END)
    if idx.empty:
        print("  ! Could not fetch KOSPI index")
    else:
        eq = idx["Close"] / idx["Close"].iloc[0] * 10_000_000.0
        eq.name = "equity"
        pd.DataFrame({"equity": eq}).to_csv(RESULTS / "kospi_buyhold.csv")
        print(f"  ✓ kospi_buyhold.csv  ({len(eq)} bars)")

    print("\nNext: commit & push these to GitHub so Streamlit Cloud sees them.")


if __name__ == "__main__":
    main()
