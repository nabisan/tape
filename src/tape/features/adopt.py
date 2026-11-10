from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Final

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.features.catalog import Entry, FeatureCatalog, Origin
from tape.features.kinds import Atr, ChannelHigh, ChannelLow, Ema, FeatureSpec, Sma, Source
from tape.log import Log

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tape.catalog.bars import Bars
    from tape.core.hashing import Digest

log = Log("Features")

BAR_COLUMNS: Final = ("time", "open", "high", "low", "close", "volume")
COLUMNS: Final[dict[str, Callable[[int], FeatureSpec]]] = {
    "sma": lambda n: Sma(window=n),
    "ema": lambda n: Ema(window=n),
    "chan_high": lambda n: ChannelHigh(window=n),
    "chan_low": lambda n: ChannelLow(window=n),
    "atr": lambda n: Atr(window=n),
    "vol_sma": lambda n: Sma(window=n, source=Source.VOLUME),
}


class AdoptionError(Exception):
    pass


def column_spec(name: str) -> FeatureSpec | None:
    kind, _, window = name.rpartition("_")
    make = COLUMNS.get(kind)
    return make(int(window)) if make is not None and window.isdigit() else None


def part(root: Path, dataset: str, timeframe: Timeframe, ticker: Ticker) -> Path:
    return root / dataset / f"timeframe={timeframe.value}" / f"ticker={ticker}" / "part-0.parquet"


def bars_match(bars: Bars, path: Path) -> bool:
    t = pq.read_table(path, columns=list(BAR_COLUMNS))
    if t.num_rows != len(bars):
        return False
    return bool(
        np.array_equal(t["time"].cast(pa.int64()).to_numpy(), bars.time_ns)
        and np.array_equal(t["open"].to_numpy(), bars.open)
        and np.array_equal(t["high"].to_numpy(), bars.high)
        and np.array_equal(t["low"].to_numpy(), bars.low)
        and np.array_equal(t["close"].to_numpy(), bars.close)
        and np.array_equal(t["volume"].to_numpy(), bars.volume)
    )


@dataclass(frozen=True, slots=True)
class _Job:
    store_dir: Path
    data_version: Digest
    source: Path
    ticker: Ticker
    timeframe: Timeframe
    audit: bool


@dataclass(frozen=True, slots=True)
class _Outcome:
    job: _Job
    entries: list[Entry]
    reason: str


def _check(job: _Job) -> _Outcome:
    features = part(job.source, "features", job.timeframe, job.ticker)
    bars_file = part(job.source, "bars", job.timeframe, job.ticker)
    if not features.is_file() or not bars_file.is_file():
        return _Outcome(job, [], "missing")
    store = Store.open(job.store_dir, job.data_version)
    bars = store.bars(job.ticker, job.timeframe)
    if not bars_match(bars, bars_file):
        return _Outcome(job, [], "bars differ")
    specs = {c: s for c in pq.read_schema(features).names if (s := column_spec(c)) is not None}
    if job.audit:
        table = pq.read_table(features, columns=list(specs))
        for name, spec in specs.items():
            if not np.array_equal(spec.compute(bars), table[name].to_numpy(), equal_nan=True):
                raise AdoptionError(
                    f"{job.ticker} {job.timeframe} {name}: precomputed values differ from tape"
                )
    day = store.sessions().day
    first, last = day[int(bars.session[0])].item(), day[int(bars.session[-1])].item()
    entries = [
        Entry(spec, job.timeframe, job.ticker, len(bars), first, last, Origin.ADOPTED, str(features), name)
        for name, spec in specs.items()
    ]
    return _Outcome(job, entries, "adopted")


def adopt(store_dir: Path, source: Path, *, workers: int = 1, audit_every: int = 20) -> int:
    store = Store.open(store_dir)
    manifest = json.loads((source / "manifest.json").read_text())
    talib = metadata.version("ta-lib")
    if manifest.get("talib") != talib:
        raise AdoptionError(f"source built with TA-Lib {manifest.get('talib')}, tape runs {talib}")
    timeframes = sorted(
        (Timeframe(p.name.removeprefix("timeframe=")) for p in (source / "features").glob("timeframe=*")),
        key=list(Timeframe).index,
    )
    jobs = [
        _Job(store_dir, store.manifest.data_version, source.resolve(), t, tf, i % audit_every == 0)
        for i, (tf, t) in enumerate((tf, t) for tf in timeframes for t in store.manifest.available)
    ]
    log.info("adopting", source=source, timeframes=[tf.value for tf in timeframes], jobs=len(jobs))
    entries: list[Entry] = []
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(_check, jobs):
            counts[outcome.reason] = counts.get(outcome.reason, 0) + 1
            if outcome.entries:
                entries.extend(outcome.entries)
            else:
                log.warn(
                    "not adopted",
                    ticker=outcome.job.ticker,
                    timeframe=outcome.job.timeframe.value,
                    reason=outcome.reason,
                )
    catalog = FeatureCatalog(store)
    catalog.register(entries)
    log.info(
        "adopted", entries=len(entries), catalog=len(catalog), audited=sum(j.audit for j in jobs), **counts
    )
    return len(entries)
