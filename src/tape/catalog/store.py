from __future__ import annotations

import os
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from tape.calendar import Sessions
from tape.catalog.bars import Bars
from tape.catalog.source import Drops, columns
from tape.core.hashing import Digest
from tape.core.types import Ticker, Timeframe

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION: Final = 1
LAYOUT: Final = f"catalog-v{SCHEMA_VERSION}"
MANIFEST: Final = "manifest.json"
SESSIONS: Final = "sessions.parquet"

BAR_SCHEMA: Final = columns(
    time=pa.timestamp("ns"),
    session=pa.int32(),
    open=pa.float64(),
    high=pa.float64(),
    low=pa.float64(),
    close=pa.float64(),
    volume=pa.uint64(),
    count=pa.uint32(),
)
SESSION_SCHEMA: Final = columns(
    day=pa.date32(),
    open=pa.timestamp("ns"),
    close=pa.timestamp("ns"),
)


class Status(StrEnum):
    OK = "ok"
    REJECTED = "rejected"


class TickerReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: Ticker
    status: Status
    reason: str | None = None
    source_digest: Digest
    source_bytes: int
    source_rows: int = 0
    regular_rows: int = 0
    non_session_days: int = 0
    first_session: date | None = None
    last_session: date | None = None
    sessions_expected: int = 0
    sessions_missing: tuple[date, ...] = ()
    drops: Drops = Drops()
    bars: dict[Timeframe, int] = {}


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    data_version: Digest
    built_at: datetime
    calendar: str
    calendar_library: str
    reference: Ticker
    first_session: date
    last_session: date
    sessions: int
    tickers: dict[Ticker, TickerReport]

    @property
    def available(self) -> list[Ticker]:
        return [t for t, r in self.tickers.items() if r.status is Status.OK]


def bar_path(root: Path, ticker: Ticker, timeframe: Timeframe) -> Path:
    return root / "bars" / f"timeframe={timeframe.value}" / f"ticker={ticker}" / "part-0.parquet"


def _write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def write_bars(bars: Bars, path: Path) -> None:
    table = pa.Table.from_arrays(
        [
            pa.array(bars.time_ns.view("datetime64[ns]")),
            pa.array(bars.session),
            pa.array(bars.open),
            pa.array(bars.high),
            pa.array(bars.low),
            pa.array(bars.close),
            pa.array(bars.volume),
            pa.array(bars.count),
        ],
        schema=BAR_SCHEMA,
    )
    _write(table, path)


def read_bars(path: Path) -> Bars:
    t = pq.read_table(path, memory_map=True)
    return Bars(
        time_ns=t["time"].cast(pa.int64()).to_numpy(),
        session=t["session"].to_numpy(),
        open=t["open"].to_numpy(),
        high=t["high"].to_numpy(),
        low=t["low"].to_numpy(),
        close=t["close"].to_numpy(),
        volume=t["volume"].to_numpy(),
        count=t["count"].to_numpy(),
    )


def write_sessions(sessions: Sessions, path: Path) -> None:
    table = pa.Table.from_arrays(
        [
            pa.array(sessions.day),
            pa.array(sessions.open_ns.view("datetime64[ns]")),
            pa.array(sessions.close_ns.view("datetime64[ns]")),
        ],
        schema=SESSION_SCHEMA,
    )
    _write(table, path)


def read_sessions(path: Path) -> Sessions:
    t = pq.read_table(path)
    return Sessions(
        day=t["day"].to_numpy().astype("datetime64[D]"),
        open_ns=t["open"].cast(pa.int64()).to_numpy(),
        close_ns=t["close"].cast(pa.int64()).to_numpy(),
    )


class Store:
    __slots__ = ("manifest", "root")

    def __init__(self, root: Path, manifest: Manifest) -> None:
        if manifest.schema_version != SCHEMA_VERSION:
            raise ValueError(f"store schema v{manifest.schema_version}, reader v{SCHEMA_VERSION}")
        self.root = root
        self.manifest = manifest

    @classmethod
    def open(cls, store_dir: Path, data_version: Digest | None = None) -> Store:
        base = store_dir / LAYOUT
        if data_version is not None:
            root = base / data_version
            return cls(root, Manifest.model_validate_json((root / MANIFEST).read_bytes()))
        found = [p for p in base.iterdir() if (p / MANIFEST).is_file()] if base.is_dir() else []
        if not found:
            raise FileNotFoundError(f"no catalog under {base}")
        stores = [cls(p, Manifest.model_validate_json((p / MANIFEST).read_bytes())) for p in found]
        return max(stores, key=lambda s: s.manifest.built_at)

    def sessions(self) -> Sessions:
        return read_sessions(self.root / SESSIONS)

    def bars(self, ticker: Ticker, timeframe: Timeframe) -> Bars:
        report = self.manifest.tickers.get(ticker)
        if report is None or report.status is not Status.OK:
            raise KeyError(f"{ticker} not in catalog {self.manifest.data_version}")
        return read_bars(bar_path(self.root, ticker, timeframe))
