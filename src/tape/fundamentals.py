from __future__ import annotations

import itertools
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from tape.engine import tables
from tape.engine.reports import Measure

if TYPE_CHECKING:
    from pathlib import Path

FILE: Final = "fundamentals.parquet"
SOURCE_DATE: Final = "%d-%m-%Y"
OUTLIER: Final = 5.0
NEIGHBOURS: Final = 3
STALE: Final = timedelta(days=7)
ROW_GROUP: Final = 4096

SOURCE: Final = {
    "ticker": "ticker",
    "date": "day",
    "shares": "shares",
    "market_cap": "market_cap",
    "ttm_net_income": "net_income",
    "pe_ratio": "pe",
    "ni_period_end": "earnings_period",
}
SCHEMA: Final = pa.schema(
    [
        ("ticker", tables.TEXT),
        ("day", pa.date32()),
        ("shares", tables.F64),
        ("market_cap", tables.F64),
        ("net_income", tables.F64),
        ("pe", tables.F64),
        ("earnings_period", pa.date32()),
        ("plausible", pa.bool_()),
    ]
)

type F64 = npt.NDArray[np.float64]
type Mask = npt.NDArray[np.bool_]


class FundamentalsError(ValueError):
    pass


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    market_cap: Measure
    pe: Measure
    net_income: Measure
    plausible: bool


class Valuation(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    start: Snapshot | None
    end: Snapshot | None


def plausible(shares: F64, factor: float = OUTLIER, width: int = NEIGHBOURS) -> Mask:
    out = np.ones(len(shares), dtype=np.bool_)
    ok = np.isfinite(shares) & (shares > 0)
    v = shares[ok]
    if not len(v):
        return out
    first = np.r_[True, v[1:] != v[:-1]]
    runs = v[first]
    run = np.cumsum(first) - 1
    good = np.ones(len(runs), dtype=np.bool_)
    typical = float(np.median(v))
    if len(runs) > 1:
        for i, value in enumerate(runs):
            ref = float(np.median(np.r_[runs[max(0, i - width) : i], runs[i + 1 : i + 1 + width]]))
            local = max(value / ref, ref / value) <= factor
            good[i] = local or max(value / typical, typical / value) <= factor
    out[ok] = good[run]
    return out


def _day(column: pa.ChunkedArray[Any]) -> Any:
    text = pc.cast(column, pa.string())
    return pc.cast(pc.strptime(text, format=SOURCE_DATE, unit="s", error_is_null=False), pa.date32())


def load(source: Path) -> pa.Table:
    found = pq.read_schema(source).names
    if missing := sorted(set(SOURCE) - set(found)):
        raise FundamentalsError(f"{source} lacks columns {', '.join(missing)}")
    raw = pq.read_table(source, columns=list(SOURCE))
    try:
        columns: dict[str, Any] = {
            "ticker": pc.cast(raw["ticker"], pa.string()),
            "day": _day(raw["date"]),
            "shares": pc.cast(raw["shares"], tables.F64),
            "market_cap": pc.cast(raw["market_cap"], tables.F64),
            "net_income": pc.cast(raw["ttm_net_income"], tables.F64),
            "pe": pc.cast(raw["pe_ratio"], tables.F64),
            "earnings_period": _day(raw["ni_period_end"]),
        }
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
        raise FundamentalsError(f"{source}: {e}") from e
    t = pa.table(columns).sort_by([("ticker", "ascending"), ("day", "ascending")])
    if t.num_rows and pc.any(pc.is_null(t["day"])).as_py():
        raise FundamentalsError(f"{source}: rows without a date")
    names = t["ticker"].to_numpy(zero_copy_only=False)
    shares = t["shares"].to_numpy(zero_copy_only=False).astype(np.float64)
    mask = np.zeros(t.num_rows, dtype=np.bool_)
    bounds = np.flatnonzero(np.r_[True, names[1:] != names[:-1], True]) if t.num_rows else np.array([0])
    for lo, hi in itertools.pairwise(bounds):
        mask[lo:hi] = plausible(shares[lo:hi])
    return tables.conform(t.append_column("plausible", pa.array(mask)), SCHEMA)


def save(table: pa.Table, root: Path) -> Path:
    path = root / FILE
    tmp = path.with_suffix(".tmp")
    root.mkdir(parents=True, exist_ok=True)
    pq.write_table(tables.conform(table, SCHEMA), tmp, compression="zstd", row_group_size=ROW_GROUP)
    tmp.replace(path)
    return path


def snapshot(t: pa.Table, day: date) -> Snapshot | None:
    days = t["day"].to_numpy(zero_copy_only=False).astype("datetime64[D]")
    i = int(np.searchsorted(days, np.datetime64(day, "D"), side="right")) - 1
    if i < 0:
        return None
    row = {k: v[0] for k, v in t.slice(i, 1).to_pydict().items()}
    found: date = row["day"]
    if day - found > STALE:
        return None
    ok = bool(row["plausible"])

    def value(name: str) -> float:
        v = row[name]
        return math.nan if v is None or not ok else float(v)

    return Snapshot(
        day=found,
        market_cap=value("market_cap"),
        pe=value("pe"),
        net_income=math.nan if row["net_income"] is None else float(row["net_income"]),
        plausible=ok,
    )


class Fundamentals:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def open(cls, root: Path) -> Fundamentals | None:
        path = root / FILE
        return cls(path) if path.is_file() else None

    def series(self, ticker: str) -> pa.Table:
        found = pq.read_table(self.path, filters=pc.field("ticker") == ticker)
        return tables.conform(found, SCHEMA)

    def valuation(self, ticker: str, start: date, end: date) -> Valuation:
        t = self.series(ticker)
        return Valuation(ticker=ticker, start=snapshot(t, start), end=snapshot(t, end))
