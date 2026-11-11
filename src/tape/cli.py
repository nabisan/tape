#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
import platform
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Final

import httpx2
import numpy as np
import numpy.typing as npt
import uvicorn

from tape import log
from tape.catalog.build import REFERENCE, CatalogError, build
from tape.catalog.store import Status, Store
from tape.config import Settings
from tape.core.hashing import Digest, Json
from tape.core.types import Ticker, Timeframe, ticker
from tape.features.adopt import AdoptionError, adopt
from tape.features.build import build as build_features
from tape.service.api import create_app
from tape.service.results import ResultStore
from tape.service.worker import execute_run, init_worker
from tape.sweep import SUMMARY, SweepSpec, expand, summary
from tape.ui.theme import STREAMLIT as THEME
from tape.universe import (
    CONTACT,
    SEC_TICKERS,
    SOURCES,
    Classifications,
    Index,
    classify,
    listing,
    membership,
    save,
    save_classifications,
    sic,
)

PACKAGES = ("tape", "numpy", "pandas", "pyarrow", "numba", "ta-lib", "pydantic", "exchange-calendars")


def _check_talib(out: log.Log) -> bool:
    talib = importlib.import_module("talib")
    sma = talib.SMA(np.arange(1.0, 6.0), timeperiod=3)
    ok = bool(np.isnan(sma[:2]).all() and np.allclose(sma[2:], [2.0, 3.0, 4.0]))
    core = talib.__ta_version__.decode().split()[0]
    out.info("ta-lib loaded", core=core, functions=len(talib.get_functions()), sma_ok=ok)
    return ok


def _total(x: npt.NDArray[np.float64]) -> float:
    s = 0.0
    for v in x:
        s += v
    return s


def _check_numba(out: log.Log) -> bool:
    numba = importlib.import_module("numba")
    ok = bool(numba.njit(_total)(np.arange(10.0)) == 45.0)
    out.info(
        "numba jit",
        llvm=".".join(map(str, importlib.import_module("llvmlite.binding").llvm_version_info)),
        threads=numba.config.NUMBA_NUM_THREADS,
        ok=ok,
    )
    return ok


def env(settings: Settings) -> int:
    out = log.Log("Env")
    out.info(
        "python", version=platform.python_version(), system=platform.system(), machine=platform.machine()
    )
    for name in PACKAGES:
        out.info("package", name=name, version=metadata.version(name))
    ok = _check_talib(out) & _check_numba(out)
    for role, path in (
        ("source", settings.source_dir),
        ("store", settings.store_dir),
        ("results", settings.results_dir),
    ):
        if path.is_dir():
            out.info("path", role=role, path=path, parquet=sum(1 for _ in path.glob("*.parquet")))
        else:
            out.warn("path missing", role=role, path=path)
    if not ok:
        out.error("environment check failed")
    return 0 if ok else 1


def catalog_build(settings: Settings, args: argparse.Namespace) -> int:
    try:
        build(
            settings.source_dir,
            settings.store_dir,
            tickers=[ticker(t) for t in args.ticker] or None,
            reference=ticker(args.reference),
            workers=args.workers,
        )
    except CatalogError as e:
        log.Log("Catalog").error("build failed", reason=e)
        return 1
    return 0


def catalog_show(settings: Settings, args: argparse.Namespace) -> int:
    out = log.Log("Catalog")
    m = Store.open(settings.store_dir, Digest(args.data_version) if args.data_version else None).manifest
    out.info(
        "catalog",
        data_version=m.data_version,
        built_at=m.built_at.isoformat(timespec="seconds"),
        sessions=m.sessions,
        first=m.first_session,
        last=m.last_session,
        tickers=len(m.available),
        calendar=m.calendar_library,
    )
    for r in m.tickers.values():
        if r.status is Status.REJECTED:
            out.warn("rejected", ticker=r.ticker, reason=r.reason)
        elif r.sessions_missing or r.drops.total or r.non_session_days:
            out.warn(
                "gaps",
                ticker=r.ticker,
                missing=len(r.sessions_missing),
                first_missing=r.sessions_missing[0] if r.sessions_missing else None,
                dropped=r.drops.total,
                non_session_days=r.non_session_days,
            )
    return 0


def features_adopt(settings: Settings, args: argparse.Namespace) -> int:
    try:
        adopt(
            settings.store_dir,
            Path(args.source).expanduser(),
            workers=args.workers,
            audit_every=args.audit_every,
        )
    except AdoptionError as e:
        log.Log("Features").error("adoption failed", reason=e)
        return 1
    return 0


def features_build(settings: Settings, args: argparse.Namespace) -> int:
    build_features(
        settings.store_dir,
        tickers=[ticker(t) for t in args.ticker] or None,
        workers=args.workers,
        force=args.force,
    )
    return 0


def _values(text: str) -> tuple[Json, ...]:
    return tuple(int(v) if v.lstrip("-").isdigit() else v for v in text.split(",") if v)


def _assignments(items: list[str]) -> dict[str, tuple[Json, ...]]:
    out: dict[str, tuple[Json, ...]] = {}
    for item in items:
        name, _, values = item.partition("=")
        out[name] = _values(values)
    return out


def sweep(settings: Settings, args: argparse.Namespace) -> int:
    out = log.Log("Sweep")
    store = Store.open(settings.store_dir)
    m = store.manifest
    spec = SweepSpec(
        tickers=tuple(ticker(t) for t in args.ticker) or tuple(m.available),
        start=date.fromisoformat(args.start) if args.start else m.first_session,
        end=date.fromisoformat(args.end) if args.end else m.last_session,
        strategy=args.strategy,
        timeframe=Timeframe(args.timeframe),
        grid=_assignments(args.grid),
        params={k: v[0] for k, v in _assignments(args.param).items()},
    )
    plan = expand(spec, store)
    for s in plan.skipped:
        out.warn("skipped", ticker=s.ticker, reason=s.reason)
    results = ResultStore(settings.results_dir)
    keys = [r.key(m.data_version) for r in plan.runs]
    pending = [r.model_dump_json() for r, k in zip(plan.runs, keys, strict=True) if not results.exists(k)]
    out.info(
        "sweep", key=spec.key[:12], runs=len(keys), cached=len(keys) - len(pending), workers=args.workers
    )
    initargs = (str(settings.store_dir), m.data_version, str(settings.results_dir), settings.log_level)
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker, initargs=initargs) as pool:
        for i, _ in enumerate(pool.map(execute_run, pending), 1):
            if i % 50 == 0 or i == len(pending):
                out.info("progress", done=i, of=len(pending))
    typed = summary(plan, keys, results)
    results.save_sweep(spec.key, spec, typed)
    frame = typed.to_pandas()
    out.info("summary", path=results.sweep_summary(spec.key), rows=len(frame))
    for _, row in frame.sort_values("sharpe", ascending=False).head(args.top).iterrows():
        swept = {
            k: row[k] for k in spec.grid or frame.columns if k in frame.columns and k not in {"key", *SUMMARY}
        }
        out.info(
            "top",
            **swept,
            sharpe=round(row["sharpe"], 2),
            cagr=round(row["cagr_pct"], 2),
            maxdd=round(row["max_drawdown_pct"], 2),
            trades=row["trades"],
        )
    return 0


def catalog_tickers(settings: Settings, api: str | None) -> set[Ticker]:
    if api is not None:
        response = httpx2.get(f"{api.rstrip('/')}/catalog", timeout=30)
        response.raise_for_status()
        return {ticker(t["ticker"]) for t in response.json()["tickers"]}
    if settings.store_dir.is_dir():
        return set(Store.open(settings.store_dir).manifest.available)
    return set()


def universes_fetch(settings: Settings, args: argparse.Namespace) -> int:
    out = log.Log("Universe")
    available = catalog_tickers(settings, args.api)
    today = date.today()
    listings = {index: listing(index, today) for index in Index}
    for index, found in listings.items():
        target = save(membership(index, found))
        out.info(
            "membership",
            index=index.value,
            members=len(found.tickers),
            in_catalog=len(available & set(found.tickers)),
            as_of=found.as_of,
            path=target,
        )
    tickers = available.union(*(found.tickers for found in listings.values()))
    names = {t: n for found in listings.values() for t, n in found.names.items()}
    gics = {t: g for found in listings.values() for t, g in found.gics.items()}
    contact = os.environ.get(CONTACT, "")
    if contact:
        sics = sic(sorted(tickers), contact)
    else:
        out.warn("sic skipped", reason=f"set {CONTACT} to the contact SEC EDGAR requires")
        sics = {}
    entries = classify(tickers, names, gics, sics)
    target = save_classifications(
        Classifications(
            as_of=today,
            sources={"gics": SOURCES[Index.SP500], "sic": SEC_TICKERS if sics else ""},
            entries=entries,
        )
    )
    out.info(
        "classification",
        tickers=len(entries),
        gics=sum(e.gics is not None for e in entries),
        sic=sum(e.sic is not None for e in entries),
        neither=sum(e.gics is None and e.sic is None for e in entries),
        path=target,
    )
    return 0


def serve_api(settings: Settings, args: argparse.Namespace) -> int:
    uvicorn.run(create_app(settings, args.workers), host=args.host, port=args.port, log_config=None)
    return 0


def serve_ui(args: argparse.Namespace) -> int:
    app = Path(__file__).with_name("ui") / "app.py"
    os.environ.setdefault("TAPE_API_URL", args.api)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app),
            "--server.address",
            args.host,
            "--server.port",
            str(args.port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            *(f"--{k}={v}" for k, v in THEME.items()),
        ],
    )


type Handler = Callable[[Settings, argparse.Namespace], int]

COMMANDS: Final[dict[tuple[str, str | None], Handler]] = {
    ("env", None): lambda s, _: env(s),
    ("catalog", "build"): catalog_build,
    ("catalog", "show"): catalog_show,
    ("features", "adopt"): features_adopt,
    ("features", "build"): features_build,
    ("sweep", None): sweep,
    ("universes", "fetch"): universes_fetch,
    ("serve", "api"): serve_api,
    ("serve", "ui"): lambda _, a: serve_ui(a),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tape", description="bar-level US equity backtesting")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("env", help="verify interpreter, native libraries and configured paths")
    catalog = commands.add_parser("catalog", help="build and inspect the bar store").add_subparsers(
        dest="action", required=True
    )
    b = catalog.add_parser("build", help="validate the source and build a versioned store")
    b.add_argument("--ticker", action="append", default=[], help="repeatable; default every source file")
    b.add_argument("--reference", default=REFERENCE)
    b.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    s = catalog.add_parser("show", help="summarise a store and list rejected or gapped tickers")
    s.add_argument("--data-version")
    features = commands.add_parser("features", help="feature store").add_subparsers(
        dest="action", required=True
    )
    a = features.add_parser("adopt", help="register precomputed features whose bars match the catalog")
    a.add_argument("source", help="directory with bars/ and features/ in the Hive layout, plus manifest.json")
    a.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a.add_argument("--audit-every", type=int, default=20)
    fb = features.add_parser("build", help="materialize the feature grid for every catalog ticker")
    fb.add_argument("--ticker", action="append", default=[], help="repeatable; default every ticker")
    fb.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    fb.add_argument("--force", action="store_true", help="rebuild entries already in the catalog")
    sw = commands.add_parser("sweep", help="run a strategy over a parameter grid and tickers")
    sw.add_argument("--strategy", required=True)
    sw.add_argument("--timeframe", required=True, choices=[tf.value for tf in Timeframe])
    sw.add_argument("--ticker", action="append", default=[], help="repeatable; default every ticker")
    sw.add_argument("--start")
    sw.add_argument("--end")
    sw.add_argument("--grid", action="append", default=[], help="name=v1,v2; window defaults to the catalog")
    sw.add_argument("--param", action="append", default=[], help="name=value held fixed")
    sw.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    sw.add_argument("--top", type=int, default=10)
    universes = commands.add_parser("universes", help="index membership reference data").add_subparsers(
        dest="action", required=True
    )
    uf = universes.add_parser("fetch", help="fetch index constituents and sector classifications")
    uf.add_argument("--api", help="read the catalog tickers from a running tape API instead of the store")
    serve = commands.add_parser("serve", help="run the API or the web UI").add_subparsers(
        dest="action", required=True
    )
    api = serve.add_parser("api", help="backtest service")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ui = serve.add_parser("ui", help="Streamlit front end")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    log.setup(settings.log_level)
    handler = COMMANDS.get((args.command, getattr(args, "action", None)))
    if handler is None:
        parser.error(f"unknown command {args.command}")
    return handler(settings, args)


if __name__ == "__main__":
    sys.exit(main())
