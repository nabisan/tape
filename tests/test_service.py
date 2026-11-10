from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tape.config import Settings
from tape.service.api import create_app
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

RUN = {
    "ticker": "AAPL",
    "start": "2024-02-01",
    "end": "2024-06-28",
    "strategy": "channel_breakout",
    "params": {"timeframe": "1h", "entry_window": 10, "exit_window": 5},
    "features": "compute",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    root: Path = tmp_path_factory.mktemp("svc")
    make_store(root)
    settings = Settings(source_dir=root / "source", store_dir=root / "store", results_dir=root / "results")
    with TestClient(create_app(settings, workers=1)) as c:
        yield c


def test_catalog_and_strategies(client: TestClient) -> None:
    cat = client.get("/catalog").json()
    assert [t["ticker"] for t in cat["tickers"]] == ["AAPL", "SPY"]
    strategies = client.get("/strategies").json()
    assert set(strategies) == {"sma_cross", "channel_breakout"}
    assert "fast" in strategies["sma_cross"]["config_schema"]["properties"]


def test_run_is_computed_then_cached(client: TestClient) -> None:
    first = client.post("/runs", json=RUN)
    assert first.status_code == 200, first.text
    again = client.post("/runs", json=RUN).json()
    assert (first.json()["cached"], again["cached"]) == (False, True)
    assert again["key"] == first.json()["key"]
    assert again["metrics"] == first.json()["metrics"]


def test_tables_are_parquet(client: TestClient) -> None:
    key = client.post("/runs", json=RUN).json()["key"]
    trades = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/trades").content))
    equity = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/equity").content))
    assert len(trades) > 0
    assert {"equity", "drawdown_pct", "position"} <= set(equity.columns)
    assert client.get(f"/runs/{key}").json()["spec"]["ticker"] == "AAPL"


def test_errors(client: TestClient) -> None:
    assert client.post("/runs", json={**RUN, "ticker": "MSFT"}).status_code == 404
    assert client.post("/runs", json={**RUN, "strategy": "nope"}).status_code == 404
    bad = {**RUN, "strategy": "sma_cross", "params": {"fast": 50, "slow": 20}}
    assert client.post("/runs", json=bad).status_code == 422
    assert client.get("/runs/0123/trades").status_code == 404
    assert client.get("/runs/x/notatable").status_code == 422


def test_precomputed_policy_rejects_missing_features(client: TestClient) -> None:
    r = client.post(
        "/runs", json={**RUN, "features": "precomputed", "params": {"timeframe": "1d", "entry_window": 30}}
    )
    assert r.status_code == 422
    assert "not precomputed" in r.json()["detail"]


def test_space_without_catalog_is_empty(client: TestClient) -> None:
    body = client.get("/strategies/sma_cross/space", params={"ticker": "AAPL"}).json()
    assert body["space"]["timeframes"] == []
    assert client.get("/strategies/nope/space", params={"ticker": "AAPL"}).status_code == 404
