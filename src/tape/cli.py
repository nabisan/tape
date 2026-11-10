#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
import platform
import sys
from importlib import metadata
from pathlib import Path

import numpy as np
import numpy.typing as npt
import uvicorn

from tape import log
from tape.catalog.build import REFERENCE, CatalogError, build
from tape.catalog.store import Status, Store
from tape.config import Settings
from tape.core.hashing import Digest
from tape.core.types import ticker
from tape.service.api import create_app
from tape.ui.theme import STREAMLIT as THEME

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
    match args.command, getattr(args, "action", None):
        case "env", _:
            return env(settings)
        case "catalog", "build":
            return catalog_build(settings, args)
        case "catalog", "show":
            return catalog_show(settings, args)
        case "serve", "api":
            return serve_api(settings, args)
        case "serve", "ui":
            return serve_ui(args)
        case _:
            parser.error(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
