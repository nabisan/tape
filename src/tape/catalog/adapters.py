from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from tape import calendar
from tape.catalog.source import SCHEMA, SourceError
from tape.core.types import Ticker
from tape.venues import currency_pair

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path


def _required(name: str, kind: pa.DataType) -> pa.Field[Any]:
    return pa.field(name, kind, nullable=False)


FX_VENDOR: Final = pa.schema(
    [
        _required("time", pa.timestamp("ms", tz="UTC")),
        _required("open", pa.float64()),
        _required("high", pa.float64()),
        _required("low", pa.float64()),
        _required("close", pa.float64()),
        _required("volume", pa.float64()),
        _required("trades", pa.uint64()),
    ]
)
FX_FILE: Final = re.compile(r"(?P<base>[A-Z]{3})_(?P<quote>[A-Z]{3})_forex_1m_.+\.parquet")


def fx_symbol(path: Path) -> Ticker:
    found = FX_FILE.fullmatch(path.name)
    if found is None:
        raise SourceError(f"{path.name} is not named like EUR_USD_forex_1m_<span>.parquet")
    return currency_pair(Ticker(found["base"] + found["quote"])).symbol


def fx_minutes(vendor: pa.Table, start: date | None = None) -> pa.Table:
    if not vendor.schema.equals(FX_VENDOR):
        raise SourceError(
            f"FX vendor schema mismatch: {vendor.schema.to_string(show_schema_metadata=False)!r}"
        )
    table = pa.Table.from_arrays(
        [
            vendor["time"].cast(pa.timestamp("ms")),
            vendor["open"],
            vendor["high"],
            vendor["low"],
            vendor["close"],
            vendor["trades"],
        ],
        schema=SCHEMA,
    )
    if start is None:
        return table
    first = calendar.fx_sessions(start, start)
    if not len(first):
        raise SourceError(f"{start} is not an FX session")
    opens = int(first.open_ns[0]) // 1_000_000
    return table.filter(pc.greater_equal(table["time"], pa.scalar(opens, pa.timestamp("ms"))))


def ingest_fx(files: Sequence[Path], out_dir: Path, start: date | None = None) -> dict[Ticker, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[Ticker, Path] = {}
    for path in sorted(files):
        symbol = fx_symbol(path)
        if symbol in written:
            raise SourceError(f"two files for {symbol}: {written[symbol]} and {path}")
        target = out_dir / f"{symbol}.parquet"
        pq.write_table(fx_minutes(pq.read_table(path), start), target, compression="zstd")
        written[symbol] = target
    return written
