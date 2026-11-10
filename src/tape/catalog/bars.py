from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

from tape.core.types import INTRADAY_MINUTES, NS_PER_MINUTE, Timeframe

if TYPE_CHECKING:
    from tape.calendar import Sessions
    from tape.catalog.source import Minutes
    from tape.core.types import F64, I32, I64, U32, U64


DTYPES: Final = tuple(
    np.dtype(d)
    for d in (np.int64, np.int32, np.float64, np.float64, np.float64, np.float64, np.uint64, np.uint32)
)


@dataclass(frozen=True, slots=True)
class Bars:
    time_ns: I64
    session: I32
    open: F64
    high: F64
    low: F64
    close: F64
    volume: U64
    count: U32

    def __post_init__(self) -> None:
        columns = (
            self.time_ns,
            self.session,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.count,
        )
        assert all(len(a) == len(self.time_ns) for a in columns)
        assert [a.dtype for a in columns] == list(DTYPES)

    def __len__(self) -> int:
        return len(self.time_ns)


def regular_hours(minutes: Minutes, sessions: Sessions) -> Bars:
    session, inside = sessions.locate(minutes.time_ns)
    return Bars(
        time_ns=minutes.time_ns[inside],
        session=session[inside],
        open=minutes.open[inside],
        high=minutes.high[inside],
        low=minutes.low[inside],
        close=minutes.close[inside],
        volume=minutes.volume[inside],
        count=np.ones(int(inside.sum()), dtype=np.uint32),
    )


def labels(m1: Bars, sessions: Sessions, timeframe: Timeframe) -> I64:
    if timeframe is Timeframe.M1:
        return m1.time_ns
    session_open = sessions.open_ns[m1.session]
    if timeframe is Timeframe.D1:
        return session_open
    width = INTRADAY_MINUTES[timeframe] * NS_PER_MINUTE
    return session_open + (m1.time_ns - session_open) // width * width


def last_minutes(m1: Bars, sessions: Sessions, timeframe: Timeframe) -> I64:
    label = labels(m1, sessions, timeframe)
    return np.flatnonzero(np.r_[label[1:] != label[:-1], True])


def aggregate(m1: Bars, sessions: Sessions, timeframe: Timeframe) -> Bars:
    if timeframe is Timeframe.M1 or not len(m1):
        return m1
    label = labels(m1, sessions, timeframe)
    start = np.flatnonzero(np.r_[True, label[1:] != label[:-1]])
    end = np.r_[start[1:], len(label)] - 1
    return Bars(
        time_ns=label[start],
        session=m1.session[start],
        open=m1.open[start],
        high=np.maximum.reduceat(m1.high, start),
        low=np.minimum.reduceat(m1.low, start),
        close=m1.close[end],
        volume=np.add.reduceat(m1.volume, start, dtype=np.uint64),
        count=np.add.reduceat(m1.count, start, dtype=np.uint32),
    )
