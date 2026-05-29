"""
Investor-type flow data loader (foreign / institutional / retail net buying).

Source: KRX via pykrx (requires KRX_ID / KRX_PW env vars).
Endpoint: 투자자별 거래실적 개별종목 일별추이 (12008), 순매수 거래대금 기준 (KRW).

Cache: separate pickle dir from OHLCV cache (different schema), 24h TTL.
"""

from __future__ import annotations

import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
from pykrx import stock
from tqdm import tqdm

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache_investor"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.pkl"


def _cache_fresh(name: str, max_age_hours: int = 24) -> bool:
    p = _cache_path(name)
    if not p.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
    return age < timedelta(hours=max_age_hours)


def _save(name: str, obj) -> None:
    with open(_cache_path(name), "wb") as f:
        pickle.dump(obj, f)


def _load(name: str):
    with open(_cache_path(name), "rb") as f:
        return pickle.load(f)


def _to_krx_date(s: str) -> str:
    """Accept 'YYYY-MM-DD' or 'YYYYMMDD'. Return 'YYYYMMDD' for pykrx."""
    return s.replace("-", "")


COLUMN_MAP = {
    "기관합계": "inst_net",
    "외국인합계": "foreign_net",
    "개인": "retail_net",
    "기타법인": "other_corp_net",
}


def get_investor_flow(
    code: str,
    start: str,
    end: str,
    force: bool = False,
) -> pd.DataFrame:
    """Daily net trading value (KRW) per investor type for one ticker.

    Returns a DataFrame indexed by date with columns:
        foreign_net, inst_net, retail_net, other_corp_net
    Positive = net buying, negative = net selling.

    On any error returns an empty DataFrame (e.g. ETF/SPAC, delisted).
    """
    name = f"flow_{code}_{_to_krx_date(start)}_{_to_krx_date(end)}"
    p = _cache_path(name)
    end_historical = pd.Timestamp(end) < pd.Timestamp.now().normalize()

    if not force and p.exists():
        cached = _load(name)
        # Immutable historical window with real data -> always trust the cache,
        # ignoring the 24h TTL (the data for a past window never changes; a
        # needless refetch only risks clobbering it on a transient failure).
        if end_historical and cached is not None and not cached.empty:
            return cached
        # Live window (end >= today) -> honor the 24h freshness TTL.
        if not end_historical and _cache_fresh(name):
            return cached

    try:
        raw = stock.get_market_trading_value_by_date(
            _to_krx_date(start), _to_krx_date(end), code
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ! {code} flow fetch fail: {e}")
        raw = pd.DataFrame()

    if raw is None or raw.empty:
        out = pd.DataFrame(
            columns=["foreign_net", "inst_net", "retail_net", "other_corp_net"]
        )
    else:
        out = raw.rename(columns=COLUMN_MAP)
        keep = ["foreign_net", "inst_net", "retail_net", "other_corp_net"]
        out = out[[c for c in keep if c in out.columns]].astype("int64")
        out.index.name = "Date"

    # Safety: never overwrite a populated cache with an empty fetch result
    # (e.g. KRX auth failure). Losing real historical data to a transient
    # error is far worse than serving slightly stale data.
    if out.empty and p.exists():
        prev = _load(name)
        if prev is not None and not prev.empty:
            return prev

    _save(name, out)
    return out


def get_investor_flow_batch(
    codes: Iterable[str],
    start: str,
    end: str,
    force: bool = False,
    sleep: float = 0.2,
) -> dict[str, pd.DataFrame]:
    """Fetch many tickers. Returns dict[code] -> DataFrame.

    Default sleep=0.2s between calls to be polite to KRX.
    """
    out: dict[str, pd.DataFrame] = {}
    codes = list(codes)
    for code in tqdm(codes, desc="InvestorFlow"):
        df = get_investor_flow(code, start, end, force=force)
        if not df.empty:
            out[code] = df
        if sleep:
            time.sleep(sleep)
    return out
