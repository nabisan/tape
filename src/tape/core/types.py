from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Final, NewType

import numpy as np
import numpy.typing as npt
from pydantic import Field

type I64 = npt.NDArray[np.int64]
type I32 = npt.NDArray[np.int32]
type U64 = npt.NDArray[np.uint64]
type U32 = npt.NDArray[np.uint32]
type F64 = npt.NDArray[np.float64]
type Mask = npt.NDArray[np.bool_]

NS_PER_MINUTE: Final = 60_000_000_000
NS_PER_DAY: Final = 86_400_000_000_000

Ticker = NewType("Ticker", str)
EpochNs = NewType("EpochNs", int)
SessionIndex = NewType("SessionIndex", int)
OrderId = NewType("OrderId", int)
TradeId = NewType("TradeId", int)

type Window = Annotated[int, Field(ge=2, le=5_000)]
type Quantity = Annotated[int, Field(ge=1)]
type Minutes = Annotated[int, Field(ge=0, le=390)]
type Notional = Annotated[float, Field(gt=0)]
type PerShare = Annotated[float, Field(ge=0, le=1)]
type Bps = Annotated[float, Field(ge=0, le=1_000)]
type Probability = Annotated[float, Field(ge=0, le=1)]

_TICKER: Final = re.compile(r"[A-Z][A-Z0-9]{0,5}(\.[A-Z])?")


def ticker(symbol: str) -> Ticker:
    if _TICKER.fullmatch(symbol) is None:
        raise ValueError(f"invalid ticker {symbol!r}")
    return Ticker(symbol)


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


INTRADAY_MINUTES: Final = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
}
