from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
import pyarrow.parquet as pq
import pytest

from tape.calendar import Sessions, sessions
from tape.catalog.bars import Bars, aggregate, regular_hours
from tape.catalog.source import read
from tape.core.types import Timeframe
from tests.conftest import SESSION_DAYS, minutes

if TYPE_CHECKING:
    from datetime import date


@pytest.fixture
def cal(span: tuple[date, date]) -> Sessions:
    return sessions(*span)


@pytest.fixture
def m1(cal: Sessions) -> Bars:
    buf = io.BytesIO()
    pq.write_table(minutes(SESSION_DAYS, seed=2), buf)
    return regular_hours(read(buf.getvalue())[0], cal)


def clock(ns: np.int64) -> str:
    return str(np.datetime64(int(ns), "ns"))[11:16]


def in_session(b: Bars, i: int) -> Bars:
    keep = b.session == i
    return Bars(*(getattr(b, f)[keep] for f in Bars.__slots__))


def test_regular_hours_only(m1: Bars, cal: Sessions) -> None:
    for i in range(len(cal)):
        b = in_session(m1, i)
        assert clock(b.time_ns[0]) >= "09:30"
        assert clock(b.time_ns[-1]) <= ("12:59" if str(cal.day[i]) == "2024-11-29" else "15:59")


def test_hour_bars_anchor_at_open_and_truncate_at_close(m1: Bars, cal: Sessions) -> None:
    h1 = aggregate(m1, cal, Timeframe.H1)
    full = in_session(h1, 0)
    half = in_session(h1, cal.index(np.datetime64("2024-11-29").item()))
    assert [clock(t) for t in full.time_ns] == ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    assert [clock(t) for t in half.time_ns] == ["09:30", "10:30", "11:30", "12:30"]
    assert full.count[-1] <= 30
    assert half.count[-1] <= 30


def test_four_hour_bars_split_at_1330(m1: Bars, cal: Sessions) -> None:
    h4 = aggregate(m1, cal, Timeframe.H4)
    full = in_session(h4, 0)
    half = in_session(h4, cal.index(np.datetime64("2024-11-29").item()))
    assert [clock(t) for t in full.time_ns] == ["09:30", "13:30"]
    assert [clock(t) for t in half.time_ns] == ["09:30"]


@pytest.mark.parametrize("tf", list(Timeframe))
def test_aggregation_conserves_minutes(m1: Bars, cal: Sessions, tf: Timeframe) -> None:
    b = aggregate(m1, cal, tf)
    assert int(b.volume.sum()) == int(m1.volume.sum())
    assert int(b.count.sum()) == len(m1)
    assert bool(np.all(np.diff(b.time_ns) > 0))
    assert bool(np.all((b.low <= b.open) & (b.low <= b.close) & (b.high >= b.open) & (b.high >= b.close)))


def test_daily_bar_is_session_extremes(m1: Bars, cal: Sessions) -> None:
    d1 = aggregate(m1, cal, Timeframe.D1)
    assert len(d1) == len(cal)
    for i in range(len(cal)):
        s = in_session(m1, i)
        assert d1.time_ns[i] == cal.open_ns[i]
        assert (d1.open[i], d1.close[i]) == (s.open[0], s.close[-1])
        assert (d1.high[i], d1.low[i]) == (s.high.max(), s.low.min())


def test_sparse_minutes_are_not_filled(m1: Bars, cal: Sessions) -> None:
    m5 = aggregate(m1, cal, Timeframe.M5)
    assert int(m5.count.max()) <= 5
    assert int(m5.count.min()) >= 1
    assert len(m1) < 390 * 4 + 210
