from __future__ import annotations

import io

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.catalog.source import SCHEMA, SourceError, read
from tests.conftest import SESSION_DAYS, minutes


def encode(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def test_clean_source_round_trips() -> None:
    table = minutes(SESSION_DAYS, seed=1)
    m, drops = read(encode(table))
    assert drops.total == 0
    assert len(m) == table.num_rows
    assert m.time_ns[0] == np.datetime64(table["time"][0].as_py(), "ns").astype(np.int64)
    assert m.volume.dtype == np.uint64


def test_schema_mismatch_rejected() -> None:
    table = minutes(SESSION_DAYS, seed=1).cast(SCHEMA.set(5, pa.field("volume", pa.int64())))
    with pytest.raises(SourceError, match="schema mismatch"):
        read(encode(table))


def test_unsorted_rejected() -> None:
    table = minutes(SESSION_DAYS, seed=1)
    order = np.arange(table.num_rows)
    order[[10, 11]] = order[[11, 10]]
    with pytest.raises(SourceError, match="not strictly increasing at row 11"):
        read(encode(table.take(order)))


def test_duplicate_time_rejected() -> None:
    table = minutes(SESSION_DAYS, seed=1)
    with pytest.raises(SourceError, match="not strictly increasing"):
        read(encode(pa.concat_tables([table.slice(0, 5), table.slice(4)])))


def test_off_minute_rejected() -> None:
    table = minutes(SESSION_DAYS, seed=1)
    shifted = pa.compute.add(table["time"], pa.scalar(1_000, pa.duration("ms")))
    with pytest.raises(SourceError, match="minute boundaries"):
        read(encode(table.set_column(0, "time", shifted)))


def test_bad_rows_dropped_and_counted() -> None:
    table = minutes(SESSION_DAYS, seed=1)
    high = table["high"].to_numpy().copy()
    low = table["low"].to_numpy().copy()
    close = table["close"].to_numpy().copy()
    close[3] = np.nan
    low[5] = -1.0
    high[7] = low[7] / 2
    patched = table.set_column(2, "high", pa.array(high)).set_column(3, "low", pa.array(low))
    patched = patched.set_column(4, "close", pa.array(close))
    m, drops = read(encode(patched))
    assert (drops.non_finite, drops.non_positive, drops.ohlc_inconsistent) == (1, 1, 1)
    assert len(m) == table.num_rows - 3
