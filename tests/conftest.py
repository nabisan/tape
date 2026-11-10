from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.catalog.source import SCHEMA

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

SESSION_DAYS = ("2024-11-25", "2024-11-26", "2024-11-27", "2024-11-29", "2024-12-02")
HALF_DAYS = frozenset({"2024-11-29"})


def minutes(days: Sequence[str], seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    stamps = [
        np.datetime64(f"{d}T04:00", "ms") + np.arange(16 * 60) * np.timedelta64(60_000, "ms") for d in days
    ]
    time = np.concatenate(stamps)
    time = time[rng.random(len(time)) > 0.15]
    close = 100 * np.exp(np.cumsum(rng.normal(0, 5e-4, len(time))))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 2e-4, len(time))) * close
    return pa.Table.from_arrays(
        [
            pa.array(time),
            pa.array(open_),
            pa.array(np.maximum(open_, close) + spread),
            pa.array(np.minimum(open_, close) - spread),
            pa.array(close),
            pa.array(rng.integers(1, 5_000, len(time)).astype(np.uint64)),
        ],
        schema=SCHEMA,
    )


type WriteSource = Callable[..., Path]


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    d = tmp_path / "source"
    d.mkdir()
    return d


@pytest.fixture
def write_source(source_dir: Path) -> WriteSource:
    def write(
        ticker: str, days: Sequence[str] = SESSION_DAYS, seed: int = 0, table: pa.Table | None = None
    ) -> Path:
        path = source_dir / f"{ticker}.parquet"
        pq.write_table(table if table is not None else minutes(days, seed), path)
        return path

    return write


@pytest.fixture
def span() -> tuple[date, date]:
    return date(2024, 11, 25), date(2024, 12, 2)
