from __future__ import annotations

import io
import time
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tape.catalog.store import Store
from tape.config import Settings
from tape.core.types import Ticker, Timeframe
from tape.features.build import build
from tape.features.catalog import FeatureCatalog
from tape.features.grid import FeatureGrid
from tape.features.kinds import ChannelHigh, ChannelLow, Sma
from tape.runner import FeaturePolicy
from tape.service.api import create_app
from tape.sweep import SweepSpec, expand
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
    plan = expand(sma(), FeatureCatalog(Store.open(root / "store")), GRID)
    pairs = sorted({(r.params["fast"], r.params["slow"]) for r in plan.runs})
    assert pairs == [(5, 10), (5, 20), (10, 20)]
    assert len(plan.runs) == 6
    assert plan.skipped == ()


def test_explicit_grid_is_intersected_with_the_catalog(root: Path) -> None:
    plan = expand(
        sma(grid={"fast": [5, 7], "slow": [20, 200]}), FeatureCatalog(Store.open(root / "store")), GRID
    )
    assert sorted({(r.params["fast"], r.params["slow"]) for r in plan.runs}) == [(5, 20)]


def test_tickers_without_features_are_skipped(root: Path) -> None:
    plan = expand(sma(timeframe="4h"), FeatureCatalog(Store.open(root / "store")), GRID)
    assert plan.runs == ()
    assert {s.ticker for s in plan.skipped} == {AAPL, SPY}


def test_compute_policy_takes_the_grid_as_given(root: Path) -> None:
    spec = sma(
        timeframe="4h", tickers=["AAPL"], grid={"fast": [3], "slow": [8]}, features=FeaturePolicy.COMPUTE
    )
    plan = expand(spec, FeatureCatalog(Store.open(root / "store")), GRID)
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
    assert {"ticker", "fast", "slow", "sharpe", "key"} <= set(frame.columns)
    for key in frame["key"]:
        assert client.get(f"/runs/{key}").status_code == 200
    again = client.post("/sweeps", json=body).json()
    assert (again["state"], again["done"]) == ("done", 6)


def test_channel_sweep_runs(client: TestClient) -> None:
    body = sma(strategy="channel_breakout", tickers=["AAPL"]).model_dump(mode="json")
    started = client.post("/sweeps", json=body).json()
    assert started["total"] == 1
    assert wait(client, started["key"])["state"] == "done"


def test_unknown_strategy_is_422(client: TestClient) -> None:
    assert client.post("/sweeps", json=sma(strategy="nope").model_dump(mode="json")).status_code == 422
