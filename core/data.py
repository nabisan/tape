"""Data layer.

Raw files are 1-second OHLCV bars with the timestamp stored as text. Reading and
parsing them every run is slow, so the first request for a ticker builds a
1-minute cache (cache/1m/<TICKER>.parquet, ~5 s per ticker). Every other
timeframe is resampled from that cache.
"""
from __future__ import annotations

import os
import threading
from datetime import date
from functools import lru_cache

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .config import ACTIONS_FILE, ADJUST_CORPORATE_ACTIONS, CACHE_DIR, DATA_DIR, RTH_END, RTH_START, TIMEFRAMES


def list_tickers() -> list[str]:
    """Tickers available from the raw folder, the cache folder, or both."""
    names = {p.stem for p in DATA_DIR.glob("*.parquet")}
    names |= {p.stem for p in CACHE_DIR.glob("*.parquet")}
    return sorted(names)


def _cache_path(ticker: str):
    return CACHE_DIR / f"{ticker}.parquet"


def data_range(ticker: str) -> tuple[date, date]:
    """First and last trading date available for a ticker.

    Read from the parquet footer statistics only (no data is loaded), so it is
    instant even for the large raw files.
    """
    path = _cache_path(ticker)
    if not path.exists():
        path = DATA_DIR / f"{ticker}.parquet"
    f = pq.ParquetFile(path)
    col = f.schema_arrow.get_field_index("time")
    lo = hi = None
    for i in range(f.metadata.num_row_groups):
        s = f.metadata.row_group(i).column(col).statistics
        if s is None or not s.has_min_max:
            continue
        lo = s.min if lo is None or s.min < lo else lo
        hi = s.max if hi is None or s.max > hi else hi
    if lo is None:  # no statistics written: fall back to reading the column
        t = pd.to_datetime(f.read(columns=["time"]).column("time").to_pandas())
        lo, hi = t.min(), t.max()
    return pd.Timestamp(lo).date(), pd.Timestamp(hi).date()


def build_minute_cache(ticker: str, force: bool = False) -> None:
    """Aggregate raw 1-second bars into 1-minute bars and save them."""
    out = _cache_path(ticker)
    src = DATA_DIR / f"{ticker}.parquet"
    if not src.exists():
        if out.exists():
            return  # cache-only deployment: nothing to rebuild from
        raise FileNotFoundError(f"No data for {ticker}: neither {src} nor {out} exists")
    if out.exists() and not force and out.stat().st_mtime >= src.stat().st_mtime:
        return
    t = pq.read_table(src, columns=["time", "open", "high", "low", "close", "volume"])
    # A few raw 1-second rows carry volume but no prices (vendor gaps); they would
    # turn the minute's open/close into NaN, so drop them.
    priced = pc.and_(pc.and_(pc.is_finite(t["open"]), pc.is_finite(t["high"])),
                     pc.and_(pc.is_finite(t["low"]), pc.is_finite(t["close"])))
    t = t.filter(priced)
    ts = t["time"].cast(pa.timestamp("s"))
    t = t.set_column(0, "time", pc.floor_temporal(ts, unit="minute"))
    g = t.group_by("time", use_threads=False).aggregate(
        [("open", "first"), ("high", "max"), ("low", "min"), ("close", "last"), ("volume", "sum")]
    )
    g = g.rename_columns(["time", "open", "high", "low", "close", "volume"])
    g = g.sort_by("time")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename, so a teammate loading the same ticker at
    # the same moment never reads a half-written cache file.
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    pq.write_table(g, tmp, compression="zstd")
    os.replace(tmp, out)


@lru_cache(maxsize=1)
def _all_actions() -> pd.DataFrame:
    if not ACTIONS_FILE.exists():
        return pd.DataFrame(columns=["ticker", "ex_date", "type", "factor", "description"])
    a = pd.read_csv(ACTIONS_FILE, parse_dates=["ex_date"])
    return a.sort_values(["ticker", "ex_date"]).reset_index(drop=True)


def corporate_actions(ticker: str) -> pd.DataFrame:
    """Price adjustments (spin-offs) applied to this ticker, oldest first."""
    a = _all_actions()
    return a[a["ticker"] == ticker]


def apply_corporate_actions(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Back-adjust prices for spin-offs, like splits are adjusted in the source data.

    Every price before an ex-date is multiplied by that event's factor
    (1 - value distributed per share / previous close). Several events compound.
    Volume is unchanged: a spin-off does not change the parent's share count.
    """
    acts = corporate_actions(ticker)
    if acts.empty or df.empty:
        return df
    df = df.copy()
    px = ["open", "high", "low", "close"]
    for ex, factor in zip(acts["ex_date"], acts["factor"]):
        before = df.index < ex
        if before.any():
            df.loc[before, px] *= factor
    return df


def load_minute(ticker: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    build_minute_cache(ticker)
    filters = []
    if start is not None:
        filters.append(("time", ">=", pd.Timestamp(start)))
    if end is not None:
        filters.append(("time", "<", pd.Timestamp(end) + pd.Timedelta(days=1)))
    df = pq.read_table(_cache_path(ticker), filters=filters or None).to_pandas()
    df = df.set_index("time")
    if ADJUST_CORPORATE_ACTIONS:
        df = apply_corporate_actions(df, ticker)
    return df


def load_bars(
    ticker: str,
    timeframe: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """OHLCV bars indexed by bar-open time, regular trading hours only.

    Only RTH (09:30-16:00 ET) is used; pre-market and after-hours bars are dropped.
    Daily bars are one bar per trading date built from the RTH session.
    """
    df = load_minute(ticker, start, end)
    if df.empty:
        return df
    df = df.between_time(RTH_START, RTH_END, inclusive="left")

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if timeframe == "1m":
        out = df
    elif timeframe == "1D":
        out = df.groupby(df.index.normalize()).agg(agg)
    else:
        rule = pd.Timedelta(TIMEFRAMES[timeframe])
        # Anchor bins at the RTH open so 1h bars are 09:30-10:30 (not 09:00-10:00)
        # and 4h bars are 09:30-13:30 and 13:30-16:00.
        open_ = pd.Timedelta(f"{RTH_START}:00")
        out = df.resample(rule, origin="start_day", offset=open_ % rule, label="left", closed="left").agg(agg)
    out = out.dropna(subset=["open"])
    out.index.name = "time"
    return out
