from __future__ import annotations

import itertools
from datetime import date
from typing import TYPE_CHECKING, Final

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tape.core.hashing import Digest, Json, digest
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import ExecutionSpec
from tape.runner import FeaturePolicy, RunSpec
from tape.strategies.registry import STRATEGIES

if TYPE_CHECKING:
    from tape.features.catalog import FeatureCatalog
    from tape.service.results import ResultStore

SUMMARY: Final = (
    "net_pnl",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "sortino",
    "max_drawdown_pct",
    "calmar",
    "trades",
    "win_rate_pct",
    "profit_factor",
    "exposure_pct",
    "buy_hold_return_pct",
)


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
    features: FeaturePolicy = FeaturePolicy.PRECOMPUTED

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


def expand(spec: SweepSpec, catalog: FeatureCatalog) -> Plan:
    if spec.strategy not in STRATEGIES:
        raise KeyError(f"unknown strategy {spec.strategy!r}")
    cls = STRATEGIES[spec.strategy]
    space = cls.parameters
    runs: list[RunSpec] = []
    skipped: list[Skipped] = []
    for ticker in spec.tickers:
        domains: dict[str, tuple[Json, ...]] = dict(spec.grid)
        if spec.features is FeaturePolicy.PRECOMPUTED:
            resolved = space.resolve(catalog, ticker)
            if spec.timeframe not in resolved.choices:
                skipped.append(Skipped(ticker=ticker, reason=f"no precomputed features on {spec.timeframe}"))
                continue
            for name, windows in resolved.choices[spec.timeframe].items():
                allowed = tuple(windows)
                domains[name] = tuple(w for w in domains.get(name, allowed) if w in allowed)
        names = list(domains)
        for combo in itertools.product(*(domains[n] for n in names)):
            values = dict(zip(names, combo, strict=True))
            if not all(
                o.holds(_window(values[o.lower]), _window(values[o.upper]))
                for o in space.ordered
                if o.lower in values and o.upper in values
            ):
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
        if not any(r.ticker == ticker for r in runs):
            skipped.append(
                Skipped(ticker=ticker, reason="no parameter combination satisfies the constraints")
            )
    return Plan(runs=tuple(runs), skipped=tuple(skipped))


def summary(plan: Plan, keys: list[Digest], results: ResultStore) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for run, key in zip(plan.runs, keys, strict=True):
        if not results.exists(key):
            continue
        m = results.metrics(key)
        swept = {k: v for k, v in run.params.items() if k != "timeframe"}
        rows.append({"ticker": run.ticker, **swept, "key": key, **{f: getattr(m, f) for f in SUMMARY}})
    return pd.DataFrame(rows)
