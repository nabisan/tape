from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar, Final, Self

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

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


TIE: Final = 1e-9


def direction_of(fast: float, slow: float) -> int:
    gap = fast - slow
    if abs(gap) <= TIE * abs(slow):
        return 0
    return 1 if gap > 0 else -1


class SmaCross(Strategy[SmaCrossConfig]):
    version: ClassVar[int] = 3
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
        now = direction_of(self.fast.value, self.slow.value)
        fast, slow = self.fast.ago(1), self.slow.ago(1)
        if math.isnan(fast) or math.isnan(slow) or now in {direction_of(fast, slow), 0}:
            return
        self.target(now)
