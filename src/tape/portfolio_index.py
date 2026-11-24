from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, ValidationError

from tape.book import parameters
from tape.engine.backtest import ENGINE_VERSION
from tape.log import Log
from tape.portfolio import VERSION, Portfolio, cached
from tape.service.results import scratch
from tape.sheets import GENERIC
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec
from tape.venues import Venue

if TYPE_CHECKING:
    from pathlib import Path

    from tape.catalog.catalogs import Catalogs
    from tape.core.hashing import Digest
    from tape.service.results import ResultStore

FILE: Final = "portfolios.parquet"
STATUS: Final = "portfolios.json"
RECENT: Final = 10

_FIELDS: Final[list[pa.Field[Any]]] = [
    pa.field("sweep", pa.string(), nullable=False),
    pa.field("run", pa.string(), nullable=False),
    pa.field("book", pa.string(), nullable=False),
    pa.field("strategy", pa.string(), nullable=False),
    pa.field("strategy_title", pa.string(), nullable=False),
    pa.field("market", pa.string(), nullable=False),
    pa.field("timeframe", pa.string(), nullable=False),
    pa.field("tickers", pa.int64(), nullable=False),
    pa.field("start", pa.date32(), nullable=False),
    pa.field("end", pa.date32(), nullable=False),
    pa.field("years", pa.float64(), nullable=False),
    pa.field("parameters", pa.string(), nullable=False),
    pa.field("parameters_json", pa.string(), nullable=False),
    pa.field("stop_loss_pct", pa.float64()),
    pa.field("fills_on", pa.string(), nullable=False),
    pa.field("intraday", pa.bool_(), nullable=False),
    pa.field("margin", pa.string(), nullable=False),
    pa.field("commission_per_share", pa.float64(), nullable=False),
    pa.field("slippage_bps", pa.float64(), nullable=False),
    pa.field("net_pnl", pa.float64(), nullable=False),
    pa.field("total_return_pct", pa.float64(), nullable=False),
    pa.field("cagr_pct", pa.float64(), nullable=False),
    pa.field("volatility_pct", pa.float64(), nullable=False),
    pa.field("sharpe", pa.float64(), nullable=False),
    pa.field("max_drawdown_pct", pa.float64(), nullable=False),
    pa.field("max_capital_loss_pct", pa.float64(), nullable=False),
    pa.field("beta", pa.float64(), nullable=False),
    pa.field("correlation", pa.float64(), nullable=False),
    pa.field("trades", pa.int64(), nullable=False),
    pa.field("hold_return_pct", pa.float64(), nullable=False),
    pa.field("qqq_return_pct", pa.float64(), nullable=False),
    pa.field("benchmark_ends", pa.date32()),
    pa.field("capital", pa.float64(), nullable=False),
    pa.field("final_equity", pa.float64(), nullable=False),
    pa.field("avg_capital_used", pa.float64(), nullable=False),
    pa.field("max_capital_used", pa.float64(), nullable=False),
    pa.field("avg_overnight_capital", pa.float64(), nullable=False),
    pa.field("engine", pa.int64()),
    pa.field("outdated", pa.bool_(), nullable=False),
    pa.field("version", pa.int64(), nullable=False),
]
SCHEMA: Final = pa.schema(_FIELDS)

log = Log("Portfolios")


class Status(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    rows: int
    added: int
    sweeps: list[str]
    skipped: dict[str, str]
    seconds: float
    built_at: datetime


class IndexState(BaseModel):
    model_config = ConfigDict(frozen=True)

    building: bool
    stale: bool
    built: Status | None


def status(results: ResultStore) -> Status | None:
    path = results.root / STATUS
    if not path.is_file():
        return None
    try:
        return Status.model_validate_json(path.read_text())
    except ValidationError:
        return None


def stale(results: ResultStore) -> bool:
    found = status(results)
    if found is None or found.version != VERSION:
        return True
    return {str(k) for k, _ in recent(results)} != set(found.sweeps)


def recent(results: ResultStore) -> list[tuple[Digest, Path]]:
    return results.stored_sweeps()[:RECENT]


def anchors(summary: pd.DataFrame) -> list[str]:
    names = [c for c in parameters(summary.columns) if c != "ticker"]
    if not names:
        return [str(summary["key"].iloc[0])] if len(summary) else []
    first = summary.drop_duplicates(subset=names, keep="first")
    return [str(k) for k in first["key"]]


def describe(spec: SweepSpec, row: pd.Series) -> tuple[dict[str, object], float | None]:
    cls = STRATEGIES[spec.strategy]
    fields = cls.config_type.model_fields
    varied = {c: _scalar(row[c]) for c in parameters(row.index) if c != "ticker" and c in fields}
    config = cls.config_type.model_validate({**spec.params, **varied, "timeframe": spec.timeframe.value})
    dumped = config.model_dump(mode="json")
    stop = dumped.get("stop_loss_pct")
    return {k: v for k, v in dumped.items() if k not in GENERIC}, float(stop) if stop is not None else None


def text(params: dict[str, object]) -> str:
    return " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in params.items())


def to_row(
    spec: SweepSpec, row: pd.Series, folio: Portfolio, market: Venue, engine: int | None
) -> dict[str, object]:
    params, stop = describe(spec, row)
    lines = {line.label: line for line in folio.lines}
    book, hold = lines["Book"], lines["Hold"]
    qqq = lines.get("QQQ")
    execution = spec.execution
    margin = execution.margin
    return {
        "sweep": folio.sweep,
        "run": folio.run,
        "book": folio.book,
        "strategy": spec.strategy,
        "strategy_title": STRATEGIES[spec.strategy].title,
        "market": market.label,
        "timeframe": spec.timeframe.value,
        "tickers": folio.tickers,
        "start": folio.start,
        "end": folio.end,
        "years": folio.sessions / folio.periods,
        "parameters": text(params),
        "parameters_json": json.dumps(params, sort_keys=True),
        "stop_loss_pct": stop,
        "fills_on": execution.fill_on.value,
        "intraday": execution.intraday,
        "margin": "none" if margin is None else f"{margin.leverage:g}x",
        "commission_per_share": execution.fill.commission_per_share,
        "slippage_bps": execution.fill.slippage_bps,
        "net_pnl": book.net_pnl,
        "total_return_pct": book.total_return_pct,
        "cagr_pct": book.cagr_pct,
        "volatility_pct": book.volatility_pct,
        "sharpe": book.sharpe,
        "max_drawdown_pct": book.max_drawdown_pct,
        "max_capital_loss_pct": folio.capital.max_capital_loss_pct,
        "beta": book.exposure.beta,
        "correlation": book.exposure.correlation,
        "trades": folio.trades,
        "hold_return_pct": hold.total_return_pct,
        "qqq_return_pct": qqq.total_return_pct if qqq is not None else float("nan"),
        "benchmark_ends": qqq.ends if qqq is not None else None,
        "capital": folio.capital.capital,
        "final_equity": folio.capital.final_equity,
        "avg_capital_used": folio.capital.avg_used,
        "max_capital_used": folio.capital.max_used,
        "avg_overnight_capital": folio.capital.avg_overnight,
        "engine": engine,
        "outdated": engine != ENGINE_VERSION,
        "version": VERSION,
    }


def read(results: ResultStore) -> pa.Table:
    path = results.root / FILE
    if not path.is_file():
        return SCHEMA.empty_table()
    found = pq.read_table(path)
    return found if found.schema.remove_metadata().equals(SCHEMA) else SCHEMA.empty_table()


def build(results: ResultStore, catalogs: Catalogs) -> Status:
    t0 = time.monotonic()
    known = {(r["sweep"], r["run"]): r for r in read(results).to_pylist() if r["version"] == VERSION}
    rows: list[dict[str, object]] = []
    skipped: dict[str, str] = {}
    added = 0
    market = catalogs.of(Venue.XNYS)
    stored = recent(results)
    for key, path in stored:
        try:
            spec = SweepSpec.model_validate_json(path.read_text())
            summary = pd.read_parquet(path.with_suffix(".parquet"))
            store = catalogs.common(spec.tickers)
        except Exception as e:
            skipped[key] = _reason(e)
            log.warn("simulation skipped", sweep=key[:8], reason=skipped[key])
            continue
        engine = results.sweep_engine(key)
        for run in anchors(summary):
            if (key, run) in known:
                rows.append(known[(key, run)])
                continue
            try:
                folio = cached(results, key, run, store, market)
                row = summary[summary["key"] == run].iloc[0]
                rows.append(to_row(spec, row, folio, store.venue, engine))
                added += 1
            except Exception as e:
                skipped[f"{key}:{run}"] = _reason(e)
                log.warn("book skipped", sweep=key[:8], run=run[:8], reason=skipped[f"{key}:{run}"])
        log.info("indexed", sweep=key[:8], strategy=spec.strategy, rows=len(rows), added=added)
    write(results, rows)
    built = Status(version=VERSION, rows=len(rows), added=added, sweeps=[str(k) for k, _ in stored],
                   skipped=skipped, seconds=round(time.monotonic() - t0, 1), built_at=datetime.now(UTC))
    results.save_text(results.root / STATUS, built.model_dump_json(indent=2))
    log.info("built", rows=built.rows, added=built.added, skipped=len(skipped), seconds=built.seconds)
    return built


def encode(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    data: bytes = sink.getvalue().to_pybytes()
    return data


def write(results: ResultStore, rows: list[dict[str, object]]) -> Path:
    path = results.root / FILE
    tmp = scratch(path)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    tmp.replace(path)
    return path


def _reason(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _scalar(value: object) -> object:
    return value.item() if hasattr(value, "item") else value
