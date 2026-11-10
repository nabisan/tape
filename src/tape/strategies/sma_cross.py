from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import model_validator

from tape.core.types import Window
from tape.engine.parameters import Ordered, ParameterSpace, WindowParameter
from tape.engine.strategy import Indicator, Strategy, StrategyConfig
from tape.features.kinds import FeatureKind, Sma

if TYPE_CHECKING:
    from tape.engine.strategy import Bar


class SmaCrossConfig(StrategyConfig):
    fast: Window = 20
    slow: Window = 50
    require_cross: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


class SmaCross(Strategy[SmaCrossConfig]):
    title: ClassVar[str] = "SMA cross"
    name: ClassVar[str] = "sma_cross"
    config_type: ClassVar[type[StrategyConfig]] = SmaCrossConfig
    parameters: ClassVar[ParameterSpace] = ParameterSpace(
        windows={
            "fast": WindowParameter(kinds=(FeatureKind.SMA,)),
            "slow": WindowParameter(kinds=(FeatureKind.SMA,)),
        },
        ordered=(Ordered(lower="fast", upper="slow"),),
    )

    fast: Indicator
    slow: Indicator

    def on_start(self) -> None:
        self.fast = self.indicator(Sma(window=self.config.fast))
        self.slow = self.indicator(Sma(window=self.config.slow))

    def on_bar(self, bar: Bar) -> None:
        if not self.slow.ready:
            return
        side = (self.fast.value > self.slow.value) - (self.fast.value < self.slow.value)
        if self.config.require_cross:
            before = (self.fast.ago(1) > self.slow.ago(1)) - (self.fast.ago(1) < self.slow.ago(1))
            if side in {before, 0}:
                return
        self.target(side, bar.close)
