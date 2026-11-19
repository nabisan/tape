from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

import pandas as pd
from pydantic import BaseModel

from tape.benchmarks import REFERENCE, prices
from tape.book import combine, encode, members
from tape.engine.backtest import ENGINE_VERSION
from tape.log import Log
from tape.runner import pair_key
from tape.service.results import Table
from tape.sweep import Plan, Skipped, SweepSpec, expand, summary

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Executor

    from tape.catalog.catalogs import Catalogs
    from tape.core.hashing import Digest
    from tape.features.grid import FeatureGrid
    from tape.runner import RunSpec
    from tape.service.results import ResultStore

type State = Literal["running", "done", "failed", "stopped"]

WEEKLY_ABOVE: Final = 2_000_000


class SweepStatus(BaseModel):
    key: str
    state: State
    total: int
    done: int
    failed: int
    skipped: list[Skipped]
    error: str | None = None
    strategy: str = ""
    timeframe: str = ""
    tickers: int = 0


@dataclass(slots=True)
class _Progress:
    key: Digest
    total: int
    skipped: list[Skipped]
    spec: SweepSpec
    state: State = "running"
    done: int = 0
    failed: int = 0
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def status(self) -> SweepStatus:
        return SweepStatus(
            key=self.key,
            state=self.state,
            total=self.total,
            done=self.done,
            failed=self.failed,
            skipped=self.skipped,
            error=self.error,
            strategy=self.spec.strategy,
            timeframe=self.spec.timeframe.value,
            tickers=len(self.spec.tickers),
        )


class SweepManager:
    def __init__(
        self,
        catalogs: Catalogs,
        results: ResultStore,
        *,
        pool: Callable[[], Executor],
        execute: Callable[[str], Digest],
        concurrency: int,
        grid: FeatureGrid,
        execute_book: Callable[[str], Digest] | None = None,
    ) -> None:
        self._grid = grid
        self._catalogs = catalogs
        self._results = results
        self._pool = pool
        self._execute = execute
        self._execute_book = execute_book
        self._limit = asyncio.Semaphore(concurrency)
        self._sweeps: dict[Digest, _Progress] = {}
        self._log = Log("Sweeps")

    def start(self, spec: SweepSpec) -> SweepStatus:
        key = spec.key
        current = self._sweeps.get(key)
        if current is not None and current.state == "running":
            return current.status()
        store = self._catalogs.common(spec.tickers)
        plan = expand(spec, store, self._grid)
        progress = _Progress(key=key, total=len(plan.runs), skipped=list(plan.skipped), spec=spec)
        self._sweeps[key] = progress
        if self._results.sweep_engine(key) == ENGINE_VERSION:
            progress.state, progress.done = "done", progress.total
            return progress.status()
        self._log.info(
            "sweep", sweep=key[:12], strategy=spec.strategy, runs=progress.total, tickers=len(spec.tickers)
        )
        version = store.manifest.data_version
        progress.task = asyncio.create_task(self._run(spec, plan, progress, version))
        return progress.status()

    def running(self) -> list[SweepStatus]:
        return [p.status() for p in self._sweeps.values() if p.state == "running"]

    def stop(self, key: Digest) -> SweepStatus | None:
        progress = self._sweeps.get(key)
        if progress is None:
            return None
        if progress.state == "running" and progress.task is not None:
            progress.state = "stopped"
            progress.task.cancel()
            self._log.info("sweep stopped", sweep=key[:12], done=progress.done, runs=progress.total)
        return progress.status()

    def status(self, key: Digest) -> SweepStatus | None:
        if key in self._sweeps:
            return self._sweeps[key].status()
        path = self._results.sweep_summary(key)
        if not path.is_file():
            return None
        rows = len(pd.read_parquet(path, columns=["key"]))
        return SweepStatus(key=key, state="done", total=rows, done=rows, failed=0, skipped=[])

    async def _run(self, spec: SweepSpec, plan: Plan, progress: _Progress, version: Digest) -> None:
        try:
            if spec.execution.margin is None:
                keys = [r.key(version) for r in plan.runs]
                await asyncio.gather(
                    *(self._one(r, k, progress) for r, k in zip(plan.runs, keys, strict=True))
                )
            else:
                books = grouped(plan.runs)
                owner = {id(r): b[0].book_key([s.ticker for s in b], version) for b in books for r in b}
                keys = [pair_key(owner[id(r)], r.ticker) for r in plan.runs]
                await asyncio.gather(*(self._book(b, version, progress) for b in books))
            self._results.save_sweep(spec.key, spec, summary(plan, keys, self._results))
            progress.state = "done"
            self._log.info("sweep done", sweep=progress.key[:12], runs=progress.total, failed=progress.failed)
        except Exception as e:
            progress.state, progress.error = "failed", str(e)
            self._log.error("sweep failed", sweep=progress.key[:12], reason=e)

    async def _book(self, specs: list[RunSpec], version: Digest, progress: _Progress) -> None:
        key = specs[0].book_key([s.ticker for s in specs], version)
        if not (
            self._results.has_book(key) and all(self._results.exists(pair_key(key, s.ticker)) for s in specs)
        ):
            if self._execute_book is None:
                raise RuntimeError("this service cannot run margin books")
            async with self._limit:
                try:
                    payload = json.dumps([s.model_dump(mode="json") for s in specs])
                    await asyncio.get_running_loop().run_in_executor(
                        self._pool(), self._execute_book, payload
                    )
                except Exception as e:
                    progress.failed += len(specs)
                    self._log.warn("sweep book failed", sweep=progress.key[:12], book=key[:12], reason=e)
                    return
        progress.done += len(specs)

    async def _one(self, run: RunSpec, key: Digest, progress: _Progress) -> None:
        if not self._results.exists(key):
            async with self._limit:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        self._pool(), self._execute, run.model_dump_json()
                    )
                except Exception as e:
                    progress.failed += 1
                    self._log.warn("sweep run failed", sweep=progress.key[:12], ticker=run.ticker, reason=e)
                    return
        progress.done += 1


def grouped(runs: Sequence[RunSpec]) -> list[list[RunSpec]]:
    books: dict[str, list[RunSpec]] = {}
    for r in runs:
        books.setdefault(json.dumps(r.params, sort_keys=True), []).append(r)
    return list(books.values())


def book(results: ResultStore, key: Digest, run: str) -> bytes:
    summary = results.sweep_summary(key)
    spec = SweepSpec.model_validate_json(summary.with_suffix(".json").read_text())
    chosen = members(pd.read_parquet(summary), run)
    sleeves = {t: results.frame(k, Table.EQUITY).set_index("date")["equity"] for t, k in chosen.items()}
    return encode(combine(sleeves, spec.execution.initial_cash))


def paths(catalogs: Catalogs, results: ResultStore, key: Digest) -> pd.DataFrame:
    summary = results.sweep_summary(key)
    spec = SweepSpec.model_validate_json(summary.with_suffix(".json").read_text())
    known = [t for t in spec.tickers if catalogs.has(t)]
    store = catalogs.common(known) if known else None
    keys = pd.read_parquet(summary, columns=["key"])["key"].tolist()
    cash = spec.execution.initial_cash
    frame = pd.DataFrame({k: results.frame(k, Table.EQUITY).set_index("date")["equity"] for k in keys})
    frame = 100 * (frame / cash - 1)
    if store is not None and REFERENCE in store.manifest.available:
        ref = prices(store, REFERENCE, pd.DatetimeIndex(frame.index))
        frame[REFERENCE] = 100 * (ref.close / ref.open - 1)
    if frame.size > WEEKLY_ABOVE:
        frame = frame.resample("W-FRI").last()
    return frame.astype("float32").rename_axis("date").reset_index()
