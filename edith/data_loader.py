"""
Korean market data loader with on-disk pickle cache.

- OHLCV via FinanceDataReader (free, no API key)
- Universe + market cap via FinanceDataReader.StockListing
"""

from __future__ import annotations

import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import FinanceDataReader as fdr
import pandas as pd
from tqdm import tqdm

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.pkl"


def _cache_fresh(name: str, max_age_hours: int = 12) -> bool:
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


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def get_kospi_listing(force: bool = False) -> pd.DataFrame:
    name = "listing_kospi"
    if not force and _cache_fresh(name, 24):
        return _load(name)
    df = fdr.StockListing("KOSPI")
    _save(name, df)
    return df


def get_kosdaq_listing(force: bool = False) -> pd.DataFrame:
    name = "listing_kosdaq"
    if not force and _cache_fresh(name, 24):
        return _load(name)
    df = fdr.StockListing("KOSDAQ")
    _save(name, df)
    return df


def get_top_universe(
    n_kospi: int = 200,
    n_kosdaq: int = 150,
    force: bool = False,
) -> pd.DataFrame:
    """Return top-N by market cap from each board, ETF/SPAC/preferred filtered out."""
    name = f"universe_top_{n_kospi}_{n_kosdaq}"
    if not force and _cache_fresh(name, 24):
        return _load(name)

    kospi = get_kospi_listing(force=force).copy()
    kosdaq = get_kosdaq_listing(force=force).copy()
    kospi["Board"] = "KOSPI"
    kosdaq["Board"] = "KOSDAQ"

    def _clean(df: pd.DataFrame, top: int) -> pd.DataFrame:
        df = df.dropna(subset=["Marcap"]).copy()
        df = df[~df["Name"].str.contains("스팩|SPAC|우$|2우B|3우B", regex=True, na=False)]
        df = df.sort_values("Marcap", ascending=False).head(top)
        return df[["Code", "Name", "Board", "Marcap"]]

    out = pd.concat([_clean(kospi, n_kospi), _clean(kosdaq, n_kosdaq)], ignore_index=True)
    _save(name, out)
    return out


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def get_ohlcv(code: str, start: str, end: str, force: bool = False) -> pd.DataFrame:
    """Single ticker daily bars. Columns: Open, High, Low, Close, Volume, Change."""
    name = f"ohlcv_{code}_{start}_{end}"
    if not force and _cache_fresh(name, 24):
        return _load(name)
    df = fdr.DataReader(code, start, end)
    if df is None or df.empty:
        df = pd.DataFrame()
    _save(name, df)
    return df


def get_ohlcv_batch(
    codes: Iterable[str],
    start: str,
    end: str,
    force: bool = False,
    sleep: float = 0.0,
) -> dict[str, pd.DataFrame]:
    """Fetch many tickers. Returns dict[code] -> DataFrame."""
    out: dict[str, pd.DataFrame] = {}
    codes = list(codes)
    for code in tqdm(codes, desc="OHLCV"):
        try:
            df = get_ohlcv(code, start, end, force=force)
            if not df.empty:
                out[code] = df
        except Exception as e:  # noqa: BLE001
            print(f"  ! {code} fetch fail: {e}")
        if sleep:
            time.sleep(sleep)
    return out


def get_index(code: str, start: str, end: str, force: bool = False) -> pd.DataFrame:
    """Index daily bars. KOSPI='KS11', KOSDAQ='KQ11'."""
    name = f"index_{code}_{start}_{end}"
    if not force and _cache_fresh(name, 24):
        return _load(name)
    df = fdr.DataReader(code, start, end)
    _save(name, df)
    return df
