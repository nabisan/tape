from __future__ import annotations

import math
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import Field

from tape.core.types import Timeframe
from tape.engine.exits import Exit
from tape.engine.orders import OrderSide, OrderType
from tape.engine.strategy import Direction, Strategy, StrategyConfig

if TYPE_CHECKING:
    from tape.engine.orders import Fill
    from tape.engine.strategy import Bar


class BreakoutConfig(StrategyConfig):
    timeframe: Timeframe = Timeframe.M5
    stop_loss_pct: Annotated[
        float, Field(gt=0, lt=100, title="Stop loss %", json_schema_extra={"step": 0.05})
    ] = 0.25


class HighBreakoutConfig(BreakoutConfig):
    direction: Literal[Direction.LONG_ONLY] = Direction.LONG_ONLY


class LowBreakdownConfig(BreakoutConfig):
    direction: Literal[Direction.SHORT_ONLY] = Direction.SHORT_ONLY


class Breakout[C: BreakoutConfig](Strategy[C]):
    side: ClassVar[OrderSide]
    intraday: ClassVar[bool] = False
    exits: ClassVar[frozenset[Exit]] = frozenset({Exit.STOP_LOSS})

    extreme: float
    stopped: bool

    @property
    def stop(self) -> float:
        return self.config.stop_loss_pct / 100

    def on_start(self) -> None:
        self.extreme = math.nan
        self.stopped = False
        self.enter(self.side, tag="initial", stop_loss=self.stop)

    def on_fill(self, fill: Fill) -> None:
        if fill.side is self.side:
            self.extreme = fill.reference
        else:
            self.stopped = True

    def on_bar(self, bar: Bar) -> None:
        if self.position or self.stopped:
            long = self.side is OrderSide.BUY
            self.extreme = max(self.extreme, bar.high) if long else min(self.extreme, bar.low)
        if self.stopped:
            self.stopped = False
            self.enter(
                self.side,
                kind=OrderType.STOP_MARKET,
                price=self.extreme,
                through=True,
                tag="breakout",
                stop_loss=self.stop,
            )


class HighBreakout(Breakout[HighBreakoutConfig]):
    version: ClassVar[int] = 1
    title: ClassVar[str] = "High-Breakout"
    name: ClassVar[str] = "high_breakout"
    config_type: ClassVar[type[StrategyConfig]] = HighBreakoutConfig
    side: ClassVar[OrderSide] = OrderSide.BUY


class LowBreakdown(Breakout[LowBreakdownConfig]):
    version: ClassVar[int] = 1
    title: ClassVar[str] = "Low-Breakdown"
    name: ClassVar[str] = "low_breakdown"
    config_type: ClassVar[type[StrategyConfig]] = LowBreakdownConfig
    side: ClassVar[OrderSide] = OrderSide.SELL
