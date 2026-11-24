from __future__ import annotations

import asyncio
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow.parquet as pq
import pytest

from tape import portfolio_index
from tape.catalog.catalogs import Catalogs
from tape.core.hashing import Digest
from tape.core.types import Ticker
from tape.lab import portfolios as view
from tape.portfolio import Portfolio, cached
from tape.service import portfolios as service_portfolios
from tape.service.results import ResultStore
from tests.test_sweep import client, root, sma, wait

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from tape.catalog.store import Store

__all__ = ["client", "root"]

type Built = tuple[ResultStore, pd.DataFrame, list[str]]


def run(client: TestClient, **kw: object) -> str:
    started = client.post("/sweeps", json=sma(**kw).model_dump(mode="json")).json()
    assert wait(client, started["key"])["state"] == "done"
    return str(started["key"])


@pytest.fixture(scope="module")
def built(root: Path, client: TestClient) -> Built:
    keys = [run(client), run(client, strategy="high_breakout", tickers=["AAPL", "SPY"])]
    results = ResultStore(root / "results")
    portfolio_index.build(results, Catalogs.open(root / "store"))
    return results, portfolio_index.read(results).to_pandas(), keys


def test_one_row_per_book_equal_to_the_book_portfolio(
    root: Path, built: Built
) -> None:
    results, index, (sma_key, _) = built
    summary = pd.read_parquet(results.sweep_summary(Digest(sma_key)))
    rows = index[index["sweep"] == sma_key]
    assert len(rows) == len(summary[["fast", "slow"]].drop_duplicates())
    store = Catalogs.open(root / "store").common([Ticker("AAPL"), Ticker("SPY")])
    for _, row in rows.iterrows():
        folio = cached(results, Digest(sma_key), str(row["run"]), store, None)
        book = folio.lines[0]
        assert row["net_pnl"] == pytest.approx(book.net_pnl)
        assert row["sharpe"] == pytest.approx(book.sharpe, nan_ok=True)
        assert row["avg_capital_used"] == pytest.approx(folio.capital.avg_used)
        assert row["trades"] == folio.trades and row["tickers"] == 2
        params = json.loads(row["parameters_json"])
        assert row["parameters"] == f"fast {params['fast']} \u00b7 slow {params['slow']}"


def test_index_is_stale_until_rebuilt_and_reuses_rows(
    root: Path, client: TestClient, built: Built
) -> None:
    results, _, _ = built
    portfolio_index.build(results, Catalogs.open(root / "store"))
    assert not portfolio_index.stale(results)
    again = portfolio_index.build(results, Catalogs.open(root / "store"))
    assert again.added == 0
    run(client, end="2024-05-31")
    assert portfolio_index.stale(results)
    after = portfolio_index.build(results, Catalogs.open(root / "store"))
    assert after.added > 0 and not portfolio_index.stale(results)


def test_api_serves_the_table_and_its_state(
    client: TestClient, built: Built
) -> None:
    for _ in range(100):
        state = client.get("/portfolio-index/status").json()
        if not state["building"]:
            break
        time.sleep(0.1)
    table = pq.read_table(io.BytesIO(client.get("/portfolio-index").content))
    assert table.schema.remove_metadata().equals(portfolio_index.SCHEMA)
    assert table.num_rows >= len(built[1])


def test_parameters_get_columns_only_for_one_strategy(built: Built) -> None:
    frame = view.prepare(built[1])
    both, _ = view.shown(frame, view.DEFAULT_GROUPS)
    assert "Parameters" in both.columns and "Fast" not in both.columns
    sma_only = view.narrow(frame, view.Criteria(strategies=["SMA cross"]))
    one, _ = view.shown(sma_only, view.DEFAULT_GROUPS)
    assert {"Fast", "Slow"} <= set(one.columns)
    assert list(one.columns).index("Fast") == list(one.columns).index("Parameters") + 1


def test_filters_and_hidden_outdated(built: Built) -> None:
    frame = view.prepare(built[1])
    frame.loc[0, "outdated"] = True
    assert len(view.narrow(frame, view.Criteria())) == len(frame) - 1
    assert len(view.narrow(frame, view.Criteria(outdated=True))) == len(frame)
    found = view.narrow(frame, view.Criteria(search="fast", outdated=True))
    assert set(found["strategy_title"]) == {"SMA cross"}


def test_capital_by_default_and_execution_only_when_picked(
    built: Built
) -> None:
    frame = view.prepare(built[1])
    short, _ = view.shown(frame, view.DEFAULT_GROUPS)
    assert "Capital used avg" in short.columns and "Fills on" not in short.columns
    wide, formats = view.shown(frame, list(view.Group))
    assert {"Capital used avg", "Fills on", "Simulation"} <= set(wide.columns)
    assert "Capital used avg" in formats


def test_open_link_pins_the_book(built: Built) -> None:
    frame = view.prepare(built[1])
    row = frame[frame["strategy"] == "sma_cross"].iloc[0]
    found = view.open_link(row)
    assert f"sweep={row['sweep']}" in found and "fast=" in found and "slow=" in found


def test_one_failing_book_is_skipped_and_the_rest_are_built(
    root: Path, built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, _, (sma_key, _) = built
    portfolio_index.build(results, Catalogs.open(root / "store"))
    clean = portfolio_index.read(results).to_pandas()
    bad = str(clean[clean["sweep"] == sma_key]["run"].iloc[0])
    portfolio_index.write(results, [])
    def flaky(store: ResultStore, sweep: Digest, run: str, at: Store, market: Store | None) -> Portfolio:
        if run == bad:
            raise IndexError("index 0 is out of bounds")
        return cached(store, sweep, run, at, market)

    monkeypatch.setattr("tape.portfolio_index.cached", flaky)
    status = portfolio_index.build(results, Catalogs.open(root / "store"))
    assert status.skipped[f"{sma_key}:{bad}"] == "IndexError: index 0 is out of bounds"
    rebuilt = portfolio_index.read(results).to_pandas()
    assert bad not in set(rebuilt["run"]) and len(rebuilt) == len(clean) - 1


def test_a_failed_build_is_not_retried_until_a_simulation_is_added(
    built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, _, _ = built
    calls: list[int] = []

    def failing() -> int:
        calls.append(1)
        raise RuntimeError("disk full")

    monkeypatch.setattr(service_portfolios, "build_portfolios", failing)
    monkeypatch.setattr(portfolio_index, "stale", lambda _: True)

    async def scenario() -> None:
        with ThreadPoolExecutor(1) as pool:
            indexer = service_portfolios.Indexer(results, lambda: pool)
            indexer.kick()
            while indexer.building:
                await asyncio.sleep(0.01)
            indexer.kick()
            assert not indexer.building

    asyncio.run(scenario())
    assert calls == [1]


def test_only_the_most_recent_simulations_are_indexed(
    root: Path, built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, _, _ = built
    monkeypatch.setattr("tape.portfolio_index.RECENT", 1)
    newest = str(results.stored_sweeps()[0][0])
    status = portfolio_index.build(results, Catalogs.open(root / "store"))
    assert status.sweeps == [newest]
    assert set(portfolio_index.read(results).to_pandas()["sweep"]) == {newest}
    assert not portfolio_index.stale(results)
