from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from tape.engine.exits import Exit
from tape.engine.parameters import ResolvedSpace
from tape.engine.reports import Metrics
from tape.runner import RunSpec
from tape.universe import Membership


class TickerInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    first_session: date
    last_session: date


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_version: str
    first_session: date
    last_session: date
    tickers: list[TickerInfo]


class StrategyInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    version: int
    intraday: bool
    exits: list[Exit]
    config_schema: dict[str, Any]


class StrategySpace(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    ticker: str
    space: ResolvedSpace
    config_schema: dict[str, Any]


class UniverseInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership: Membership
    in_catalog: list[str]
    missing: list[str]


class RunDays(BaseModel):
    model_config = ConfigDict(frozen=True)

    days: list[date]
    recent: date | None


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    cached: bool
    metrics: Metrics


class RunDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    spec: RunSpec
    metrics: Metrics
