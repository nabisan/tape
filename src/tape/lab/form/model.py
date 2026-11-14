from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import anywidget
import traitlets
from pydantic import BaseModel, ConfigDict, Field

from tape.core.hashing import Json
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import ExecutionSpec
from tape.engine.matching import FillModel
from tape.lab.schema import capital
from tape.runner import RunSpec
from tape.sweep import SweepSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

HERE: Final = Path(__file__).parent
COMMISSION: Final = 0.005
SLIPPAGE: Final = 1.0
SQUAREOFF: Final = 5


class ExecutionChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commission: bool = False
    commission_per_share: float = Field(default=COMMISSION, ge=0)
    slippage: bool = False
    slippage_bps: float = Field(default=SLIPPAGE, ge=0)
    prob_fill_on_limit: float = Field(default=1.0, ge=0, le=1)
    intraday: bool = False
    entry_delay_minutes: int = Field(default=0, ge=0)
    squareoff_minutes_before_close: int = Field(default=SQUAREOFF, ge=1)

    def spec(self) -> ExecutionSpec:
        return ExecutionSpec(
            fill=FillModel(
                commission_per_share=self.commission_per_share if self.commission else 0.0,
                slippage_bps=self.slippage_bps if self.slippage else 0.0,
                prob_fill_on_limit=self.prob_fill_on_limit,
            ),
            intraday=self.intraday,
            entry_delay_minutes=self.entry_delay_minutes if self.intraday else 0,
            squareoff_minutes_before_close=self.squareoff_minutes_before_close,
        )

    @classmethod
    def of(cls, execution: ExecutionSpec) -> ExecutionChoice:
        fill = execution.fill
        return cls(
            commission=fill.commission_per_share > 0,
            commission_per_share=fill.commission_per_share or COMMISSION,
            slippage=fill.slippage_bps > 0,
            slippage_bps=fill.slippage_bps or SLIPPAGE,
            prob_fill_on_limit=fill.prob_fill_on_limit,
            intraday=execution.intraday,
            entry_delay_minutes=execution.entry_delay_minutes,
            squareoff_minutes_before_close=execution.squareoff_minutes_before_close,
        )


class Leg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: str
    params: dict[str, Json] = {}


class RunRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int
    ticker: Ticker
    start: date
    end: date
    legs: dict[str, Leg] = Field(min_length=1, max_length=2)
    execution: ExecutionChoice = ExecutionChoice()

    def specs(self) -> dict[str, RunSpec]:
        execution = self.execution.spec()
        return {
            name: RunSpec(
                ticker=self.ticker,
                start=self.start,
                end=self.end,
                strategy=leg.strategy,
                params=leg.params,
                execution=capital(leg.strategy, leg.params, execution),
            )
            for name, leg in self.legs.items()
        }


class SweepRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int
    tickers: tuple[Ticker, ...] = Field(min_length=1)
    start: date
    end: date
    strategy: str
    timeframe: Timeframe
    grid: dict[str, tuple[int, ...]] = {}
    params: dict[str, Json] = {}
    execution: ExecutionChoice = ExecutionChoice()

    def spec(self) -> SweepSpec:
        return SweepSpec(
            tickers=self.tickers,
            start=self.start,
            end=self.end,
            strategy=self.strategy,
            timeframe=self.timeframe,
            grid={name: tuple(values) for name, values in self.grid.items()},
            params=self.params,
            execution=capital(self.strategy, self.params, self.execution.spec()),
        )


class Seed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str | None = None
    start: str | None = None
    end: str | None = None
    compare: bool = False
    legs: dict[str, Leg] = {}
    execution: ExecutionChoice = ExecutionChoice()


def seed(specs: Mapping[str, RunSpec]) -> dict[str, Any] | None:
    if not specs:
        return None
    first = next(iter(specs.values()))
    found = Seed(
        ticker=first.ticker,
        start=first.start.isoformat(),
        end=first.end.isoformat(),
        compare=len(specs) == 2,
        legs={name: Leg(strategy=s.strategy, params=s.params) for name, s in specs.items()},
        execution=ExecutionChoice.of(first.execution),
    )
    return found.model_dump(mode="json")


class Form(anywidget.AnyWidget):
    _esm = HERE / "form.js"
    _css = HERE / "form.css"

    page = traitlets.Unicode("run").tag(sync=True)
    pages = traitlets.Dict().tag(sync=True)
    api = traitlets.Unicode("").tag(sync=True)
    seed = traitlets.Dict(allow_none=True, default_value=None).tag(sync=True)
    submission = traitlets.Dict(allow_none=True, default_value=None).tag(sync=True)
    message = traitlets.Unicode("").tag(sync=True)
    busy = traitlets.Bool(False).tag(sync=True)
