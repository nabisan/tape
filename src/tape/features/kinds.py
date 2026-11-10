from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, NewType

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tape.core.hashing import digest
from tape.core.types import Window
from tape.features import ta

if TYPE_CHECKING:
    from tape.catalog.bars import Bars
    from tape.core.types import F64


FeatureId = NewType("FeatureId", str)


class FeatureKind(StrEnum):
    SMA = "sma"
    EMA = "ema"
    CHANNEL_HIGH = "channel_high"
    CHANNEL_LOW = "channel_low"
    ATR = "atr"


class Source(StrEnum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


def _series(bars: Bars, source: Source) -> F64:
    match source:
        case Source.OPEN:
            return bars.open
        case Source.HIGH:
            return bars.high
        case Source.LOW:
            return bars.low
        case Source.CLOSE:
            return bars.close
        case Source.VOLUME:
            return bars.volume.astype(np.float64)


def _previous(x: F64) -> F64:
    out = np.empty_like(x)
    out[0] = np.nan
    out[1:] = x[:-1]
    return out


class _Kind(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: ClassVar[int] = 1

    @property
    def id(self) -> FeatureId:
        return FeatureId(digest({"spec": self.model_dump(mode="json"), "version": self.version}))

    def warmup(self) -> int:
        raise NotImplementedError

    def compute(self, bars: Bars) -> F64:
        raise NotImplementedError


class Sma(_Kind):
    kind: Literal[FeatureKind.SMA] = FeatureKind.SMA
    window: Window
    source: Source = Source.CLOSE

    @property
    def name(self) -> str:
        return f"sma_{self.window}" if self.source is Source.CLOSE else f"sma_{self.source}_{self.window}"

    def warmup(self) -> int:
        return self.window - 1

    def compute(self, bars: Bars) -> F64:
        return ta.sma(_series(bars, self.source), self.window)


class Ema(_Kind):
    kind: Literal[FeatureKind.EMA] = FeatureKind.EMA
    window: Window
    source: Source = Source.CLOSE

    @property
    def name(self) -> str:
        return f"ema_{self.window}" if self.source is Source.CLOSE else f"ema_{self.source}_{self.window}"

    def warmup(self) -> int:
        return self.window - 1

    def compute(self, bars: Bars) -> F64:
        return ta.ema(_series(bars, self.source), self.window)


class ChannelHigh(_Kind):
    kind: Literal[FeatureKind.CHANNEL_HIGH] = FeatureKind.CHANNEL_HIGH
    window: Window

    @property
    def name(self) -> str:
        return f"chan_high_{self.window}"

    def warmup(self) -> int:
        return self.window

    def compute(self, bars: Bars) -> F64:
        return _previous(ta.rolling_max(bars.high, self.window) if self.window > 1 else bars.high.copy())


class ChannelLow(_Kind):
    kind: Literal[FeatureKind.CHANNEL_LOW] = FeatureKind.CHANNEL_LOW
    window: Window

    @property
    def name(self) -> str:
        return f"chan_low_{self.window}"

    def warmup(self) -> int:
        return self.window

    def compute(self, bars: Bars) -> F64:
        return _previous(ta.rolling_min(bars.low, self.window) if self.window > 1 else bars.low.copy())


class Atr(_Kind):
    kind: Literal[FeatureKind.ATR] = FeatureKind.ATR
    window: Window

    @property
    def name(self) -> str:
        return f"atr_{self.window}"

    def warmup(self) -> int:
        return self.window

    def compute(self, bars: Bars) -> F64:
        return ta.atr(bars.high, bars.low, bars.close, self.window)


type FeatureSpec = Annotated[Sma | Ema | ChannelHigh | ChannelLow | Atr, Field(discriminator="kind")]

SPEC_TYPES: Final[dict[FeatureKind, type[Sma | Ema | ChannelHigh | ChannelLow | Atr]]] = {
    FeatureKind.SMA: Sma,
    FeatureKind.EMA: Ema,
    FeatureKind.CHANNEL_HIGH: ChannelHigh,
    FeatureKind.CHANNEL_LOW: ChannelLow,
    FeatureKind.ATR: Atr,
}


def spec_for(kind: FeatureKind, window: int) -> FeatureSpec:
    return SPEC_TYPES[kind](window=window)
