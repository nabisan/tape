from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel, ValidationError

from tape import log
from tape.catalog.store import Status, Store
from tape.core.hashing import Digest
from tape.core.types import Ticker
from tape.engine.backtest import ENGINE_VERSION, ExecutionSpec
from tape.engine.parameters import ResolvedSpace
from tape.engine.reports import Metrics
from tape.features.catalog import FeatureCatalog
from tape.runner import FeaturePolicy, MissingFeaturesError, RunSpec, missing_features
from tape.service.results import ResultStore, Table
from tape.service.sweeps import SweepManager, SweepStatus
from tape.service.worker import execute_run, init_worker
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from concurrent.futures import Executor

    from tape.config import Settings

PARQUET = "application/vnd.apache.parquet"


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
    title: str
    version: int
    config_schema: dict[str, Any]


class StrategySpace(BaseModel):
    name: str
    version: int
    ticker: str
    space: ResolvedSpace
    config_schema: dict[str, Any]


class RunSummary(BaseModel):
    key: str
    cached: bool
    metrics: Metrics


class RunDetail(BaseModel):
    key: str
    spec: RunSpec
    metrics: Metrics


def _catalog_routes(store: Store) -> APIRouter:
    router = APIRouter()
    manifest = store.manifest

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "data_version": manifest.data_version}

    @router.get("/catalog")
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

    @router.get("/strategies")
    def strategies() -> dict[str, StrategyInfo]:
        return {
            name: StrategyInfo(
                title=cls.title, version=cls.version, config_schema=cls.config_type.model_json_schema()
            )
            for name, cls in STRATEGIES.items()
        }

    @router.get("/strategies/{name}/space")
    def space(name: str, ticker: str) -> StrategySpace:
        if name not in STRATEGIES:
            raise HTTPException(404, f"unknown strategy {name}")
        if ticker not in manifest.available:
            raise HTTPException(404, f"{ticker} not in catalog {manifest.data_version}")
        cls = STRATEGIES[name]
        return StrategySpace(
            name=name,
            version=cls.version,
            ticker=ticker,
            space=cls.parameters.resolve(FeatureCatalog(store), Ticker(ticker)),
            config_schema=cls.config_type.model_json_schema(),
        )

    @router.get("/execution/schema")
    def execution_schema() -> dict[str, Any]:
        return ExecutionSpec.model_json_schema()

    return router


def _run_routes(store: Store, results: ResultStore, pool: Callable[[], Executor]) -> APIRouter:
    router = APIRouter()
    manifest = store.manifest
    inflight: dict[Digest, asyncio.Future[Digest]] = {}
    out = log.Log("Api")

    @router.post("/runs")
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
        if spec.features is FeaturePolicy.PRECOMPUTED and (
            missing := missing_features(FeatureCatalog(store), spec)
        ):
            raise HTTPException(
                422, f"not precomputed for {spec.ticker}: {', '.join(f.name for f in missing)}"
            )
        if key not in inflight:
            loop = asyncio.get_running_loop()
            inflight[key] = asyncio.ensure_future(
                loop.run_in_executor(pool(), execute_run, spec.model_dump_json())
            )
            out.info(
                "run", key=key, ticker=spec.ticker, strategy=spec.strategy, start=spec.start, end=spec.end
            )
        try:
            await inflight[key]
        except (ValueError, MissingFeaturesError) as e:
            raise HTTPException(422, str(e)) from e
        finally:
            inflight.pop(key, None)
        return RunSummary(key=key, cached=False, metrics=results.metrics(key))

    @router.get("/runs/{key}")
    def detail(key: str) -> RunDetail:
        k = _known(results, key)
        return RunDetail(key=k, spec=results.spec(k), metrics=results.metrics(k))

    @router.get("/runs/{key}/{table}")
    def table(key: str, table: Table) -> Response:
        return Response(results.table(_known(results, key), table), media_type=PARQUET)

    return router


def _sweep_routes(results: ResultStore, sweeps: SweepManager) -> APIRouter:
    router = APIRouter()

    @router.post("/sweeps")
    async def start_sweep(spec: SweepSpec) -> SweepStatus:
        try:
            return sweeps.start(spec)
        except (KeyError, ValidationError) as e:
            raise HTTPException(422, str(e)) from e

    @router.get("/sweeps/{key}")
    def sweep_status(key: str) -> SweepStatus:
        status = sweeps.status(Digest(key))
        if status is None:
            raise HTTPException(404, f"no sweep {key}")
        return status

    @router.get("/sweeps/{key}/summary")
    def sweep_table(key: str) -> Response:
        path = results.sweep_summary(Digest(key))
        if not path.is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        return Response(path.read_bytes(), media_type=PARQUET)

    return router


def create_app(settings: Settings, workers: int) -> FastAPI:
    out = log.Log("Api")
    store = Store.open(settings.store_dir)
    results = ResultStore(settings.results_dir)
    manifest = store.manifest

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        initargs = (
            str(settings.store_dir),
            manifest.data_version,
            str(settings.results_dir),
            settings.log_level,
        )
        app.state.pool = ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=initargs)
        out.info(
            "ready", data_version=manifest.data_version, tickers=len(manifest.available), workers=workers
        )
        yield
        app.state.pool.shutdown(cancel_futures=True)

    app = FastAPI(title="tape", version=str(ENGINE_VERSION), lifespan=lifespan)

    def pool() -> Executor:
        executor: Executor = app.state.pool
        return executor

    sweeps = SweepManager(store, results, pool, execute_run, max(1, workers) * 2)
    app.include_router(_catalog_routes(store))
    app.include_router(_run_routes(store, results, pool))
    app.include_router(_sweep_routes(results, sweeps))
    return app


def _known(results: ResultStore, key: str) -> Digest:
    k = Digest(key)
    if not results.exists(k):
        raise HTTPException(404, f"no run {key}")
    return k
