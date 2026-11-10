from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pandas as pd
from pydantic import BaseModel

from tape.features.catalog import FeatureCatalog
from tape.log import Log
from tape.sweep import Plan, Skipped, SweepSpec, expand, summary

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Executor

    from tape.catalog.store import Store
    from tape.core.hashing import Digest
    from tape.features.grid import FeatureGrid
    from tape.runner import RunSpec
    from tape.service.results import ResultStore

type State = Literal["running", "done", "failed"]


class SweepStatus(BaseModel):
    key: str
    state: State
    total: int
    done: int
    failed: int
    skipped: list[Skipped]
    error: str | None = None


@dataclass(slots=True)
class _Progress:
    key: Digest
    total: int
    skipped: list[Skipped]
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
        )


class SweepManager:
    def __init__(
        self,
        store: Store,
        results: ResultStore,
        *,
        pool: Callable[[], Executor],
        execute: Callable[[str], Digest],
        concurrency: int,
        grid: FeatureGrid,
    ) -> None:
        self._grid = grid
        self._store = store
        self._results = results
        self._pool = pool
        self._execute = execute
        self._limit = asyncio.Semaphore(concurrency)
        self._sweeps: dict[Digest, _Progress] = {}
        self._log = Log("Sweeps")

    def start(self, spec: SweepSpec) -> SweepStatus:
        key = spec.key
        current = self._sweeps.get(key)
        if current is not None and current.state == "running":
            return current.status()
        plan = expand(spec, FeatureCatalog(self._store), self._grid)
        progress = _Progress(key=key, total=len(plan.runs), skipped=list(plan.skipped))
        self._sweeps[key] = progress
        if self._results.sweep_summary(key).is_file():
            progress.state, progress.done = "done", progress.total
            return progress.status()
        self._log.info(
            "sweep", sweep=key[:12], strategy=spec.strategy, runs=progress.total, tickers=len(spec.tickers)
        )
        progress.task = asyncio.create_task(self._run(spec, plan, progress))
        return progress.status()

    def status(self, key: Digest) -> SweepStatus | None:
        if key in self._sweeps:
            return self._sweeps[key].status()
        path = self._results.sweep_summary(key)
        if not path.is_file():
            return None
        rows = len(pd.read_parquet(path, columns=["key"]))
        return SweepStatus(key=key, state="done", total=rows, done=rows, failed=0, skipped=[])

    async def _run(self, spec: SweepSpec, plan: Plan, progress: _Progress) -> None:
        version = self._store.manifest.data_version
        keys = [r.key(version) for r in plan.runs]
        try:
            await asyncio.gather(*(self._one(r, k, progress) for r, k in zip(plan.runs, keys, strict=True)))
            self._results.save_sweep(spec.key, spec, summary(plan, keys, self._results))
            progress.state = "done"
            self._log.info("sweep done", sweep=progress.key[:12], runs=progress.total, failed=progress.failed)
        except Exception as e:
            progress.state, progress.error = "failed", str(e)
            self._log.error("sweep failed", sweep=progress.key[:12], reason=e)

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
