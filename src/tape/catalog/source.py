from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from tape.core.types import NS_PER_MINUTE

if TYPE_CHECKING:
    from tape.core.types import F64, I64, U64


def columns(**types: pa.DataType) -> pa.Schema:
    return pa.schema(list(types.items()))


SCHEMA: Final = columns(
    time=pa.timestamp("ms"),
    open=pa.float64(),
    high=pa.float64(),
    low=pa.float64(),
    close=pa.float64(),
    volume=pa.uint64(),
)

OHLC_TOLERANCE: Final = 1e-9


class SourceError(Exception):
    pass


class Drops(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    non_finite: int = 0
    non_positive: int = 0
    ohlc_inconsistent: int = 0

    @property
    def total(self) -> int:
        return self.non_finite + self.non_positive + self.ohlc_inconsistent


@dataclass(frozen=True, slots=True)
class Minutes:
    time_ns: I64
    open: F64
    high: F64
    low: F64
    close: F64
    volume: U64

    def __len__(self) -> int:
        return len(self.time_ns)


def read(data: bytes) -> tuple[Minutes, Drops]:
    table = pq.read_table(pa.BufferReader(data))
    if not table.schema.equals(SCHEMA):
        raise SourceError(f"schema mismatch: {table.schema.to_string(show_schema_metadata=False)!r}")
    if table["time"].null_count or table["volume"].null_count:
        raise SourceError("null time or volume")

    time_ns = table["time"].cast(pa.int64()).to_numpy() * 1_000_000
    step = np.diff(time_ns)
    if step.size and step.min() <= 0:
        raise SourceError(f"time not strictly increasing at row {int(np.argmax(step <= 0)) + 1}")
    if np.any(time_ns % NS_PER_MINUTE):
        raise SourceError("time not on minute boundaries")

    open_, high, low, close = (
        table[name].to_numpy().astype(np.float64, copy=False) for name in ("open", "high", "low", "close")
    )
    finite = np.isfinite(open_) & np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    positive = finite & (np.minimum(np.minimum(open_, high), np.minimum(low, close)) > 0)
    slack = 1 + OHLC_TOLERANCE
    consistent = (
        positive & (low <= np.minimum(open_, close) * slack) & (high * slack >= np.maximum(open_, close))
    )
    drops = Drops(
        non_finite=int((~finite).sum()),
        non_positive=int((finite & ~positive).sum()),
        ohlc_inconsistent=int((positive & ~consistent).sum()),
    )
    volume = table["volume"].to_numpy().astype(np.uint64, copy=False)
    keep = consistent
    return (
        Minutes(time_ns[keep], open_[keep], high[keep], low[keep], close[keep], volume[keep]),
        drops,
    )
