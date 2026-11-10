from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from tape.core.hashing import digest
from tape.core.types import Timeframe
from tape.features.kinds import Atr, ChannelHigh, ChannelLow, Ema, FeatureSpec, Sma, Source

if TYPE_CHECKING:
    from tape.core.hashing import Digest
    from tape.features.kinds import FeatureId


class FeatureGrid(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframes: tuple[Timeframe, ...]
    specs: tuple[FeatureSpec, ...]
    on_demand: tuple[Timeframe, ...] = ()

    @property
    def id(self) -> Digest:
        return digest(self)

    def covers(self, timeframe: Timeframe) -> bool:
        return timeframe in self.timeframes or timeframe in self.on_demand

    def ids(self, timeframe: Timeframe) -> set[FeatureId]:
        return {s.id for s in self.specs} if self.covers(timeframe) else set()


MOVING: Final = (5, 10, 20, 50, 100, 150, 200)
CHANNELS: Final = (10, 20, 55)

DEFAULT_GRID: Final = FeatureGrid(
    timeframes=(Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1),
    specs=(
        *(Sma(window=n) for n in MOVING),
        *(Ema(window=n) for n in MOVING),
        *(ChannelHigh(window=n) for n in CHANNELS),
        *(ChannelLow(window=n) for n in CHANNELS),
        Atr(window=14),
        Sma(window=20, source=Source.VOLUME),
    ),
    on_demand=(Timeframe.M1,),
)
