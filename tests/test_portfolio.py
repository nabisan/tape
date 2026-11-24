from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

from tape.capital import combined_max, held_before
from tape.catalog.catalogs import Catalogs
from tape.catalog.store import Store
from tape.core.hashing import Digest
from tape.core.instruments import Conversion
from tape.core.types import Ticker
from tape.portfolio import Portfolio, benchmark_lines, hold_growth, portfolio
from tape.service.results import ResultStore, Table
from tape.sheets import books
from tests.test_sweep import client, root, sma, wait

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from fastapi.testclient import TestClient

__all__ = ["client", "root"]


@pytest.fixture(scope="module")
def sweep(client: TestClient) -> tuple[str, pd.DataFrame]:
    started = client.post("/sweeps", json=sma().model_dump(mode="json")).json()
    assert wait(client, started["key"])["state"] == "done"
    summary = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{started['key']}/summary").content))
    return started["key"], summary


def fetch(client: TestClient, key: str, run: str) -> Portfolio:
    response = client.get(f"/sweeps/{key}/portfolio", params={"run": run})
    assert response.status_code == 200, response.text
    return Portfolio.model_validate(response.json())


def test_book_hold_and_benchmark_lines(client: TestClient, sweep: tuple[str, pd.DataFrame]) -> None:
    key, summary = sweep
    run = str(summary["key"].iloc[0])
    folio = fetch(client, key, run)
    assert [(line.label, line.kind) for line in folio.lines] == [("Book", "book"), ("Hold", "hold"),
                                                                 ("SPY", "benchmark")]
    first = summary.iloc[0]
    same = summary[(summary["fast"] == first["fast"]) & (summary["slow"] == first["slow"])]
    cash = 10_000.0 * len(same)
    book, held = folio.lines[0], folio.lines[1]
    assert folio.tickers == len(same) and folio.trades == int(same["trades"].sum())
    assert math.isclose(book.net_pnl, float(same["final_equity"].sum()) - cash, rel_tol=1e-9)
    want_hold = float((10_000.0 * (1 + same["buy_hold_return_pct"] / 100)).sum())
    assert math.isclose(held.total_return_pct, 100 * (want_hold / cash - 1), rel_tol=1e-9, abs_tol=1e-9)
    assert math.isnan(book.exposure.beta)


def test_capital_use_matches_the_workbook(
    root: Path, client: TestClient, sweep: tuple[str, pd.DataFrame]
) -> None:
    key, _ = sweep
    results = ResultStore(root / "results")
    sheet = books(Catalogs.open(root / "store"), results, [Digest(key)])
    for b in sheet:
        run = b.pairs[0].key
        folio = fetch(client, key, run)
        assert math.isclose(folio.capital.avg_used, sum(p.avg_used for p in b.pairs), rel_tol=1e-9)
        assert math.isclose(folio.capital.avg_overnight, sum(p.avg_overnight for p in b.pairs), rel_tol=1e-9)
        assert math.isclose(folio.capital.max_used, b.max_used, rel_tol=1e-9)
        assert math.isclose(folio.capital.max_capital_loss_pct, b.max_capital_loss, rel_tol=1e-9)
        assert math.isclose(folio.lines[0].volatility_pct, b.volatility, rel_tol=1e-9)


def test_result_is_cached_per_book(root: Path, client: TestClient, sweep: tuple[str, pd.DataFrame]) -> None:
    key, summary = sweep
    run = str(summary["key"].iloc[0])
    first, second = fetch(client, key, run), fetch(client, key, run)
    assert first == second
    cached = list((root / "results" / "sweeps" / f"{key}.portfolio").glob("*.json"))
    assert first.book in {p.stem for p in cached}
    books_seen = {fetch(client, key, str(k)).book for k in summary["key"]}
    assert len(books_seen) == summary[["fast", "slow"]].drop_duplicates().shape[0]


def test_unknown_sweep_and_foreign_run(client: TestClient, sweep: tuple[str, pd.DataFrame]) -> None:
    key, _ = sweep
    assert client.get("/sweeps/" + "0" * 32 + "/portfolio", params={"run": "x"}).status_code == 404
    assert client.get(f"/sweeps/{key}/portfolio", params={"run": "f" * 32}).status_code == 422


@pytest.mark.parametrize("conversion", list(Conversion))
def test_hold_growth_matches_the_instrument_conversion(conversion: Conversion) -> None:
    marks = np.array([1.1, 1.2, 0.9, 150.0])
    want = np.array([1 + conversion.growth(1.25, m) for m in marks])
    assert np.allclose(hold_growth(conversion, 1.25, marks), want)


def test_benchmark_that_ends_before_the_book_says_so(root: Path) -> None:
    market = Store.open(root / "store")
    sessions = pd.to_datetime(market.sessions().day)
    days = pd.DatetimeIndex([*sessions[-30:], *(sessions[-1] + pd.Timedelta(days=d) for d in range(1, 8))])
    [spy] = benchmark_lines(market, days, np.full(len(days), math.nan), 50_000.0)
    assert spy.label == "SPY" and spy.ends == sessions[-1].date()
    [within] = benchmark_lines(market, days[:30], np.full(30, math.nan), 50_000.0)
    assert within.ends is None


def test_held_before_with_no_fills_is_flat_zero() -> None:
    empty_times, empty_cost = np.zeros(0, np.int64), np.zeros(0, np.float64)
    assert held_before(empty_times, empty_cost, np.array([1, 2, 3], np.int64)).tolist() == [0.0, 0.0, 0.0]


def test_combined_max_of_runs_without_bars_is_zero() -> None:
    assert combined_max({}) == 0.0
    assert combined_max({Ticker("AAA"): pd.Series(dtype=float)}) == 0.0


def test_a_run_with_no_fills_counts_as_never_holding(
    root: Path, sweep: tuple[str, pd.DataFrame], monkeypatch: pytest.MonkeyPatch
) -> None:
    key, summary = sweep
    results = ResultStore(root / "results")
    silent = str(summary["key"].iloc[0])
    real = ResultStore.frame

    def frame(
        self: ResultStore, run: Digest, table: Table, start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        found = real(self, run, table, start, end)
        return found.iloc[0:0] if run == silent and table is Table.FILLS else found

    monkeypatch.setattr(ResultStore, "frame", frame)
    store = Catalogs.open(root / "store").common([Ticker("AAPL"), Ticker("SPY")])
    folio = portfolio(results, Digest(key), silent, store, None)
    assert math.isfinite(folio.capital.avg_used) and folio.capital.avg_used >= 0.0
    assert math.isfinite(folio.capital.max_used)
