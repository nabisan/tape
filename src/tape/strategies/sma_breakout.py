from __future__ import annotations

import math
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import Field

from tape.core.types import Timeframe, Window
from tape.engine.exits import Exit
from tape.engine.orders import OrderSide, OrderType
from tape.engine.parameters import ParameterSpace, WindowParameter
from tape.engine.strategy import Direction, Strategy, StrategyConfig
from tape.features.kinds import FeatureKind, Sma

if TYPE_CHECKING:
    from tape.engine.orders import Fill, Order
    from tape.engine.strategy import Bar, Indicator

type Trend = Literal[Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1]


class SmaBreakoutConfig(StrategyConfig):
    timeframe: Literal[Timeframe.M5] = Timeframe.M5
    direction: Literal[Direction.LONG_ONLY] = Direction.LONG_ONLY
    trend: Annotated[Trend, Field(title="SMA timeframe")] = Timeframe.H1
    window: Annotated[Window, Field(title="SMA window")] = 200
    stop_loss_pct: Annotated[
        float, Field(gt=0, lt=100, title="Stop loss %", json_schema_extra={"step": 0.05})
    ] = 0.25


class SmaBreakout(Strategy[SmaBreakoutConfig]):
    version: ClassVar[int] = 2
    title: ClassVar[str] = "SMA Breakout"
    name: ClassVar[str] = "sma_breakout"
    config_type: ClassVar[type[StrategyConfig]] = SmaBreakoutConfig
    parameters: ClassVar[ParameterSpace] = ParameterSpace(
        windows={"window": WindowParameter(kinds=(FeatureKind.SMA,), source="trend")},
        timeframes=(Timeframe.M5,),
    )
    intraday: ClassVar[bool] = False
    exits: ClassVar[frozenset[Exit]] = frozenset({Exit.SIGNAL, Exit.STOP_LOSS})

    sma: Indicator

    @property
    def stop(self) -> float:
        return self.config.stop_loss_pct / 100

    def on_start(self) -> None:
        self.sma = self.indicator(Sma(window=self.config.window), self.config.trend)
        self.high = math.nan
        self.stopped = False
        self.blocked = True
        self.pending: Order | None = None

    def on_fill(self, fill: Fill) -> None:
        if fill.side is OrderSide.BUY:
            self.high = fill.reference
            self.pending = None
        else:
            self.stopped = True

    def on_bar(self, bar: Bar) -> None:
        if self.position or self.stopped:
            self.high = max(self.high, bar.high)
        if not self.sma.ready:
            return
        if bar.close < self.sma.value:
            self.blocked = True
            self.pending = None
            self.cancel_all()
            self.flatten(tag="sma_exit")
        elif bar.close > self.sma.value and self.blocked:
            self.blocked = False
            if not self.position and self.pending is None:
                self.stopped = False
                self.pending = self.enter(OrderSide.BUY, tag="trend", stop_loss=self.stop)
        elif self.stopped and not self.blocked and self.pending is None:
            self.stopped = False
            self.pending = self.enter(
                OrderSide.BUY,
                kind=OrderType.STOP_MARKET,
                price=self.high,
                through=True,
                tag="breakout",
                stop_loss=self.stop,
            )
