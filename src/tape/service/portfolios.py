from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from tape import portfolio_index
from tape.log import Log
from tape.portfolio_index import IndexState
from tape.service.worker import build_portfolios

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Executor

    from tape.service.results import ResultStore

PARQUET = "application/vnd.apache.parquet"


class Indexer:
    def __init__(self, results: ResultStore, pool: Callable[[], Executor]) -> None:
        self._results, self._pool = results, pool
        self._task: asyncio.Task[None] | None = None
        self._failed: frozenset[str] | None = None
        self._log = Log("Portfolios")

    @property
    def building(self) -> bool:
        return self._task is not None and not self._task.done()

    def state(self) -> IndexState:
        results = self._results
        built = portfolio_index.status(results)
        return IndexState(building=self.building, stale=portfolio_index.stale(results), built=built)

    def kick(self) -> None:
        if self.building or not portfolio_index.stale(self._results):
            return
        stored = frozenset(str(k) for k, _ in portfolio_index.recent(self._results))
        if stored == self._failed:
            return
        self._task = asyncio.get_running_loop().create_task(self._build(stored))

    async def _build(self, stored: frozenset[str]) -> None:
        try:
            rows = await asyncio.get_running_loop().run_in_executor(self._pool(), build_portfolios)
            self._failed = None
            self._log.info("index ready", rows=rows)
        except Exception as e:
            self._failed = stored
            self._log.error("index failed, retried when a simulation is added", reason=e)


def routes(results: ResultStore, indexer: Indexer) -> APIRouter:
    router = APIRouter()

    @router.get("/portfolio-index")
    async def portfolios() -> Response:
        indexer.kick()
        table = portfolio_index.read(results)
        return Response(portfolio_index.encode(table), media_type=PARQUET)

    @router.get("/portfolio-index/status")
    async def status() -> IndexState:
        indexer.kick()
        return indexer.state()

    return router
