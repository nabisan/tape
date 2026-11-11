from __future__ import annotations

import asyncio
import io
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import pandas as pd
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from tape import log
from tape.benchmarks import Comparison, compare
from tape.catalog.store import Status, Store
from tape.core.hashing import Digest
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import ENGINE_VERSION, ExecutionSpec
from tape.engine.parameters import ResolvedSpace
from tape.engine.reports import Metrics
from tape.features.catalog import FeatureCatalog
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.runner import MissingFeaturesError, RunSpec, cold, missing_features, unbuildable, warm
from tape.service.results import BARS_PER_ROW, STALE, ResultStore, StaleResultError, Table, thin
from tape.service.sweeps import SweepManager, SweepStatus, paths
from tape.service.worker import execute_run, init_worker
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec
from tape.universe import Classification, Membership, Profiles, classifications, memberships, profiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from concurrent.futures import Executor

    from tape.catalog.bars import Bars
    from tape.config import Settings

PARQUET = "application/vnd.apache.parquet"
RECENT_BARS = 2_500


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
    intraday: bool
    config_schema: dict[str, Any]


class StrategySpace(BaseModel):
    name: str
    version: int
    ticker: str
    space: ResolvedSpace
    config_schema: dict[str, Any]


class UniverseInfo(BaseModel):
    membership: Membership
    in_catalog: list[str]
    missing: list[str]


class RunDays(BaseModel):
    days: list[date]
    recent: date | None


class RunSummary(BaseModel):
    key: str
    cached: bool
    metrics: Metrics


class RunDetail(BaseModel):
    key: str
    spec: RunSpec
    metrics: Metrics


def _catalog_routes(store: Store, grid: FeatureGrid) -> APIRouter:
    router = APIRouter()
    manifest = store.manifest
    sessions = store.sessions()

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
                title=cls.title,
                version=cls.version,
                intraday=cls.intraday,
                config_schema=cls.config_type.model_json_schema(),
            )
            for name, cls in STRATEGIES.items()
        }

    @lru_cache(maxsize=256)
    def _bars(ticker: Ticker, timeframe: Timeframe) -> Bars:
        return store.bars(ticker, timeframe)

    @router.get("/strategies/{name}/space")
    def space(name: str, ticker: str, end: date | None = None) -> StrategySpace:
        if name not in STRATEGIES:
            raise HTTPException(404, f"unknown strategy {name}")
        if ticker not in manifest.available:
            raise HTTPException(404, f"{ticker} not in catalog {manifest.data_version}")
        cls = STRATEGIES[name]
        resolved = cls.parameters.resolve(FeatureCatalog(store), Ticker(ticker), grid)
        if end is not None:
            resolved = warm(resolved, cls.parameters, lambda tf: _bars(Ticker(ticker), tf), sessions, end)
        return StrategySpace(
            name=name,
            version=cls.version,
            ticker=ticker,
            space=resolved,
            config_schema=cls.config_type.model_json_schema(),
        )

    @router.get("/universes")
    def universes() -> list[UniverseInfo]:
        available = set(manifest.available)
        return [
            UniverseInfo(
                membership=m,
                in_catalog=[t for t in m.tickers if t in available],
                missing=[t for t in m.tickers if t not in available],
            )
            for m in memberships()
        ]

    @router.get("/universes/classification")
    def classification() -> list[Classification]:
        available = set(manifest.available)
        found = classifications()
        known = {e.ticker: e for e in found.entries} if found is not None else {}
        return [known.get(t, Classification(ticker=t)) for t in sorted(available)]

    @lru_cache(maxsize=32)
    def _profiles(before: date) -> Profiles:
        return profiles(store, before)

    @router.get("/universes/profiles")
    def universe_profiles(before: date) -> Profiles:
        return _profiles(before)

    @router.get("/execution/schema")
    def execution_schema() -> dict[str, Any]:
        return ExecutionSpec.model_json_schema()

    return router


def _run_routes(
    store: Store, results: ResultStore, pool: Callable[[], Executor], grid: FeatureGrid
) -> APIRouter:
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
        if reasons := cold(store, spec):
            raise HTTPException(422, f"{spec.ticker}: {'; '.join(reasons)}")
        if results.exists(key):
            return RunSummary(key=key, cached=True, metrics=results.metrics(key))
        if refused := unbuildable(spec, missing_features(FeatureCatalog(store), spec), grid):
            names = ", ".join(f.name for f in refused)
            raise HTTPException(422, f"not precomputed for {spec.ticker}: {names}")
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

    @router.get("/runs/{key}/comparison")
    def comparison(key: str) -> Comparison:
        k = _known(results, key)
        return compare(store, Ticker(results.spec(k).ticker), results.frame(k, Table.EQUITY))

    @lru_cache(maxsize=64)
    def _days(key: Digest, recent: int) -> RunDays:
        times = results.frame(key, Table.BARS)["time"]
        days = pd.DatetimeIndex(times.dt.normalize().unique())
        start = times.iloc[max(0, len(times) - recent)].normalize() if len(times) else None
        return RunDays(days=[d.date() for d in days], recent=start.date() if start is not None else None)

    @router.get("/runs/{key}/days")
    def days(key: str, recent: int = RECENT_BARS) -> RunDays:
        return _days(_known(results, key), recent)

    @router.get("/runs/{key}/{table}")
    def table(
        key: str,
        table: Table,
        start: date | None = None,
        end: date | None = None,
        max_rows: int | None = None,
    ) -> Response:
        k = _known(results, key)
        if start is None and end is None and max_rows is None:
            return Response(results.table(k, table), media_type=PARQUET)
        lo = datetime.combine(start, time()) if start is not None else None
        hi = datetime.combine(end + timedelta(days=1), time()) if end is not None else None
        frame = results.frame(k, table, lo, hi)
        factor = 1
        if table is Table.BARS and max_rows is not None:
            frame, factor = thin(frame, max_rows)
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False)
        return Response(buffer.getvalue(), media_type=PARQUET, headers={BARS_PER_ROW: str(factor)})

    return router


def _sweep_routes(store: Store, results: ResultStore, sweeps: SweepManager) -> APIRouter:
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

    @router.get("/sweeps/{key}/spec")
    def sweep_spec(key: str) -> SweepSpec:
        path = results.sweep_summary(Digest(key)).with_suffix(".json")
        if not path.is_file():
            raise HTTPException(404, f"no sweep {key}")
        try:
            return SweepSpec.model_validate_json(path.read_text())
        except ValidationError as e:
            raise HTTPException(410, f"sweep {key[:8]} was {STALE}") from e

    @router.get("/sweeps/{key}/paths")
    def sweep_paths(key: str) -> Response:
        k = Digest(key)
        if not results.sweep_summary(k).is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        if not results.sweep_paths(k).is_file():
            results.save_paths(k, paths(store, results, k))
        return Response(results.sweep_paths(k).read_bytes(), media_type=PARQUET)

    return router


def create_app(settings: Settings, workers: int, grid: FeatureGrid = DEFAULT_GRID) -> FastAPI:
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
            grid.model_dump_json(),
        )
        app.state.pool = ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=initargs)
        out.info(
            "ready", data_version=manifest.data_version, tickers=len(manifest.available), workers=workers
        )
        yield
        app.state.pool.shutdown(cancel_futures=True)

    app = FastAPI(title="tape", version=str(ENGINE_VERSION), lifespan=lifespan)

    @app.exception_handler(StaleResultError)
    async def stale(_: Request, error: StaleResultError) -> JSONResponse:
        return JSONResponse(status_code=410, content={"detail": str(error)})

    def pool() -> Executor:
        executor: Executor = app.state.pool
        return executor

    sweeps = SweepManager(
        store, results, pool=pool, execute=execute_run, concurrency=max(1, workers) * 2, grid=grid
    )
    app.include_router(_catalog_routes(store, grid))
    app.include_router(_run_routes(store, results, pool, grid))
    app.include_router(_sweep_routes(store, results, sweeps))
    return app


def _known(results: ResultStore, key: str) -> Digest:
    k = Digest(key)
    if not results.exists(k):
        raise HTTPException(404, f"no run {key}")
    return k
