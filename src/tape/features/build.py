from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq

from tape.catalog.store import Store
from tape.core.hashing import digest
from tape.features.catalog import Entry, FeatureCatalog, Origin
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.log import Log

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tape.core.hashing import Digest
    from tape.core.types import Ticker, Timeframe
    from tape.features.kinds import FeatureSpec

log = Log("Features")


@dataclass(frozen=True, slots=True)
class _Job:
    store_dir: Path
    data_version: Digest
    ticker: Ticker
    timeframe: Timeframe
    specs: tuple[FeatureSpec, ...]


def _build(job: _Job) -> list[Entry]:
    return materialize(Store.open(job.store_dir, job.data_version), job.ticker, job.timeframe, job.specs)


def materialize(
    store: Store, ticker: Ticker, timeframe: Timeframe, specs: Sequence[FeatureSpec]
) -> list[Entry]:
    bars = store.bars(ticker, timeframe)
    day = store.sessions().day
    first, last = day[int(bars.session[0])].item(), day[int(bars.session[-1])].item()
    path = (
        store.root
        / "features"
        / "built"
        / f"timeframe={timeframe.value}"
        / f"ticker={ticker}"
        / f"{digest([s.id for s in specs])[:16]}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({spec.id: pa.array(spec.compute(bars)) for spec in specs})
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return [Entry(s, timeframe, ticker, len(bars), first, last, Origin.BUILT, str(path), s.id) for s in specs]


def build(
    store_dir: Path,
    grid: FeatureGrid = DEFAULT_GRID,
    *,
    tickers: Sequence[Ticker] | None = None,
    workers: int = 1,
    force: bool = False,
) -> int:
    store = Store.open(store_dir)
    catalog = FeatureCatalog(store)
    universe = list(tickers) if tickers else store.manifest.available
    jobs = []
    for tf in grid.timeframes:
        for t in universe:
            present = set() if force else catalog.ids(t, tf)
            missing = tuple(s for s in grid.specs if s.id not in present)
            if missing:
                jobs.append(_Job(store_dir, store.manifest.data_version, t, tf, missing))
    log.info(
        "building",
        grid=grid.id[:12],
        tickers=len(universe),
        timeframes=[tf.value for tf in grid.timeframes],
        specs=len(grid.specs),
        jobs=len(jobs),
    )
    if not jobs:
        log.info("catalog already covers the grid", entries=len(catalog))
        return 0
    entries: list[Entry] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, built in enumerate(pool.map(_build, jobs), 1):
            entries.extend(built)
            if i % 200 == 0 or i == len(jobs):
                log.info("progress", done=i, of=len(jobs))
    catalog.register(entries)
    log.info("built", entries=len(entries), catalog=len(catalog))
    return len(entries)
