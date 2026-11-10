from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from tape.calendar import Sessions, sessions


def ns(stamp: str) -> int:
    return int(np.datetime64(stamp, "ns").astype(np.int64))


@pytest.fixture(scope="module")
def nov() -> Sessions:
    return sessions(date(2024, 11, 25), date(2024, 12, 2))


def test_holidays_and_weekends_are_not_sessions(nov: Sessions) -> None:
    assert [str(d) for d in nov.day] == ["2024-11-25", "2024-11-26", "2024-11-27", "2024-11-29", "2024-12-02"]


def test_half_day_closes_at_13(nov: Sessions) -> None:
    i = nov.index(date(2024, 11, 29))
    assert nov.open_ns[i] == ns("2024-11-29T09:30")
    assert nov.close_ns[i] == ns("2024-11-29T13:00")


def test_times_are_local_across_dst() -> None:
    s = sessions(date(2024, 3, 8), date(2024, 3, 11))
    assert s.open_ns.tolist() == [ns("2024-03-08T09:30"), ns("2024-03-11T09:30")]
    assert s.close_ns.tolist() == [ns("2024-03-08T16:00"), ns("2024-03-11T16:00")]


def test_locate_regular_hours_boundaries(nov: Sessions) -> None:
    stamps = np.array(
        [
            ns("2024-11-25T09:29"),
            ns("2024-11-25T09:30"),
            ns("2024-11-25T15:59"),
            ns("2024-11-25T16:00"),
            ns("2024-11-28T10:00"),
            ns("2024-11-29T12:59"),
            ns("2024-11-29T13:00"),
            ns("2024-11-25T04:00"),
        ],
        dtype=np.int64,
    )
    index, inside = nov.locate(stamps)
    assert inside.tolist() == [False, True, True, False, False, True, False, False]
    assert index[inside].tolist() == [0, 0, 3]


def test_index_of_non_session_raises(nov: Sessions) -> None:
    with pytest.raises(KeyError, match="not a XNYS session"):
        nov.index(date(2024, 11, 28))
