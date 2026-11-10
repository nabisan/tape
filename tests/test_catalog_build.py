from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pyarrow.parquet as pq
import pytest

from tape.catalog.build import CatalogError, build
from tape.catalog.store import LAYOUT, Status, Store, bar_path
from tape.core.types import Ticker, Timeframe
from tests.conftest import SESSION_DAYS, minutes

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import WriteSource

SPY, AAPL, BAD = Ticker("SPY"), Ticker("AAPL"), Ticker("BAD")


@pytest.fixture
def sources(write_source: WriteSource) -> None:
    write_source("SPY", seed=1)
    write_source("AAPL", days=[d for d in SESSION_DAYS if d != "2024-11-26"], seed=2)
    table = minutes(SESSION_DAYS, seed=3)
    write_source("BAD", table=table.take(np.r_[np.arange(1, table.num_rows), 0]))


@pytest.mark.usefixtures("sources")
def test_build_reports_every_ticker(source_dir: Path, tmp_path: Path) -> None:
    m = build(source_dir, tmp_path / "store")
    assert m.available == [AAPL, SPY]
    assert (m.first_session, m.last_session, m.sessions) == (date(2024, 11, 25), date(2024, 12, 2), 5)
    assert m.tickers[BAD].status is Status.REJECTED
    assert "not strictly increasing" in (m.tickers[BAD].reason or "")
    assert m.tickers[AAPL].sessions_missing == (date(2024, 11, 26),)
    assert m.tickers[SPY].sessions_missing == ()
    assert m.tickers[SPY].bars[Timeframe.D1] == 5


@pytest.mark.usefixtures("sources")
def test_store_reads_back_what_was_built(source_dir: Path, tmp_path: Path) -> None:
    m = build(source_dir, tmp_path / "store")
    store = Store.open(tmp_path / "store")
    assert store.manifest == m
    assert len(store.sessions()) == 5
    d1 = store.bars(SPY, Timeframe.D1)
    assert d1.time_ns.dtype == np.int64
    assert d1.session.tolist() == [0, 1, 2, 3, 4]
    with pytest.raises(KeyError, match="BAD not in catalog"):
        store.bars(BAD, Timeframe.D1)


@pytest.mark.usefixtures("sources")
def test_build_is_deterministic_and_idempotent(source_dir: Path, tmp_path: Path) -> None:
    a = build(source_dir, tmp_path / "a", workers=2)
    b = build(source_dir, tmp_path / "b")
    assert a.data_version == b.data_version
    for tf in Timeframe:
        ra, rb = tmp_path / "a" / LAYOUT / a.data_version, tmp_path / "b" / LAYOUT / b.data_version
        assert bar_path(ra, SPY, tf).read_bytes() == bar_path(rb, SPY, tf).read_bytes()
    assert build(source_dir, tmp_path / "a") == a


@pytest.mark.usefixtures("sources")
def test_changed_source_is_a_new_data_version(
    source_dir: Path, tmp_path: Path, write_source: WriteSource
) -> None:
    a = build(source_dir, tmp_path / "store")
    write_source("AAPL", seed=9)
    b = build(source_dir, tmp_path / "store")
    assert a.data_version != b.data_version
    assert Store.open(tmp_path / "store", a.data_version).manifest == a


def test_reference_gap_fails_the_build(source_dir: Path, tmp_path: Path, write_source: WriteSource) -> None:
    write_source("SPY", days=[d for d in SESSION_DAYS if d != "2024-11-27"], seed=1)
    write_source("AAPL", seed=2)
    with pytest.raises(CatalogError, match="no bars in 1 calendar sessions: 2024-11-27"):
        build(source_dir, tmp_path / "store")
    assert not any((tmp_path / "store" / LAYOUT).iterdir())


def test_reference_trading_on_holiday_fails(
    source_dir: Path, tmp_path: Path, write_source: WriteSource
) -> None:
    write_source("SPY", days=[*SESSION_DAYS[:3], "2024-11-28", *SESSION_DAYS[3:]], seed=1)
    with pytest.raises(CatalogError, match="trades on 1 non-sessions: 2024-11-28"):
        build(source_dir, tmp_path / "store")


def test_missing_reference_fails(source_dir: Path, tmp_path: Path, write_source: WriteSource) -> None:
    write_source("AAPL", seed=2)
    with pytest.raises(CatalogError, match="reference SPY missing"):
        build(source_dir, tmp_path / "store")


@pytest.mark.usefixtures("sources")
def test_ticker_subset_keeps_reference(source_dir: Path, tmp_path: Path) -> None:
    m = build(source_dir, tmp_path / "store", tickers=[AAPL])
    assert sorted(m.tickers) == [AAPL, SPY]
    assert (
        pq.read_metadata(bar_path(tmp_path / "store" / LAYOUT / m.data_version, AAPL, Timeframe.M1)).num_rows
        > 0
    )


def test_empty_source_dir_says_so(source_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no parquet files in"):
        build(source_dir, tmp_path / "store")
