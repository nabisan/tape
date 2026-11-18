from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from typing import TYPE_CHECKING, Final

import exchange_calendars as xcals
import numpy as np
import numpy.typing as npt
import pandas as pd
from pandas.errors import Pandas4Warning

if TYPE_CHECKING:
    from datetime import date

    from tape.core.types import I32, I64, Mask

CALENDAR: Final = "XNYS"
ZONE: Final = "America/New_York"
LIBRARY: Final = f"exchange-calendars {metadata.version('exchange-calendars')}"
FX_LIBRARY: Final = "tape fx 24x5, 17:00 New York roll, closed 25 Dec and 1 Jan, v1"
FX_ROLL: Final = pd.Timedelta(hours=17)


@dataclass(frozen=True, slots=True)
class Sessions:
    day: npt.NDArray[np.datetime64]
    open_ns: I64
    close_ns: I64

    def __post_init__(self) -> None:
        assert len(self.day) == len(self.open_ns) == len(self.close_ns)
        assert bool(np.all(self.open_ns < self.close_ns))
        assert bool(np.all(self.open_ns[1:] >= self.close_ns[:-1]))

    def __len__(self) -> int:
        return len(self.day)

    def locate(self, ts_ns: I64) -> tuple[I32, Mask]:
        i = np.searchsorted(self.open_ns, ts_ns, side="right") - 1
        inside = (i >= 0) & (ts_ns < self.close_ns[np.maximum(i, 0)])
        return i.astype(np.int32), inside

    def index(self, day: date) -> int:
        i = int(np.searchsorted(self.day, np.datetime64(day, "D")))
        if i == len(self) or self.day[i] != np.datetime64(day, "D"):
            raise KeyError(f"{day} is not a session")
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


def fx_closed(day: pd.Timestamp) -> bool:
    christmas_on_sunday = day.month == 12 and day.day == 26 and day.day_of_week == 0
    return (day.month, day.day) in {(12, 25), (1, 1)} or christmas_on_sunday


def _utc_ns(local: pd.DatetimeIndex) -> I64:
    utc = local.tz_localize(ZONE).tz_convert("UTC").tz_localize(None)
    return utc.to_numpy(dtype="datetime64[ns]").view(np.int64)


@cache
def fx_sessions(start: date, end: date) -> Sessions:
    days = pd.DatetimeIndex([d for d in pd.bdate_range(start, end) if not fx_closed(d)])
    return Sessions(
        day=days.to_numpy(dtype="datetime64[D]"),
        open_ns=_utc_ns(days - pd.Timedelta(days=1) + FX_ROLL),
        close_ns=_utc_ns(days + FX_ROLL),
    )
