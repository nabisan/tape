from __future__ import annotations

import asyncio
import io
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from tape import log
from tape.baskets import Basket, BasketQuery, basket, listing
from tape.benchmarks import Comparison, compare
from tape.book import BookError
from tape.catalog.catalogs import Catalogs
from tape.catalog.store import Status, Store
from tape.core.hashing import Digest
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import ENGINE_VERSION, ExecutionSpec
from tape.features.catalog import FeatureCatalog
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.fundamentals import Fundamentals, Valuation
from tape.lab.mount import mount
from tape.portfolio import Portfolio, cached
from tape.runner import MissingFeaturesError, RunSpec, cold, missing_features, unbuildable, warm
from tape.service.ledgers import MEDIA, Ledgers, LedgerStatus, filename
from tape.service.models import (
    AccountView,
    Catalog,
    PathsMeta,
    RunDays,
    RunDetail,
    RunSummary,
    StoredSweep,
    StrategyInfo,
    StrategySpace,
    TickerInfo,
    UniverseInfo,
    VenueInfo,
)
from tape.service.portfolios import Indexer
from tape.service.portfolios import routes as portfolio_routes
from tape.service.prices import POINTS, PriceWindow, price
from tape.service.results import BARS_PER_ROW, STALE, ResultStore, StaleResultError, Table, thin
from tape.service.sweeps import SweepManager, SweepStatus, book, paths
from tape.service.worker import (
    build_ledger,
    execute_book_run,
    execute_run,
    init_ledger_worker,
    init_worker,
    versions,
)
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec
from tape.sweep import upgrade as upgrade_summary
from tape.universe import Classification, Profiles, classifications, memberships, profiles
from tape.venues import Venue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from concurrent.futures import Executor

    from tape.catalog.bars import Bars
    from tape.config import Settings

PARQUET = "application/vnd.apache.parquet"
FLOAT32 = "application/octet-stream"
RECENT_BARS = 2_500
GZIP_FROM = 1_024


def _venue_info(store: Store) -> VenueInfo:
    venue, m = store.venue, store.manifest
    return VenueInfo(
        venue=venue,
        label=venue.label,
        data_version=m.data_version,
        zone=venue.zone,
        periods=venue.periods,
        volume=venue.volume,
        first_session=m.first_session,
        last_session=m.last_session,
    )


def _ticker_info(store: Store) -> list[TickerInfo]:
    return [
        TickerInfo(
            ticker=r.ticker,
            first_session=r.first_session,
            last_session=r.last_session,
            venue=store.venue,
            price_precision=store.venue.instrument(r.ticker).price_precision,
        )
        for r in store.manifest.tickers.values()
        if r.status is Status.OK and r.first_session is not None and r.last_session is not None
    ]


def _store_for(catalogs: Catalogs, ticker: Ticker) -> Store:
    if not catalogs.has(ticker):
        raise HTTPException(404, f"{ticker} is in no catalog")
    return catalogs.store(ticker)


def _comparable(catalogs: Catalogs, ticker: Ticker) -> Store:
    store = catalogs.store(ticker) if catalogs.has(ticker) else catalogs.of(Venue.XNYS)
    if store is None:
        raise HTTPException(404, f"{ticker} is in no catalog")
    return store


def _catalog_routes(catalogs: Catalogs, grid: FeatureGrid) -> APIRouter:
    router = APIRouter()
    stores = [s for v in catalogs.venues if (s := catalogs.of(v)) is not None]
    primary = stores[0]
    equities = catalogs.of(Venue.XNYS)
    listed = set(equities.manifest.available) if equities is not None else set()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "data_version": primary.manifest.data_version}

    @router.get("/catalog")
    def catalog() -> Catalog:
        return Catalog(
            data_version=primary.manifest.data_version,
            first_session=min(s.manifest.first_session for s in stores),
            last_session=max(s.manifest.last_session for s in stores),
            tickers=[t for s in stores for t in _ticker_info(s)],
            venues=[_venue_info(s) for s in stores],
        )

    @router.get("/strategies")
    def strategies() -> dict[str, StrategyInfo]:
        return {
            name: StrategyInfo(
                title=cls.title,
                version=cls.version,
                intraday=cls.intraday,
                exits=sorted(cls.exits),
                config_schema=cls.config_type.model_json_schema(),
            )
            for name, cls in STRATEGIES.items()
        }

    @lru_cache(maxsize=256)
    def _bars(ticker: Ticker, timeframe: Timeframe) -> Bars:
        return catalogs.store(ticker).bars(ticker, timeframe)

    @router.get("/strategies/{name}/space")
    def space(name: str, ticker: str, end: date | None = None) -> StrategySpace:
        if name not in STRATEGIES:
            raise HTTPException(404, f"unknown strategy {name}")
        cls = STRATEGIES[name]
        store = _store_for(catalogs, Ticker(ticker))
        resolved = cls.parameters.resolve(FeatureCatalog(store), Ticker(ticker), grid)
        if end is not None:
            sessions = store.sessions()
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
        available = listed
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
        available = listed
        found = classifications()
        known = {e.ticker: e for e in found.entries} if found is not None else {}
        return [known.get(t, Classification(ticker=t)) for t in sorted(available)]

    @lru_cache(maxsize=32)
    def _profiles(before: date) -> Profiles:
        if equities is None:
            raise HTTPException(404, "no equity catalog")
        return profiles(equities, before)

    @router.get("/universes/profiles")
    def universe_profiles(before: date) -> Profiles:
        return _profiles(before)

    @router.post("/universes/basket")
    def universe_basket(query: BasketQuery) -> Basket:
        if query.venue is not Venue.XNYS:
            store = catalogs.of(query.venue)
            return listing(query, store.manifest.available if store is not None else [])
        found = classifications()
        entries = {str(e.ticker): e for e in found.entries} if found is not None else {}
        return basket(query, sorted(listed), memberships(), entries, _profiles(query.start))

    @router.get("/execution/schema")
    def execution_schema() -> dict[str, Any]:
        return ExecutionSpec.model_json_schema()

    return router


def _book_routes(results: ResultStore) -> APIRouter:
    router = APIRouter()

    @router.get("/runs/{key}/account")
    def account(key: str) -> AccountView:
        book = results.book_of(_known(results, key))
        if book is None or not results.has_book(book):
            raise HTTPException(404, f"run {key[:8]} is not part of a margin book")
        report, table = results.account(book)
        frame = table.to_pandas()
        return AccountView(
            book=book,
            report=report,
            date=[d.date() for d in frame["date"]],
            equity=frame["equity"].tolist(),
            locked=frame["locked"].tolist(),
            free=frame["free"].tolist(),
            maintenance=frame["maintenance"].tolist(),
        )

    return router


def _run_routes(
    catalogs: Catalogs, results: ResultStore, pool: Callable[[], Executor], grid: FeatureGrid
) -> APIRouter:
    router = APIRouter()
    inflight: dict[Digest, asyncio.Future[Digest]] = {}
    out = log.Log("Api")

    @router.post("/runs")
    async def submit(spec: RunSpec) -> RunSummary:
        if spec.strategy not in STRATEGIES:
            raise HTTPException(404, f"unknown strategy {spec.strategy}")
        store = _store_for(catalogs, spec.ticker)
        try:
            key = spec.key(store.manifest.data_version)
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
        ticker = Ticker(results.spec(k).ticker)
        return compare(_comparable(catalogs, ticker), ticker, results.frame(k, Table.EQUITY))

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


def _sweep_routes(results: ResultStore, sweeps: SweepManager) -> APIRouter:
    router = APIRouter()

    @router.get("/sweeps")
    def running_sweeps() -> list[SweepStatus]:
        return sweeps.running()

    @router.get("/sweeps/stored")
    def stored_sweeps() -> list[StoredSweep]:
        out = []
        for key, path in results.stored_sweeps():
            try:
                spec = SweepSpec.model_validate_json(path.read_text())
            except ValidationError:
                continue
            saved = datetime.fromtimestamp(path.with_suffix(".parquet").stat().st_mtime, UTC)
            out.append(
                StoredSweep(
                    key=key,
                    strategy=spec.strategy,
                    timeframe=spec.timeframe,
                    tickers=len(spec.tickers),
                    start=spec.start,
                    end=spec.end,
                    saved=saved,
                    engine=results.sweep_engine(key),
                    margin=spec.execution.margin is not None,
                )
            )
        return out

    @router.post("/sweeps/{key}/stop")
    def stop_sweep(key: str) -> SweepStatus:
        status = sweeps.stop(Digest(key))
        if status is None:
            raise HTTPException(404, f"no running sweep {key}")
        return status

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
        k = Digest(key)
        path = results.sweep_summary(k)
        if not path.is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        stored = pq.read_table(path)
        current = upgrade_summary(stored, results)
        if current is not stored:
            results.replace_summary(k, current)
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

    @router.get("/sweeps/{key}/book")
    def sweep_book(key: str, run: str) -> Response:
        k = Digest(key)
        if not results.sweep_summary(k).is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        try:
            return Response(book(results, k, run), media_type=PARQUET)
        except BookError as e:
            raise HTTPException(422, str(e)) from e
        except ValidationError as e:
            raise HTTPException(410, f"sweep {key[:8]} was {STALE}") from e

    return router


def _portfolio_routes(catalogs: Catalogs, results: ResultStore) -> APIRouter:
    router = APIRouter()

    @router.get("/sweeps/{key}/portfolio")
    def sweep_portfolio(key: str, run: str) -> Portfolio:
        k = Digest(key)
        summary = results.sweep_summary(k)
        if not summary.is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        try:
            spec = SweepSpec.model_validate_json(summary.with_suffix(".json").read_text())
            store = catalogs.common(spec.tickers)
            return cached(results, k, run, store, catalogs.of(Venue.XNYS))
        except BookError as e:
            raise HTTPException(422, str(e)) from e
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValidationError as e:
            raise HTTPException(410, f"sweep {key[:8]} was {STALE}") from e

    return router


def _path_routes(catalogs: Catalogs, results: ResultStore) -> APIRouter:
    router = APIRouter()

    def stored_paths(key: str) -> Path:
        k = Digest(key)
        if not results.sweep_summary(k).is_file():
            raise HTTPException(404, f"sweep {key} has no summary yet")
        if not results.sweep_paths(k).is_file():
            results.save_paths(k, paths(catalogs, results, k))
        return results.sweep_paths(k)

    @router.get("/sweeps/{key}/paths")
    def sweep_paths(key: str) -> Response:
        return Response(stored_paths(key).read_bytes(), media_type=PARQUET)

    @router.get("/sweeps/{key}/paths/meta")
    def sweep_paths_meta(key: str) -> PathsMeta:
        found = pq.read_table(stored_paths(key))
        days = found["date"].cast(pa.date32()).to_pylist()
        if any(d is None for d in days):
            raise HTTPException(500, f"sweep {key[:8]} paths have missing dates")
        return PathsMeta(
            dates=[d for d in days if d is not None], series=[n for n in found.schema.names if n != "date"]
        )

    @router.get("/sweeps/{key}/paths/values")
    def sweep_paths_values(key: str) -> Response:
        found = pq.read_table(stored_paths(key))
        series = [n for n in found.schema.names if n != "date"]
        body = b"".join(found[n].to_numpy(zero_copy_only=False).astype("<f4").tobytes() for n in series)
        return Response(body, media_type=FLOAT32)

    return router


def create_app(settings: Settings, workers: int, grid: FeatureGrid = DEFAULT_GRID) -> FastAPI:
    out = log.Log("Api")
    catalogs = Catalogs.open(settings.store_dir)
    results = ResultStore(settings.results_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        initargs = (
            str(settings.store_dir),
            versions(catalogs),
            str(settings.results_dir),
            settings.log_level,
            grid.model_dump_json(),
        )
        app.state.pool = ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=initargs)
        app.state.ledger_pool = ProcessPoolExecutor(
            max_workers=1, initializer=init_ledger_worker, initargs=initargs
        )
        app.state.book_pool = ProcessPoolExecutor(
            max_workers=1, max_tasks_per_child=1, initializer=init_worker, initargs=initargs
        )
        app.state.index_pool = ProcessPoolExecutor(
            max_workers=1, max_tasks_per_child=1, initializer=init_worker, initargs=initargs
        )
        indexer.kick()
        for venue, version in catalogs.versions.items():
            out.info("catalog", venue=venue, data_version=version)
        out.info("ready", tickers=len(catalogs.available), workers=workers)
        yield
        app.state.pool.shutdown(cancel_futures=True)
        app.state.ledger_pool.shutdown(cancel_futures=True)
        app.state.book_pool.shutdown(cancel_futures=True)
        app.state.index_pool.shutdown(cancel_futures=True)

    app = FastAPI(title="tape", version=str(ENGINE_VERSION), lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_FROM)

    @app.exception_handler(StaleResultError)
    async def stale(_: Request, error: StaleResultError) -> JSONResponse:
        return JSONResponse(status_code=410, content={"detail": str(error)})

    def pool() -> Executor:
        executor: Executor = app.state.pool
        return executor

    def ledger_pool() -> Executor:
        executor: Executor = app.state.ledger_pool
        return executor

    def book_pool() -> Executor:
        executor: Executor = app.state.book_pool
        return executor

    def index_pool() -> Executor:
        executor: Executor = app.state.index_pool
        return executor

    indexer = Indexer(results, index_pool)
    sweeps = SweepManager(
        catalogs,
        results,
        pool=pool,
        execute=execute_run,
        execute_book=execute_book_run,
        book_pool=book_pool,
        concurrency=max(1, workers) * 2,
        grid=grid,
        on_done=indexer.kick,
    )
    app.include_router(_catalog_routes(catalogs, grid))
    app.include_router(_valuation_routes(results, Fundamentals.open(settings.store_dir)))
    app.include_router(_ledger_routes(results, Ledgers(results, ledger_pool, build_ledger)))
    app.include_router(_price_routes(results))
    app.include_router(_book_routes(results))
    app.include_router(_run_routes(catalogs, results, pool, grid))
    app.include_router(_sweep_routes(results, sweeps))
    app.include_router(_path_routes(catalogs, results))
    app.include_router(_portfolio_routes(catalogs, results))
    app.include_router(portfolio_routes(results, indexer))
    mount(app)
    return app


def _valuation_routes(results: ResultStore, fundamentals: Fundamentals | None) -> APIRouter:
    router = APIRouter()

    @router.get("/runs/{key}/valuation")
    def valuation(key: str) -> Valuation:
        k = _known(results, key)
        spec = results.spec(k)
        days = results.frame(k, Table.EQUITY)["date"]
        start, end = (days.iloc[0].date(), days.iloc[-1].date()) if len(days) else (spec.start, spec.end)
        if fundamentals is None:
            return Valuation(ticker=spec.ticker, start=None, end=None)
        return fundamentals.valuation(spec.ticker, start, end)

    return router


def _ledger_routes(results: ResultStore, ledgers: Ledgers) -> APIRouter:
    router = APIRouter()

    @router.get("/ledgers")
    def building() -> list[str]:
        return ledgers.building()

    @router.get("/runs/{key}/ledger")
    def ledger_status(key: str) -> LedgerStatus:
        return ledgers.status(_known(results, key))

    @router.post("/runs/{key}/ledger")
    def ledger_start(key: str) -> LedgerStatus:
        return ledgers.start(_known(results, key))

    @router.get("/runs/{key}/ledger.xlsx")
    def ledger_file(key: str) -> FileResponse:
        k = _known(results, key)
        path = ledgers.path(k)
        if not path.is_file():
            raise HTTPException(404, f"the ledger for run {key[:8]} is not built yet")
        return FileResponse(path, media_type=MEDIA, filename=filename(results.spec(k)))

    return router


def _price_routes(results: ResultStore) -> APIRouter:
    router = APIRouter()

    @router.get("/runs/{key}/price")
    def run_price(
        key: str, first: int | None = None, last: int | None = None, points: int = POINTS
    ) -> PriceWindow:
        return price(results, _known(results, key), first, last, points)

    return router


def _known(results: ResultStore, key: str) -> Digest:
    k = Digest(key)
    if not results.exists(k):
        raise HTTPException(404, f"no run {key}")
    return k
