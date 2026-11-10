from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ValidationError

from tape import log
from tape.catalog.store import Status, Store
from tape.core.hashing import Digest
from tape.engine.backtest import ENGINE_VERSION, ExecutionSpec
from tape.engine.reports import Metrics
from tape.runner import RunSpec, execute
from tape.service.results import ResultStore, Table
from tape.strategies.registry import STRATEGIES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tape.config import Settings
    from tape.log import Level

PARQUET = "application/vnd.apache.parquet"

_WORKER: dict[str, tuple[Store, ResultStore]] = {}


def _init_worker(store_dir: str, data_version: str, results_dir: str, level: Level) -> None:
    log.setup(level)
    _WORKER["state"] = (Store.open(Path(store_dir), Digest(data_version)), ResultStore(Path(results_dir)))


def _execute(payload: str) -> Digest:
    store, results = _WORKER["state"]
    result = execute(store, RunSpec.model_validate_json(payload))
    results.save(result)
    return result.key


class TickerInfo(BaseModel):
    ticker: str
    first_session: str
    last_session: str


class Catalog(BaseModel):
    data_version: str
    first_session: str
    last_session: str
    tickers: list[TickerInfo]


class StrategyInfo(BaseModel):
    version: int
    config_schema: dict[str, Any]


class RunSummary(BaseModel):
    key: str
    cached: bool
    metrics: Metrics


class RunDetail(BaseModel):
    key: str
    spec: RunSpec
    metrics: Metrics


def create_app(settings: Settings, workers: int) -> FastAPI:
    out = log.Log("Api")
    store = Store.open(settings.store_dir)
    results = ResultStore(settings.results_dir)
    manifest = store.manifest
    inflight: dict[Digest, asyncio.Future[Digest]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(
                str(settings.store_dir),
                manifest.data_version,
                str(settings.results_dir),
                settings.log_level,
            ),
        )
        app.state.pool = pool
        out.info(
            "ready", data_version=manifest.data_version, tickers=len(manifest.available), workers=workers
        )
        yield
        pool.shutdown(cancel_futures=True)

    app = FastAPI(title="tape", version=str(ENGINE_VERSION), lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "data_version": manifest.data_version}

    @app.get("/catalog")
    def catalog() -> Catalog:
        return Catalog(
            data_version=manifest.data_version,
            first_session=str(manifest.first_session),
            last_session=str(manifest.last_session),
            tickers=[
                TickerInfo(
                    ticker=r.ticker, first_session=str(r.first_session), last_session=str(r.last_session)
                )
                for r in manifest.tickers.values()
                if r.status is Status.OK
            ],
        )

    @app.get("/strategies")
    def strategies() -> dict[str, StrategyInfo]:
        return {
            name: StrategyInfo(version=cls.version, config_schema=cls.config_type.model_json_schema())
            for name, cls in STRATEGIES.items()
        }

    @app.get("/execution/schema")
    def execution_schema() -> dict[str, Any]:
        return ExecutionSpec.model_json_schema()

    @app.post("/runs")
    async def submit(spec: RunSpec) -> RunSummary:
        if spec.strategy not in STRATEGIES:
            raise HTTPException(404, f"unknown strategy {spec.strategy}")
        if spec.ticker not in manifest.available:
            raise HTTPException(404, f"{spec.ticker} not in catalog {manifest.data_version}")
        try:
            key = spec.key(manifest.data_version)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
        if results.exists(key):
            return RunSummary(key=key, cached=True, metrics=results.metrics(key))
        if key not in inflight:
            loop = asyncio.get_running_loop()
            inflight[key] = asyncio.ensure_future(
                loop.run_in_executor(app.state.pool, _execute, spec.model_dump_json())
            )
            out.info(
                "run", key=key, ticker=spec.ticker, strategy=spec.strategy, start=spec.start, end=spec.end
            )
        try:
            await inflight[key]
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        finally:
            inflight.pop(key, None)
        return RunSummary(key=key, cached=False, metrics=results.metrics(key))

    @app.get("/runs/{key}")
    def detail(key: str) -> RunDetail:
        k = _known(results, key)
        return RunDetail(key=k, spec=results.spec(k), metrics=results.metrics(k))

    @app.get("/runs/{key}/{table}")
    def table(key: str, table: Table) -> Response:
        return Response(results.table(_known(results, key), table), media_type=PARQUET)

    return app


def _known(results: ResultStore, key: str) -> Digest:
    k = Digest(key)
    if not results.exists(k):
        raise HTTPException(404, f"no run {key}")
    return k
