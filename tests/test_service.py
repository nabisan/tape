from __future__ import annotations

import io
import json
import time
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from tape.config import Settings
from tape.engine.tables import FILLS, ORDERS, TRADES
from tape.features.grid import FeatureGrid
from tape.fundamentals import save
from tape.service.api import create_app
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

RUN = {
    "ticker": "AAPL",
    "start": "2024-02-01",
    "end": "2024-06-28",
    "strategy": "high_breakout",
    "params": {"timeframe": "1h", "stop_loss_pct": 1.0},
    "features": "compute",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    root: Path = tmp_path_factory.mktemp("svc")
    make_store(root)
    settings = Settings(source_dir=root / "source", store_dir=root / "store", results_dir=root / "results")
    with TestClient(create_app(settings, workers=1, grid=FeatureGrid(timeframes=(), specs=()))) as c:
        yield c


def test_catalog_and_strategies(client: TestClient) -> None:
    cat = client.get("/catalog").json()
    assert [t["ticker"] for t in cat["tickers"]] == ["AAPL", "SPY"]
    strategies = client.get("/strategies").json()
    assert set(strategies) == {"sma_cross", "high_breakout", "low_breakdown"}
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
        "/runs",
        json={
            **RUN,
            "strategy": "sma_cross",
            "features": "precomputed",
            "params": {"timeframe": "1d", "fast": 5, "slow": 30},
        },
    )
    assert r.status_code == 422
    assert "not precomputed" in r.json()["detail"]


def test_space_without_catalog_is_empty(client: TestClient) -> None:
    body = client.get("/strategies/sma_cross/space", params={"ticker": "AAPL"}).json()
    assert body["space"]["timeframes"] == []
    assert client.get("/strategies/nope/space", params={"ticker": "AAPL"}).status_code == 404


def test_universes_split_members_by_catalog(client: TestClient) -> None:
    universes = {u["membership"]["index"]: u for u in client.get("/universes").json()}
    assert {"sp500", "ndx"} <= set(universes)
    sp500 = universes["sp500"]
    assert "AAPL" in sp500["in_catalog"]
    assert "SPY" not in sp500["in_catalog"]
    assert len(sp500["in_catalog"]) + len(sp500["missing"]) == len(sp500["membership"]["tickers"])
    assert sp500["membership"]["point_in_time"] is False


def test_universe_profiles(client: TestClient) -> None:
    body = client.get("/universes/profiles", params={"before": "2024-06-03"}).json()
    assert {p["ticker"] for p in body["profiles"]} == {"AAPL", "SPY"}
    assert body["last"] < "2024-06-03"
    assert all(p["sessions"] > 0 for p in body["profiles"])


def test_classification_covers_every_catalog_ticker(client: TestClient) -> None:
    entries = {e["ticker"]: e for e in client.get("/universes/classification").json()}
    assert set(entries) == {"AAPL", "SPY"}


def test_tables_can_be_read_in_a_time_window(client: TestClient) -> None:
    key = client.post("/runs", json=RUN).json()["key"]
    full = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/bars").content))
    response = client.get(f"/runs/{key}/bars", params={"start": "2024-03-01", "end": "2024-03-28"})
    part = pd.read_parquet(io.BytesIO(response.content))
    inside = full[(full["time"] >= "2024-03-01") & (full["time"] < "2024-03-29")]
    assert len(part) == len(inside) > 0
    assert response.headers["X-Tape-Bars-Per-Row"] == "1"
    trades = pd.read_parquet(
        io.BytesIO(client.get(f"/runs/{key}/trades", params={"start": "2024-04-01"}).content)
    )
    assert (trades["entry_time"] >= "2024-04-01").all()


def test_wide_bar_windows_are_aggregated(client: TestClient) -> None:
    key = client.post("/runs", json=RUN).json()["key"]
    full = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/bars").content))
    response = client.get(
        f"/runs/{key}/bars", params={"start": "2024-01-01", "end": "2024-12-31", "max_rows": 10}
    )
    thin = pd.read_parquet(io.BytesIO(response.content))
    factor = int(response.headers["X-Tape-Bars-Per-Row"])
    assert len(thin) <= 10 < len(full)
    assert factor == -(-len(full) // 10)
    assert thin["high"].max() == full["high"].max()
    assert thin["low"].min() == full["low"].min()
    assert thin["volume"].sum() == full["volume"].sum()
    assert thin["open"].iloc[0] == full["open"].iloc[0]
    assert thin["close"].iloc[-1] == full["close"].iloc[-1]


def test_run_days_and_recent_window(client: TestClient) -> None:
    key = client.post("/runs", json=RUN).json()["key"]
    body = client.get(f"/runs/{key}/days", params={"recent": 10}).json()
    full = pd.read_parquet(io.BytesIO(client.get(f"/runs/{key}/bars").content))
    assert body["days"] == sorted({str(t.date()) for t in full["time"]})
    assert body["recent"] == str(full["time"].iloc[-10].date())


def test_results_from_an_older_engine_are_gone_not_errors(tmp_path: Path) -> None:
    make_store(tmp_path)
    settings = Settings(
        source_dir=tmp_path / "source", store_dir=tmp_path / "store", results_dir=tmp_path / "results"
    )
    with TestClient(create_app(settings, workers=1, grid=FeatureGrid(timeframes=(), specs=()))) as c:
        key = c.post("/runs", json=RUN).json()["key"]
        spec = tmp_path / "results" / key / "spec.json"
        legacy = json.loads(spec.read_text())
        legacy["execution"]["fill"]["adaptive_high_low"] = True
        spec.write_text(json.dumps(legacy))
        for path in (f"/runs/{key}", f"/runs/{key}/comparison"):
            r = c.get(path)
            assert r.status_code == 410
            assert "older engine version" in r.json()["detail"]
        sweeps = tmp_path / "results" / "sweeps"
        sweeps.mkdir(parents=True, exist_ok=True)
        (sweeps / "ab12.json").write_text(json.dumps({"legacy": True}))
        r = c.get("/sweeps/ab12/spec")
        assert r.status_code == 410
        assert "older engine version" in r.json()["detail"]


def test_zero_trade_tables_read_typed_and_windowed(tmp_path: Path) -> None:
    make_store(tmp_path)
    settings = Settings(
        source_dir=tmp_path / "source", store_dir=tmp_path / "store", results_dir=tmp_path / "results"
    )
    with TestClient(create_app(settings, workers=1, grid=FeatureGrid(timeframes=(), specs=()))) as c:
        key = c.post("/runs", json=RUN).json()["key"]
        for name in ("fills", "trades", "orders"):
            pd.DataFrame().to_parquet(tmp_path / "results" / key / f"{name}.parquet")
        window: dict[str, str | int] = {"start": "2024-03-01", "end": "2024-03-28", "max_rows": 100}
        for name, schema in (("fills", FILLS), ("trades", TRADES), ("orders", ORDERS)):
            r = c.get(f"/runs/{key}/{name}", params=window)
            assert r.status_code == 200, r.text
            frame = pd.read_parquet(io.BytesIO(r.content))
            assert list(frame.columns) == schema.names
            assert frame.empty


def test_cold_runs_are_refused_with_a_reason(client: TestClient) -> None:
    cold = {**RUN, "strategy": "sma_cross", "params": {"timeframe": "1d", "fast": 3, "slow": 200}}
    r = client.post("/runs", json=cold)
    assert r.status_code == 422
    assert "sma_200 on 1d never has a value" in r.json()["detail"]


def test_space_offers_only_windows_warm_by_the_end(client: TestClient) -> None:
    full = client.get("/strategies/sma_cross/space", params={"ticker": "AAPL"}).json()["space"]
    early = client.get("/strategies/sma_cross/space", params={"ticker": "AAPL", "end": "2024-01-20"}).json()[
        "space"
    ]
    for tf, domain in early["choices"].items():
        for name, windows in domain.items():
            assert set(windows) <= set(full["choices"][tf][name])
            assert all(early["ready"][tf][name][str(w)] <= "2024-01-20" for w in windows)


def test_valuation_route_with_and_without_fundamentals(tmp_path: Path) -> None:
    make_store(tmp_path)
    store = tmp_path / "store"
    settings = Settings(source_dir=tmp_path / "source", store_dir=store, results_dir=tmp_path / "results")
    grid = FeatureGrid(timeframes=(), specs=())
    with TestClient(create_app(settings, workers=1, grid=grid)) as c:
        key = c.post("/runs", json=RUN).json()["key"]
        r = c.get(f"/runs/{key}/valuation")
        assert r.status_code == 200, r.text
        assert r.json() == {"ticker": RUN["ticker"], "start": None, "end": None}
    days = pd.date_range(RUN["start"], RUN["end"], freq="D")
    table = pa.table(
        {
            "ticker": [RUN["ticker"]] * len(days),
            "day": pa.array(days.date, pa.date32()),
            "shares": [1e9] * len(days),
            "market_cap": [5e10] * len(days),
            "net_income": [1e9] * len(days),
            "pe": [50.0] * len(days),
            "earnings_period": pa.array(days.date, pa.date32()),
            "plausible": [True] * len(days),
        }
    )
    save(table, store)
    with TestClient(create_app(settings, workers=1, grid=grid)) as c:
        body = c.get(f"/runs/{key}/valuation").json()
        assert body["start"]["market_cap"] == 5e10 and body["end"]["pe"] == 50.0


def test_ledger_builds_in_the_background_and_downloads(tmp_path: Path) -> None:
    make_store(tmp_path)
    settings = Settings(
        source_dir=tmp_path / "source", store_dir=tmp_path / "store", results_dir=tmp_path / "results"
    )
    with TestClient(create_app(settings, workers=1, grid=FeatureGrid(timeframes=(), specs=()))) as c:
        key = c.post("/runs", json=RUN).json()["key"]
        assert c.get(f"/runs/{key}/ledger").json()["state"] == "absent"
        assert c.get(f"/runs/{key}/ledger.xlsx").status_code == 404
        assert c.post(f"/runs/{key}/ledger").json()["state"] in {"building", "ready"}
        deadline = time.monotonic() + 120
        while (status := c.get(f"/runs/{key}/ledger").json())["state"] == "building":
            assert time.monotonic() < deadline
            time.sleep(0.2)
        assert status["state"] == "ready", status
        r = c.get(f"/runs/{key}/ledger.xlsx")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert "AAPL_high_breakout_1h_" in r.headers["content-disposition"]
        assert len(r.content) == status["size"]
        sheet = load_workbook(io.BytesIO(r.content)).active
        assert sheet is not None and str(sheet.cell(1, 1).value).startswith("AAPL · High")
        assert c.post(f"/runs/{key}/ledger").json()["state"] == "ready"
        assert c.get("/runs/feedface/ledger").status_code == 404


def test_strategies_report_their_exits(client: TestClient) -> None:
    body = client.get("/strategies").json()
    assert body["sma_cross"]["exits"] == ["signal"]
    assert body["high_breakout"]["exits"] == ["stop_loss"]
