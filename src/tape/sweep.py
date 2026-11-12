from __future__ import annotations

import itertools
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tape.core.hashing import Digest, Json, digest
from tape.core.types import Ticker, Timeframe
from tape.engine import tables
from tape.engine.backtest import ExecutionSpec
from tape.engine.parameters import admits
from tape.engine.reports import Metrics
from tape.features.catalog import FeatureCatalog
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.runner import FeaturePolicy, RunSpec, ready_on
from tape.strategies.registry import STRATEGIES

if TYPE_CHECKING:
    from tape.catalog.store import Store
    from tape.service.results import ResultStore

SUMMARY: Final = (
    "net_pnl",
    "total_return_pct",
    "cagr_pct",
    "volatility_pct",
    "sharpe",
    "sortino",
    "max_drawdown_pct",
    "calmar",
    "trades",
    "first_signal",
    "last_signal",
    "win_rate_pct",
    "profit_factor",
    "time_in_market_pct",
    "buy_hold_return_pct",
)
RETIRED: Final = frozenset({"exposure_pct"})


class SweepSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: tuple[Ticker, ...] = Field(min_length=1)
    start: date
    end: date
    strategy: str
    timeframe: Timeframe
    grid: dict[str, tuple[Json, ...]] = {}
    params: dict[str, Json] = {}
    execution: ExecutionSpec = ExecutionSpec()
    features: FeaturePolicy = FeaturePolicy.MATERIALIZE

    @property
    def key(self) -> Digest:
        return digest(self)


class Skipped(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: Ticker
    reason: str


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: tuple[RunSpec, ...]
    skipped: tuple[Skipped, ...]


def _window(value: Json) -> int:
    assert isinstance(value, int), f"window values are integers, got {value!r}"
    return value


def expand(spec: SweepSpec, store: Store, grid: FeatureGrid = DEFAULT_GRID) -> Plan:
    catalog = FeatureCatalog(store)
    sessions = store.sessions()
    if spec.strategy not in STRATEGIES:
        raise KeyError(f"unknown strategy {spec.strategy!r}")
    cls = STRATEGIES[spec.strategy]
    space = cls.parameters
    runs: list[RunSpec] = []
    skipped: list[Skipped] = []
    for ticker in spec.tickers:
        domains: dict[str, tuple[Json, ...]] = dict(spec.grid)
        if spec.features is not FeaturePolicy.COMPUTE:
            resolved = space.resolve(catalog, ticker, grid)
            if spec.timeframe not in resolved.choices:
                skipped.append(Skipped(ticker=ticker, reason=f"no precomputed features on {spec.timeframe}"))
                continue
            for name, windows in resolved.choices[spec.timeframe].items():
                allowed = tuple(windows)
                domains[name] = tuple(w for w in domains.get(name, allowed) if w in allowed)
        series = store.bars(ticker, spec.timeframe)
        names = list(domains)
        cold = 0
        for combo in itertools.product(*(domains[n] for n in names)):
            values = dict(zip(names, combo, strict=True))
            chosen = {n: _window(values[n]) for n in space.windows if n in values}
            if not admits(space.ordered, chosen):
                continue
            if any(
                (day := ready_on(series, sessions, space.warmup(n, w))) is None or day > spec.end
                for n, w in chosen.items()
            ):
                cold += 1
                continue
            params: dict[str, Json] = {**spec.params, **values, "timeframe": spec.timeframe.value}
            try:
                cls.config_type.model_validate(params)
            except ValidationError:
                continue
            runs.append(
                RunSpec(
                    ticker=ticker,
                    start=spec.start,
                    end=spec.end,
                    strategy=spec.strategy,
                    params=params,
                    execution=spec.execution,
                    features=spec.features,
                )
            )
        if cold:
            skipped.append(
                Skipped(ticker=ticker, reason=f"{cold} combinations need more history than {ticker} has")
            )
        elif not any(r.ticker == ticker for r in runs):
            skipped.append(
                Skipped(ticker=ticker, reason="no parameter combination satisfies the constraints")
            )
    return Plan(runs=tuple(runs), skipped=tuple(skipped))


def metric_type(name: str) -> pa.DataType:
    annotation = Metrics.model_fields[name].annotation
    if annotation is int:
        return tables.I64
    if annotation == (datetime | None):
        return tables.TIME
    return tables.F64


METRIC_TYPES: Final = {f: metric_type(f) for f in SUMMARY}


def summary(plan: Plan, keys: list[Digest], results: ResultStore) -> pa.Table:
    rows: list[dict[str, object]] = []
    for run, key in zip(plan.runs, keys, strict=True):
        if not results.exists(key):
            continue
        m = results.metrics(key)
        swept = {k: v for k, v in run.params.items() if k != "timeframe"}
        rows.append({"ticker": run.ticker, **swept, "key": key, **{f: getattr(m, f) for f in SUMMARY}})
    first = {k: v for k, v in plan.runs[0].params.items() if k != "timeframe"} if plan.runs else {}
    return tables.arrow(rows, tables.summary(first, METRIC_TYPES))


def upgrade(found: pa.Table, results: ResultStore) -> pa.Table:
    if retired := [n for n in found.schema.names if n in RETIRED]:
        found = found.drop_columns(retired)
    missing = [f for f in SUMMARY if f not in found.schema.names]
    if not missing or found.num_rows == 0:
        return found
    metrics = [results.metrics(Digest(str(k))) for k in found["key"].to_pylist()]
    for name in missing:
        values = [getattr(m, name) for m in metrics]
        found = found.append_column(pa.field(name, METRIC_TYPES[name]), pa.array(values, METRIC_TYPES[name]))
    order = [n for n in found.schema.names if n not in SUMMARY] + list(SUMMARY)
    return found.select(order)
