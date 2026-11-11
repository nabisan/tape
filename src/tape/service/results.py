from __future__ import annotations

import math
import os
import shutil
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import ValidationError

from tape.engine import tables
from tape.engine.reports import Metrics
from tape.runner import RunSpec

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    import pandas as pd
    from pydantic import BaseModel

    from tape.core.hashing import Digest
    from tape.runner import Result


class Table(StrEnum):
    EQUITY = "equity"
    TRADES = "trades"
    ORDERS = "orders"
    FILLS = "fills"
    BARS = "bars"


TIME_COLUMN: Final = {
    Table.EQUITY: "date",
    Table.TRADES: "entry_time",
    Table.ORDERS: "submitted",
    Table.FILLS: "time",
    Table.BARS: "time",
}
BARS_PER_ROW: Final = "X-Tape-Bars-Per-Row"
SCHEMAS: Final = {
    Table.EQUITY: tables.EQUITY,
    Table.TRADES: tables.TRADES,
    Table.ORDERS: tables.ORDERS,
    Table.FILLS: tables.FILLS,
}


def schema(table: Table, names: Sequence[str]) -> pa.Schema:
    return tables.bars_for(names) if table is Table.BARS else SCHEMAS[table]


def thin(bars: pd.DataFrame, limit: int) -> tuple[pd.DataFrame, int]:
    factor = math.ceil(len(bars) / limit) if limit > 0 else 1
    if factor <= 1:
        return bars, 1
    rule = dict.fromkeys(bars.columns, "last") | {
        "time": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    groups = np.arange(len(bars)) // factor
    return bars.groupby(groups).agg({c: rule[c] for c in bars.columns}).reset_index(drop=True), factor


class StaleResultError(Exception):
    pass


STALE: Final = "computed by an older engine version; run it again"


class ResultStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def exists(self, key: Digest) -> bool:
        return (self.root / key / "metrics.json").is_file()

    def save(self, result: Result) -> None:
        final = self.root / result.key
        if final.exists():
            return
        tmp = self.root / f".{result.key}.{os.getpid()}.tmp"
        tmp.mkdir()
        try:
            frames = {
                Table.EQUITY: result.equity,
                Table.TRADES: result.trades,
                Table.ORDERS: result.orders,
                Table.FILLS: result.fills,
                Table.BARS: result.bars,
            }
            for table, frame in frames.items():
                arrow = tables.table(frame, schema(table, list(frame.columns)))
                pq.write_table(arrow, tmp / f"{table}.parquet", compression="zstd")
            (tmp / "spec.json").write_text(result.spec.model_dump_json(indent=2))
            (tmp / "metrics.json").write_text(result.metrics.model_dump_json(indent=2))
            tmp.rename(final)
        except OSError, tables.SchemaError:
            shutil.rmtree(tmp, ignore_errors=True)
            if not final.exists():
                raise

    def metrics(self, key: Digest) -> Metrics:
        return Metrics.model_validate_json((self.root / key / "metrics.json").read_bytes())

    def spec(self, key: Digest) -> RunSpec:
        try:
            return RunSpec.model_validate_json((self.root / key / "spec.json").read_bytes())
        except ValidationError as e:
            raise StaleResultError(f"run {key[:8]} was {STALE}") from e

    def table(self, key: Digest, table: Table) -> bytes:
        path = self.root / key / f"{table}.parquet"
        stored = pq.read_schema(path)
        if stored.names and stored.equals(schema(table, stored.names)):
            return path.read_bytes()
        sink = pa.BufferOutputStream()
        pq.write_table(self._load(key, table), sink)
        return bytes(sink.getvalue().to_pybytes())

    def _load(self, key: Digest, table: Table, where: pc.Expression | None = None) -> pa.Table:
        path = self.root / key / f"{table}.parquet"
        names = pq.read_schema(path).names
        found = pq.read_table(path, filters=where if names else None)
        try:
            return tables.conform(found, schema(table, names))
        except tables.SchemaError as e:
            raise StaleResultError(f"run {key[:8]} {table} was {STALE}") from e

    def sweep_summary(self, key: Digest) -> Path:
        return self.root / "sweeps" / f"{key}.parquet"

    def sweep_paths(self, key: Digest) -> Path:
        return self.root / "sweeps" / f"{key}.paths.parquet"

    def save_paths(self, key: Digest, frame: pd.DataFrame) -> None:
        path = self.sweep_paths(key)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        arrow = tables.table(frame, tables.paths(c for c in frame.columns if c != "date"))
        pq.write_table(arrow, tmp, compression="zstd")
        tmp.replace(path)

    def save_sweep(self, key: Digest, spec: BaseModel, summary: pa.Table) -> None:
        path = self.sweep_summary(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        pq.write_table(summary, tmp, compression="zstd")
        path.with_suffix(".json").write_text(spec.model_dump_json(indent=2))
        tmp.replace(path)

    def frame(
        self, key: Digest, table: Table, start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        field = pc.field(TIME_COLUMN[table])
        where: pc.Expression | None = None
        if start is not None:
            where = field >= pa.scalar(start, tables.TIME)
        if end is not None:
            below = field < pa.scalar(end, tables.TIME)
            where = below if where is None else where & below
        return self._load(key, table, where).to_pandas()
