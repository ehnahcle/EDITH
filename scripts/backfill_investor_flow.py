"""
Backfill investor-flow cache for the full 150-ticker universe over 2010-2024.

Run once after KRX credentials are set. Subsequent calls hit the pickle cache.
Resumable: each ticker is cached independently, so a mid-run crash only loses
the in-flight one.

  ./venv/bin/python scripts/backfill_investor_flow.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make `edith.*` importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edith.data_loader import get_top_universe
from edith.investor_flow import get_investor_flow_batch

START = "2010-01-01"
END = "2024-12-31"
N_KOSPI = 100
N_KOSDAQ = 50


def main() -> None:
    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ)
    codes = uni["Code"].tolist()
    print(f"Universe: {len(codes)} tickers (KOSPI {N_KOSPI} + KOSDAQ {N_KOSDAQ})")
    print(f"Range:    {START} ~ {END}")
    print(f"ETA:      ~{len(codes) * 27 // 60} min (27s/ticker average)")
    print()

    t0 = time.time()
    flows = get_investor_flow_batch(codes, START, END, sleep=0.2)
    elapsed = time.time() - t0

    n_ok = len(flows)
    n_rows_total = sum(len(df) for df in flows.values())
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  fetched: {n_ok} / {len(codes)} tickers")
    print(f"  rows:    {n_rows_total:,}")
    if n_ok < len(codes):
        missing = [c for c in codes if c not in flows]
        print(f"  missing ({len(missing)}): {missing[:10]}{' ...' if len(missing)>10 else ''}")


if __name__ == "__main__":
    main()
