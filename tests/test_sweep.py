from __future__ import annotations

import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from tape.catalog.store import Store
from tape.config import Settings
from tape.core.hashing import Digest
from tape.core.types import Ticker, Timeframe
from tape.features.build import build
from tape.features.grid import FeatureGrid
from tape.features.kinds import ChannelHigh, ChannelLow, Sma
from tape.runner import FeaturePolicy
from tape.service.api import create_app
from tape.service.results import ResultStore
from tape.service.sweeps import SweepManager, SweepStatus
from tape.sweep import SUMMARY, SweepSpec, expand
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GRID = FeatureGrid(
    timeframes=(Timeframe.H1, Timeframe.D1),
    specs=(Sma(window=5), Sma(window=10), Sma(window=20), ChannelHigh(window=10), ChannelLow(window=10)),
)
AAPL, SPY = Ticker("AAPL"), Ticker("SPY")


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("sweep")
    make_store(path)
    build(path / "store", GRID)
    return path


def sma(**kw: object) -> SweepSpec:
    base: dict[str, object] = {
        "tickers": ["AAPL", "SPY"],
        "start": date(2024, 2, 1),
        "end": date(2024, 6, 28),
        "strategy": "sma_cross",
        "timeframe": "1h",
    }
    return SweepSpec.model_validate({**base, **kw})


def test_expansion_uses_catalog_domains_and_constraints(root: Path) -> None:
    plan = expand(sma(), Store.open(root / "store"), GRID)
    pairs = sorted({(r.params["fast"], r.params["slow"]) for r in plan.runs})
    assert pairs == [(5, 10), (5, 20), (10, 20)]
    assert len(plan.runs) == 6
    assert plan.skipped == ()


def test_explicit_grid_is_intersected_with_the_catalog(root: Path) -> None:
    plan = expand(sma(grid={"fast": [5, 7], "slow": [20, 200]}), Store.open(root / "store"), GRID)
    assert sorted({(r.params["fast"], r.params["slow"]) for r in plan.runs}) == [(5, 20)]


def test_tickers_without_features_are_skipped(root: Path) -> None:
    plan = expand(sma(timeframe="4h"), Store.open(root / "store"), GRID)
    assert plan.runs == ()
    assert {s.ticker for s in plan.skipped} == {AAPL, SPY}


def test_compute_policy_takes_the_grid_as_given(root: Path) -> None:
    spec = sma(
        timeframe="4h", tickers=["AAPL"], grid={"fast": [3], "slow": [8]}, features=FeaturePolicy.COMPUTE
    )
    plan = expand(spec, Store.open(root / "store"), GRID)
    assert [(r.params["fast"], r.params["slow"]) for r in plan.runs] == [(3, 8)]


@pytest.fixture(scope="module")
def client(root: Path) -> Iterator[TestClient]:
    settings = Settings(source_dir=root / "source", store_dir=root / "store", results_dir=root / "results")
    with TestClient(create_app(settings, workers=1, grid=GRID)) as c:
        yield c


def wait(client: TestClient, key: str) -> dict[str, object]:
    for _ in range(300):
        status: dict[str, object] = client.get(f"/sweeps/{key}").json()
        if status["state"] != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("sweep did not finish")


def test_sweep_end_to_end_through_the_api(client: TestClient) -> None:
    body = sma().model_dump(mode="json")
    started = client.post("/sweeps", json=body).json()
    assert started["total"] == 6
    status = wait(client, started["key"])
    assert (status["state"], status["done"], status["failed"]) == ("done", 6, 0)
    frame = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{started['key']}/summary").content))
    assert len(frame) == 6
    assert {"ticker", "fast", "slow", "sharpe", "key", "volatility_pct"} <= set(frame.columns)
    assert pd.api.types.is_datetime64_any_dtype(frame["first_signal"])
    assert (frame["first_signal"].dropna() <= frame["last_signal"].dropna()).all()
    for key in frame["key"]:
        assert client.get(f"/runs/{key}").status_code == 200
    again = client.post("/sweeps", json=body).json()
    assert (again["state"], again["done"]) == ("done", 6)


def test_breakout_sweep_runs(client: TestClient) -> None:
    body = sma(strategy="high_breakout", tickers=["AAPL"]).model_dump(mode="json")
    started = client.post("/sweeps", json=body).json()
    assert started["total"] == 1
    assert wait(client, started["key"])["state"] == "done"


def test_unknown_strategy_is_422(client: TestClient) -> None:
    assert client.post("/sweeps", json=sma(strategy="nope").model_dump(mode="json")).status_code == 422


def test_sweep_paths_are_cumulative_returns_per_run(client: TestClient) -> None:
    spec = sma()
    started = client.post("/sweeps", json=spec.model_dump(mode="json")).json()
    wait(client, started["key"])
    summary = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{started['key']}/summary").content))
    response = client.get(f"/sweeps/{started['key']}/paths")
    assert response.status_code == 200
    paths = pd.read_parquet(io.BytesIO(response.content))
    assert set(paths.columns) == {"date", *summary["key"]}
    key = str(summary["key"].iloc[0])
    eq = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/equity").content))
    expected = 100 * (eq["equity"].to_numpy() / spec.execution.initial_cash - 1)
    assert paths[key].to_numpy() == pytest.approx(expected, rel=1e-5, abs=1e-3)
    assert client.get("/sweeps/0123/paths").status_code == 404
    assert client.get(f"/sweeps/{started['key']}/spec").json()["strategy"] == spec.strategy


def test_combinations_that_cannot_warm_up_are_skipped(root: Path) -> None:
    spec = sma(timeframe="1d", features="compute", grid={"fast": [3], "slow": [8, 300]})
    plan = expand(spec, Store.open(root / "store"), GRID)
    assert {r.params["slow"] for r in plan.runs} == {8}
    assert len(plan.skipped) == 2
    assert all("need more history" in s.reason for s in plan.skipped)


def test_running_sweeps_are_listed_and_can_be_stopped(root: Path, tmp_path: Path) -> None:
    release = threading.Event()
    started: list[str] = []

    def execute(payload: str) -> Digest:
        started.append(payload)
        release.wait(30)
        return Digest("x")

    async def scenario() -> tuple[
        list[SweepStatus], SweepStatus | None, list[SweepStatus], SweepStatus | None
    ]:
        with ThreadPoolExecutor(max_workers=1) as pool:
            manager = SweepManager(
                Store.open(root / "store"),
                ResultStore(tmp_path / "results"),
                pool=lambda: pool,
                execute=execute,
                concurrency=1,
                grid=GRID,
            )
            status = manager.start(sma())
            await asyncio.sleep(0.2)
            running = manager.running()
            stopped = manager.stop(Digest(status.key))
            await asyncio.sleep(0.1)
            after = manager.running()
            release.set()
            return running, stopped, after, manager.status(Digest(status.key))

    running, stopped, after, final = asyncio.run(scenario())
    assert [s.state for s in running] == ["running"]
    assert running[0].strategy == "sma_cross" and running[0].tickers == 2 and running[0].total == 6
    assert stopped is not None and stopped.state == "stopped"
    assert after == []
    assert final is not None and final.state == "stopped" and final.done < final.total
    assert len(started) == 1


def test_stopping_an_unknown_sweep_is_404(client: TestClient) -> None:
    assert client.post("/sweeps/feedface/stop").status_code == 404
    assert client.get("/sweeps").json() == []


def test_stored_summaries_gain_newly_declared_columns(client: TestClient, root: Path) -> None:
    body = sma(tickers=["AAPL"]).model_dump(mode="json")
    key = client.post("/sweeps", json=body).json()["key"]
    wait(client, key)
    path = root / "results" / "sweeps" / f"{key}.parquet"
    legacy = pq.read_table(path).drop_columns(["volatility_pct", "first_signal", "last_signal"])
    pq.write_table(legacy, path)
    frame = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{key}/summary").content))
    assert list(frame.columns[-len(SUMMARY) :]) == list(SUMMARY)
    assert frame["volatility_pct"].notna().all()
    assert pd.api.types.is_datetime64_any_dtype(frame["first_signal"])
    assert set(SUMMARY) <= set(pq.read_schema(path).names)
