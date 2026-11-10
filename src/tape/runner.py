from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from tape.core.hashing import Digest, Json, digest
from tape.core.types import Ticker, Timeframe
from tape.engine import reports
from tape.engine.backtest import ENGINE_VERSION, BacktestEngine, ExecutionSpec
from tape.features.catalog import FeatureCatalog
from tape.strategies.registry import STRATEGIES, create

if TYPE_CHECKING:
    from tape.calendar import Sessions
    from tape.catalog.store import Store
    from tape.core.types import F64
    from tape.features.kinds import FeatureSpec


class FeaturePolicy(StrEnum):
    PRECOMPUTED = "precomputed"
    COMPUTE = "compute"


class MissingFeaturesError(Exception):
    pass


def missing_features(catalog: FeatureCatalog, spec: RunSpec) -> list[FeatureSpec]:
    strategy = create(spec.strategy, spec.params)
    required = type(strategy).parameters.requires(strategy.config)
    return [f for f in required if catalog.lookup(spec.ticker, strategy.config.timeframe, f) is None]


class RunSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: Ticker
    start: date
    end: date
    strategy: str
    params: dict[str, Json] = {}
    execution: ExecutionSpec = ExecutionSpec()
    features: FeaturePolicy = FeaturePolicy.PRECOMPUTED

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
class Result:
    key: Digest
    spec: RunSpec
    metrics: reports.Metrics
    equity: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    bars: pd.DataFrame


def session_range(sessions: Sessions, start: date, end: date) -> tuple[int, int]:
    lo = int(np.searchsorted(sessions.day, np.datetime64(start, "D"), side="left"))
    hi = int(np.searchsorted(sessions.day, np.datetime64(end, "D"), side="right")) - 1
    if hi < lo:
        raise ValueError(f"no sessions between {start} and {end}")
    return lo, hi


def execute(store: Store, spec: RunSpec) -> Result:
    strategy = create(spec.strategy, spec.params)
    timeframe = strategy.config.timeframe
    sessions = store.sessions()
    m1 = store.bars(spec.ticker, Timeframe.M1)
    decision = store.bars(spec.ticker, timeframe)
    first, last = session_range(sessions, spec.start, spec.end)
    catalog = FeatureCatalog(store)
    if spec.features is FeaturePolicy.PRECOMPUTED and (missing := missing_features(catalog, spec)):
        names = ", ".join(f.name for f in missing)
        raise MissingFeaturesError(f"{spec.ticker} {timeframe}: not precomputed: {names}")

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
    )
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
        metrics=reports.metrics(equity, trades, spec.execution.initial_cash),
        equity=equity,
        trades=trades,
        orders=reports.orders(run),
        fills=reports.fills(run),
        bars=frame,
    )
