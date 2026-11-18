from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from tape.calendar import fx_sessions
from tape.catalog.adapters import FX_VENDOR, fx_minutes, fx_symbol, ingest_fx
from tape.catalog.build import build
from tape.catalog.source import SourceError
from tape.catalog.store import Store
from tape.config import Settings
from tape.core.types import Ticker, Timeframe
from tape.runner import RunSpec, execute
from tape.service.api import create_app
from tape.venues import Venue
from tests.conftest import minutes
from tests.test_engine_runs import DAYS

if TYPE_CHECKING:
    from collections.abc import Iterator


def ns(stamp: str) -> int:
    return int(np.datetime64(stamp, "ns").astype(np.int64))


def vendor(start: str, end: str, seed: int, level: float) -> pa.Table:
    rng = np.random.default_rng(seed)
    time = np.arange(np.datetime64(start, "ms"), np.datetime64(end, "ms"), np.timedelta64(60_000, "ms"))
    first, last = (np.datetime64(v, "D").astype(date) for v in (start, end))
    stamps = time.astype("datetime64[ns]").view(np.int64)
    _, inside = fx_sessions(first, last + timedelta(days=1)).locate(stamps)
    time = time[inside]
    close = level * np.exp(np.cumsum(rng.normal(0, 2e-4, len(time))))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 5e-5, len(time))) * close
    return pa.Table.from_arrays(
        [
            pa.array(time).cast(pa.timestamp("ms", tz="UTC")),
            pa.array(open_),
            pa.array(np.maximum(open_, close) + spread),
            pa.array(np.minimum(open_, close) - spread),
            pa.array(close),
            pa.array(np.zeros(len(time))),
            pa.array(rng.integers(1, 200, len(time)).astype(np.uint64)),
        ],
        schema=FX_VENDOR,
    )


def test_fx_sessions_roll_at_five_in_new_york() -> None:
    s = fx_sessions(date(2024, 3, 8), date(2024, 3, 12))
    assert [str(d) for d in s.day] == ["2024-03-08", "2024-03-11", "2024-03-12"]
    assert s.open_ns.tolist() == [ns("2024-03-07T22:00"), ns("2024-03-10T21:00"), ns("2024-03-11T21:00")]
    assert s.close_ns.tolist() == [ns("2024-03-08T22:00"), ns("2024-03-11T21:00"), ns("2024-03-12T21:00")]
    assert s.open_ns[2] == s.close_ns[1]


def test_fx_holidays() -> None:
    days = {str(d) for d in fx_sessions(date(2022, 12, 22), date(2023, 1, 3)).day}
    assert {"2022-12-23", "2022-12-27", "2022-12-30", "2023-01-02", "2023-01-03"} <= days
    assert not {"2022-12-25", "2022-12-26", "2023-01-01"} & days
    assert "2025-12-25" not in {str(d) for d in fx_sessions(date(2025, 12, 24), date(2025, 12, 26)).day}


def test_a_minute_on_the_roll_opens_the_next_session() -> None:
    s = fx_sessions(date(2024, 11, 25), date(2024, 11, 26))
    index, inside = s.locate(np.array([ns("2024-11-25T21:59"), ns("2024-11-25T22:00")], dtype=np.int64))
    assert inside.tolist() == [True, True]
    assert index.tolist() == [0, 1]


def test_the_adapter_names_converts_and_trims() -> None:
    assert fx_symbol(Path("USD_JPY_forex_1m_2020-10-01_2026-11-17.parquet")) == "USDJPY"
    with pytest.raises(SourceError):
        fx_symbol(Path("USDJPY.parquet"))
    raw = vendor("2024-11-22T20:00", "2024-11-25T23:00", 1, 150.0)
    out = fx_minutes(raw, start=date(2024, 11, 25))
    assert out["time"].type == pa.timestamp("ms")
    assert out["time"][0].as_py().isoformat() == "2024-11-24T22:00:00"
    assert out["volume"].to_pylist() == raw["trades"].to_pylist()[-len(out) :]
    with pytest.raises(SourceError, match="not an FX session"):
        fx_minutes(raw, start=date(2024, 11, 23))
    with pytest.raises(SourceError, match="schema mismatch"):
        fx_minutes(raw.drop_columns(["trades"]))


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory) -> tuple[Store, Store]:
    root = tmp_path_factory.mktemp("fx")
    raw = root / "raw"
    raw.mkdir()
    for i, (name, level) in enumerate((("EUR_USD", 1.05), ("USD_JPY", 150.0))):
        table = vendor("2024-01-01T22:00", "2024-06-28T21:00", 20 + i, level)
        pq.write_table(table, raw / f"{name}_forex_1m_2024.parquet")
    ingest_fx(sorted(raw.glob("*.parquet")), root / "fx", start=date(2024, 1, 2))
    equities = root / "equities"
    equities.mkdir()
    for i, t in enumerate(["SPY", "AAPL"]):
        pq.write_table(minutes(DAYS, seed=10 + i), equities / f"{t}.parquet")
    build(root / "fx", root / "store", venue=Venue.FX)
    build(equities, root / "store")
    return Store.open(root / "store"), Store.open(root / "store", venue=Venue.FX)


def test_each_venue_opens_its_own_catalog(stores: tuple[Store, Store]) -> None:
    stock, fx = stores
    assert (stock.venue, fx.venue) == (Venue.XNYS, Venue.FX)
    assert fx.manifest.reference == "EURUSD"
    assert set(fx.manifest.available) == {"EURUSD", "USDJPY"}
    assert stock.manifest.data_version != fx.manifest.data_version
    report = fx.manifest.tickers[Ticker("USDJPY")]
    assert report.source_rows == report.regular_rows
    assert not report.sessions_missing
    daily = fx.bars(Ticker("EURUSD"), Timeframe.D1)
    assert len(daily) == fx.manifest.sessions


def test_a_yen_pair_trades_ten_thousand_dollars_and_reports_dollars(stores: tuple[Store, Store]) -> None:
    _, fx = stores
    spec = RunSpec.model_validate(
        {
            "ticker": "USDJPY",
            "start": "2024-01-02",
            "end": "2024-06-28",
            "strategy": "sma_cross",
            "params": {"timeframe": "1h", "fast": 10, "slow": 40},
            "features": "compute",
        }
    )
    r = execute(fx, spec)
    assert r.metrics.trades > 0
    first = r.fills.iloc[0]
    assert int(first["quantity"]) == 10_000
    closed = r.trades[~r.trades["open"]]
    sign = closed["side"].map({"long": 1, "short": -1})
    move = closed["exit_price"] - closed["entry_price"]
    dollars = (sign * closed["quantity"] * move / closed["exit_price"]).sum()
    assert closed["net_pnl"].sum() == pytest.approx(dollars)
    assert r.metrics.final_equity == pytest.approx(10_000 + r.trades["net_pnl"].sum())


@pytest.fixture(scope="module")
def service(stores: tuple[Store, Store]) -> Iterator[TestClient]:
    root = stores[0].root.parents[2]
    settings = Settings(source_dir=root / "fx", store_dir=root / "store", results_dir=root / "results")
    with TestClient(create_app(settings, workers=1)) as c:
        yield c


def test_one_service_serves_both_venues(service: TestClient) -> None:
    catalog = service.get("/catalog").json()
    assert [v["venue"] for v in catalog["venues"]] == ["XNYS", "FX"]
    by_ticker = {t["ticker"]: t for t in catalog["tickers"]}
    assert (by_ticker["USDJPY"]["venue"], by_ticker["USDJPY"]["price_precision"]) == ("FX", 3)
    assert (by_ticker["AAPL"]["venue"], by_ticker["AAPL"]["price_precision"]) == ("XNYS", 2)
    found = service.post("/universes/basket", json={"venue": "FX", "start": "2024-02-01"}).json()
    assert found["tickers"] == ["EURUSD", "USDJPY"]
    assert not found["sectors"]


def test_fx_runs_and_sweeps_go_to_the_fx_catalog(service: TestClient, stores: tuple[Store, Store]) -> None:
    stock, fx = stores
    run = {
        "ticker": "EURUSD",
        "start": "2024-01-02",
        "end": "2024-06-28",
        "strategy": "sma_cross",
        "params": {"timeframe": "1h", "fast": 10, "slow": 40},
        "features": "compute",
    }
    summary = service.post("/runs", json=run).json()
    assert summary["key"] == RunSpec.model_validate(run).key(fx.manifest.data_version)
    mixed = {
        "tickers": ["EURUSD", "AAPL"],
        "start": "2024-02-01",
        "end": "2024-06-28",
        "strategy": "sma_cross",
        "timeframe": "1h",
        "grid": {"fast": [10], "slow": [40]},
        "features": "compute",
    }
    refused = service.post("/sweeps", json=mixed)
    assert refused.status_code == 422
    assert "one venue" in refused.json()["detail"]
    assert stock.manifest.data_version != fx.manifest.data_version
