from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from typing import TYPE_CHECKING, Final

import exchange_calendars as xcals
import numpy as np
import numpy.typing as npt
from pandas.errors import Pandas4Warning

if TYPE_CHECKING:
    from datetime import date

    import pandas as pd

    from tape.core.types import I32, I64, Mask

CALENDAR: Final = "XNYS"
ZONE: Final = "America/New_York"
LIBRARY: Final = f"exchange-calendars {metadata.version('exchange-calendars')}"


@dataclass(frozen=True, slots=True)
class Sessions:
    day: npt.NDArray[np.datetime64]
    open_ns: I64
    close_ns: I64

    def __post_init__(self) -> None:
        assert len(self.day) == len(self.open_ns) == len(self.close_ns)
        assert bool(np.all(self.open_ns < self.close_ns))
        assert bool(np.all(self.open_ns[1:] > self.close_ns[:-1]))

    def __len__(self) -> int:
        return len(self.day)

    def locate(self, ts_ns: I64) -> tuple[I32, Mask]:
        i = np.searchsorted(self.open_ns, ts_ns, side="right") - 1
        inside = (i >= 0) & (ts_ns < self.close_ns[np.maximum(i, 0)])
        return i.astype(np.int32), inside

    def index(self, day: date) -> int:
        i = int(np.searchsorted(self.day, np.datetime64(day, "D")))
        if i == len(self) or self.day[i] != np.datetime64(day, "D"):
            raise KeyError(f"{day} is not a {CALENDAR} session")
        return i


def _local_ns(column: pd.Series[pd.Timestamp]) -> I64:
    local = column.dt.tz_convert(ZONE).dt.tz_localize(None)
    return local.to_numpy(dtype="datetime64[ns]").view(np.int64)


@cache
def sessions(start: date, end: date) -> Sessions:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Series.values returning an ndarray", Pandas4Warning)
        schedule = xcals.get_calendar(CALENDAR, start=start.isoformat(), end=end.isoformat()).schedule
    schedule = schedule.loc[start.isoformat() : end.isoformat()]
    return Sessions(
        day=schedule.index.to_numpy(dtype="datetime64[D]"),
        open_ns=_local_ns(schedule["open"]),
        close_ns=_local_ns(schedule["close"]),
    )
