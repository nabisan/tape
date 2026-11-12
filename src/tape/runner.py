from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, model_validator

from tape.core.hashing import Digest, Json, digest
from tape.core.types import DATE, Ticker, Timeframe
from tape.engine import reports
from tape.engine.backtest import ENGINE_VERSION, BacktestEngine, ExecutionSpec
from tape.engine.ledger import Ledger
from tape.features.build import materialize
from tape.features.catalog import FeatureCatalog
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.strategies.registry import STRATEGIES, create

if TYPE_CHECKING:
    from collections.abc import Callable

    import pyarrow as pa

    from tape.calendar import Sessions
    from tape.catalog.bars import Bars
    from tape.catalog.store import Store
    from tape.core.types import F64
    from tape.engine.ledger import TradeSummary
    from tape.engine.parameters import ParameterSpace, ResolvedSpace
    from tape.features.kinds import FeatureSpec


class FeaturePolicy(StrEnum):
    MATERIALIZE = "materialize"
    PRECOMPUTED = "precomputed"
    COMPUTE = "compute"


class MissingFeaturesError(Exception):
    pass


def missing_features(catalog: FeatureCatalog, spec: RunSpec) -> list[FeatureSpec]:
    strategy = create(spec.strategy, spec.params)
    required = type(strategy).parameters.requires(strategy.config)
    return [f for f in required if catalog.lookup(spec.ticker, strategy.config.timeframe, f) is None]


def unbuildable(
    spec: RunSpec, missing: list[FeatureSpec], grid: FeatureGrid = DEFAULT_GRID
) -> list[FeatureSpec]:
    match spec.features:
        case FeaturePolicy.COMPUTE:
            return []
        case FeaturePolicy.PRECOMPUTED:
            return missing
        case FeaturePolicy.MATERIALIZE:
            allowed = grid.ids(create(spec.strategy, spec.params).config.timeframe)
            return [f for f in missing if f.id not in allowed]


class RunSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: Ticker
    start: date
    end: date
    strategy: str
    params: dict[str, Json] = {}
    execution: ExecutionSpec = ExecutionSpec()
    features: FeaturePolicy = FeaturePolicy.MATERIALIZE

    @property
    def timeframe(self) -> Timeframe:
        return STRATEGIES[self.strategy].config_type.model_validate(self.params).timeframe

    @model_validator(mode="after")
    def _holding(self) -> Self:
        cls = STRATEGIES.get(self.strategy)
        if cls is not None and self.execution.intraday and not cls.intraday:
            raise ValueError(f"{cls.title} holds overnight; intraday runs are not supported")
        return self

    def key(self, data_version: Digest) -> Digest:
        cls = STRATEGIES[self.strategy]
        config = cls.config_type.model_validate(self.params)
        return digest(
            {
                "run": self.model_dump(mode="json", exclude={"params", "features"}),
                "config": config.model_dump(mode="json"),
                "strategy_version": cls.version,
                "engine_version": ENGINE_VERSION,
                "data_version": data_version,
            }
        )


@dataclass(frozen=True, slots=True)
class Recording:
    table: pa.Table
    trades: dict[int, TradeSummary]
    net_pnl: float


@dataclass(frozen=True, slots=True)
class Result:
    key: Digest
    spec: RunSpec
    metrics: reports.Metrics
    equity: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    bars: pd.DataFrame


class ColdFeaturesError(ValueError):
    pass


def ready_on(bars: Bars, sessions: Sessions, warmup: int) -> date | None:
    if warmup >= len(bars):
        return None
    day: date = sessions.day[bars.session[warmup]].astype(date)
    return day


def cold(store: Store, spec: RunSpec) -> list[str]:
    strategy = create(spec.strategy, spec.params)
    timeframe = strategy.config.timeframe
    required = type(strategy).parameters.requires(strategy.config)
    if not required:
        return []
    bars = store.bars(spec.ticker, timeframe)
    sessions = store.sessions()
    out = []
    for feature in required:
        day = ready_on(bars, sessions, feature.warmup())
        if day is None or day > spec.end:
            when = "never has a value" if day is None else f"has its first value on {day.strftime(DATE)}"
            out.append(
                f"{feature.name} on {timeframe} {when}, after the run ends ({spec.end.strftime(DATE)})"
            )
    return out


def warm(
    resolved: ResolvedSpace,
    space: ParameterSpace,
    bars: Callable[[Timeframe], Bars],
    sessions: Sessions,
    end: date,
) -> ResolvedSpace:
    choices: dict[Timeframe, dict[str, list[int]]] = {}
    ready: dict[Timeframe, dict[str, dict[int, date]]] = {}
    for tf, domain in resolved.choices.items():
        series = bars(tf)
        usable: dict[str, dict[int, date]] = {}
        for name, windows in domain.items():
            usable[name] = {}
            for w in windows:
                day = ready_on(series, sessions, space.warmup(name, w))
                if day is not None and day <= end:
                    usable[name][w] = day
        lists = {name: list(days) for name, days in usable.items()}
        if domain and not resolved.count(lists):
            continue
        choices[tf] = lists
        ready[tf] = usable
    return resolved.model_copy(update={"timeframes": list(choices), "choices": choices, "ready": ready})


def session_range(sessions: Sessions, start: date, end: date) -> tuple[int, int]:
    lo = int(np.searchsorted(sessions.day, np.datetime64(start, "D"), side="left"))
    hi = int(np.searchsorted(sessions.day, np.datetime64(end, "D"), side="right")) - 1
    if hi < lo:
        raise ValueError(f"no sessions between {start} and {end}")
    return lo, hi


@dataclass(frozen=True, slots=True)
class Prepared:
    engine: BacktestEngine
    sessions: Sessions
    m1: Bars
    decision: Bars
    first: int
    last: int


def prepare(store: Store, spec: RunSpec, grid: FeatureGrid, ledger: Ledger | None = None) -> Prepared:
    if reasons := cold(store, spec):
        raise ColdFeaturesError(f"{spec.ticker}: {'; '.join(reasons)}")
    strategy = create(spec.strategy, spec.params)
    timeframe = strategy.config.timeframe
    sessions = store.sessions()
    m1 = store.bars(spec.ticker, Timeframe.M1)
    decision = store.bars(spec.ticker, timeframe)
    first, last = session_range(sessions, spec.start, spec.end)
    catalog = FeatureCatalog(store)
    missing = missing_features(catalog, spec)
    if refused := unbuildable(spec, missing, grid):
        names = ", ".join(f.name for f in refused)
        raise MissingFeaturesError(f"{spec.ticker} {timeframe}: not precomputed: {names}")
    if missing and spec.features is FeaturePolicy.MATERIALIZE:
        catalog.register(materialize(store, spec.ticker, timeframe, missing))

    def stored(f: FeatureSpec) -> F64 | None:
        entry = catalog.lookup(spec.ticker, timeframe, f)
        return None if entry is None else catalog.values(entry)

    engine = BacktestEngine(
        strategy,
        m1=m1,
        decision=decision,
        sessions=sessions,
        execution=spec.execution,
        stored=stored,
        ledger=ledger,
    )
    return Prepared(engine, sessions, m1, decision, first, last)


def record(store: Store, spec: RunSpec, grid: FeatureGrid = DEFAULT_GRID) -> Recording:
    ledger = Ledger()
    p = prepare(store, spec, grid, ledger)
    p.engine.run(p.first, p.last)
    return Recording(
        table=reports.ledger(ledger, p.decision, p.engine.ctx, spec.execution.initial_cash),
        trades=ledger.trades(p.engine.ctx.portfolio),
        net_pnl=ledger.marks[-1].equity - spec.execution.initial_cash if ledger.marks else 0.0,
    )


def execute(store: Store, spec: RunSpec, grid: FeatureGrid = DEFAULT_GRID) -> Result:
    p = prepare(store, spec, grid)
    engine, sessions, m1, decision, first, last = p.engine, p.sessions, p.m1, p.decision, p.first, p.last
    run = engine.run(first, last)

    equity = reports.equity(run, sessions, spec.execution.initial_cash)
    trades = reports.trades(run, m1)
    k0, k1 = run.first_bar, run.last_bar + 1
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(decision.time_ns[k0:k1], unit="ns"),
            "open": decision.open[k0:k1],
            "high": decision.high[k0:k1],
            "low": decision.low[k0:k1],
            "close": decision.close[k0:k1],
            "volume": decision.volume[k0:k1],
            "equity": run.bar_equity,
        }
    )
    for feature, values in engine.ctx.features():
        frame[feature.name] = values[k0:k1]
    return Result(
        key=spec.key(store.manifest.data_version),
        spec=spec,
        metrics=reports.metrics(
            equity,
            trades,
            spec.execution.initial_cash,
            run.orders,
            float(decision.open[run.first_bar]),
            bar_ns=decision.time_ns[k0:k1],
        ),
        equity=equity,
        trades=trades,
        orders=reports.orders(run),
        fills=reports.fills(run),
        bars=frame,
    )
