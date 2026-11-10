from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING

import numpy as np
import pyarrow.parquet as pq

from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.features.store import INDEX, Adopted, bars_match, parse_column, part
from tape.log import Log

if TYPE_CHECKING:
    from pathlib import Path

    from tape.core.hashing import Digest

log = Log("Features")


class AdoptionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Job:
    store_dir: Path
    data_version: Digest
    source: Path
    ticker: Ticker
    timeframe: Timeframe
    audit: bool


def _check(job: _Job) -> tuple[Timeframe, Ticker, list[str] | None, str]:
    features = part(job.source, "features", job.timeframe, job.ticker)
    bars_file = part(job.source, "bars", job.timeframe, job.ticker)
    if not features.is_file() or not bars_file.is_file():
        return job.timeframe, job.ticker, None, "missing"
    bars = Store.open(job.store_dir, job.data_version).bars(job.ticker, job.timeframe)
    if not bars_match(bars, bars_file):
        return job.timeframe, job.ticker, None, "bars differ"
    columns = [c for c in pq.read_schema(features).names if parse_column(c) is not None]
    if job.audit:
        table = pq.read_table(features, columns=columns)
        for name in columns:
            spec = parse_column(name)
            assert spec is not None
            if not np.array_equal(spec.compute(bars), table[name].to_numpy(), equal_nan=True):
                raise AdoptionError(
                    f"{job.ticker} {job.timeframe} {name}: precomputed values differ from tape"
                )
    return job.timeframe, job.ticker, columns, "adopted"


def adopt(store_dir: Path, source: Path, *, workers: int = 1, audit_every: int = 20) -> Adopted:
    store = Store.open(store_dir)
    manifest = json.loads((source / "manifest.json").read_text())
    talib = metadata.version("ta-lib")
    if manifest.get("talib") != talib:
        raise AdoptionError(f"source built with TA-Lib {manifest.get('talib')}, tape runs {talib}")
    timeframes = sorted(
        (Timeframe(p.name.removeprefix("timeframe=")) for p in (source / "features").glob("timeframe=*")),
        key=list(Timeframe).index,
    )
    tickers = store.manifest.available
    jobs = [(t, tf) for tf in timeframes for t in tickers]
    audit = [i % audit_every == 0 for i in range(len(jobs))]
    log.info("adopting", source=source, timeframes=[tf.value for tf in timeframes], jobs=len(jobs))

    files: dict[str, dict[str, list[str]]] = {tf.value: {} for tf in timeframes}
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        work = [
            _Job(store_dir, store.manifest.data_version, source, t, tf, audited)
            for (t, tf), audited in zip(jobs, audit, strict=True)
        ]
        for tf, t, columns, outcome in pool.map(_check, work):
            counts[outcome] = counts.get(outcome, 0) + 1
            if columns is None:
                log.warn("not adopted", ticker=t, timeframe=tf.value, reason=outcome)
            else:
                files[tf.value][t] = columns

    adopted = Adopted(source=str(source.resolve()), talib=talib, files=files)
    index = store.root / INDEX
    index.parent.mkdir(parents=True, exist_ok=True)
    tmp = index.with_name(f".{index.name}.{os.getpid()}.tmp")
    tmp.write_text(adopted.model_dump_json(indent=1))
    tmp.replace(index)
    log.info("adopted", audited=sum(audit), **counts)
    return adopted
