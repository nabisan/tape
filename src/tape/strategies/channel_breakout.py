from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field, model_validator

from tape.engine.orders import OrderSide, OrderType
from tape.engine.strategy import Indicator, Strategy, StrategyConfig
from tape.features.kinds import ChannelHigh, ChannelLow

if TYPE_CHECKING:
    from tape.engine.orders import Fill
    from tape.engine.strategy import Bar


class Trigger(StrEnum):
    STOP = "stop"
    CLOSE = "close"


class ChannelBreakoutConfig(StrategyConfig):
    entry_window: int = Field(default=20, ge=2, le=1_000)
    exit_window: int = Field(default=10, ge=1, le=1_000)
    trigger: Trigger = Trigger.STOP

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.exit_window > self.entry_window:
            raise ValueError("exit_window must not exceed entry_window")
        return self


class ChannelBreakout(Strategy[ChannelBreakoutConfig]):
    name: ClassVar[str] = "channel_breakout"
    config_type: ClassVar[type[StrategyConfig]] = ChannelBreakoutConfig

    entry_high: Indicator
    entry_low: Indicator
    exit_high: Indicator
    exit_low: Indicator

    def on_start(self) -> None:
        c = self.config
        self.entry_high = self.indicator(ChannelHigh(window=c.entry_window))
        self.entry_low = self.indicator(ChannelLow(window=c.entry_window))
        self.exit_high = self.indicator(ChannelHigh(window=c.exit_window))
        self.exit_low = self.indicator(ChannelLow(window=c.exit_window))

    def on_bar(self, bar: Bar) -> None:
        if not self.entry_high.ready:
            return
        if self.config.trigger is Trigger.CLOSE:
            self._on_close(bar)
        else:
            self._rest_stops()

    def _on_close(self, bar: Bar) -> None:
        held = self.position
        if (held > 0 and bar.close < self.exit_low.value) or (held < 0 and bar.close > self.exit_high.value):
            self.flatten("exit")
        elif held == 0 and bar.close > self.entry_high.value:
            self.target(1, bar.close, "breakout")
        elif held == 0 and bar.close < self.entry_low.value:
            self.target(-1, bar.close, "breakout")

    def _rest_stops(self) -> None:
        self.cancel_all()
        held = self.position
        direction = self.config.direction
        if held > 0:
            self.submit(
                OrderSide.SELL, held, kind=OrderType.STOP_MARKET, price=self.exit_low.value, tag="exit"
            )
        elif held < 0:
            self.submit(
                OrderSide.BUY, -held, kind=OrderType.STOP_MARKET, price=self.exit_high.value, tag="exit"
            )
        else:
            if direction.allows(1):
                level = self.entry_high.value
                self.submit(
                    OrderSide.BUY, self.size(level), kind=OrderType.STOP_MARKET, price=level, tag="breakout"
                )
            if direction.allows(-1):
                level = self.entry_low.value
                self.submit(
                    OrderSide.SELL, self.size(level), kind=OrderType.STOP_MARKET, price=level, tag="breakout"
                )

    def on_fill(self, fill: Fill) -> None:
        if self.config.trigger is Trigger.STOP and self.position:
            self.cancel_all()
