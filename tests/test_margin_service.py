from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from tape.engine.backtest import ENGINE_VERSION, FillOn
from tape.engine.margin import MarginSpec
from tape.lab import views
from tape.lab.form.model import ExecutionChoice
from tape.runner import RunSpec, execute_book
from tape.service.models import AccountView, StoredSweep
from tape.ui import charts
from tests.test_fx import service, stores
from tests.test_sweep import wait

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from tape.catalog.store import Store

__all__ = ["service", "stores"]
MARGIN = MarginSpec()
SWEEP = {
    "tickers": ["EURUSD", "USDJPY"],
    "start": "2024-01-02",
    "end": "2024-06-28",
    "strategy": "sma_cross",
    "timeframe": "1h",
    "grid": {"fast": [5, 10], "slow": [20]},
    "execution": {"initial_cash": 250, "fill_on": "minute", "margin": MARGIN.model_dump()},
    "features": "compute",
}


def summary(client: TestClient, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{key}/summary").content))


def test_a_margin_simulation_stores_one_account_per_book_and_matches_the_engine(
    service: TestClient, stores: tuple[Store, Store]
) -> None:
    started = service.post("/sweeps", json=SWEEP).json()
    status = wait(service, started["key"])
    assert (status["state"], status["done"], status["failed"]) == ("done", 4, 0)
    frame = summary(service, started["key"])
    accounts = {}
    for _, row in frame.iterrows():
        assert service.get(f"/runs/{row['key']}").status_code == 200
        got = service.get(f"/runs/{row['key']}/account")
        assert got.status_code == 200, got.text
        accounts[row["key"]] = (row, AccountView.model_validate(got.json()))
    by_fast: dict[int, set[str]] = {}
    for row, view in accounts.values():
        by_fast.setdefault(int(row["fast"]), set()).add(view.book)
    assert all(len(books) == 1 for books in by_fast.values())
    assert len({b for books in by_fast.values() for b in books}) == 2
    row, view = next(iter(accounts.values()))
    specs = [
        RunSpec.model_validate(
            {
                "ticker": t,
                "start": SWEEP["start"],
                "end": SWEEP["end"],
                "strategy": "sma_cross",
                "params": {"timeframe": "1h", "fast": int(row["fast"]), "slow": 20},
                "execution": SWEEP["execution"],
                "features": "compute",
            }
        )
        for t in ("EURUSD", "USDJPY")
    ]
    direct = execute_book(stores[1], specs)
    assert direct.account == view.report
    assert view.book == direct.key
    assert {str(r.key) for r in direct.sleeves.values()} == {
        str(k) for k, (_, v) in accounts.items() if v.book == view.book
    }
    assert len(view.date) == len(view.equity) == len(view.locked) == len(view.free) > 0
    assert all(f >= 0 for f in view.free)
    book = service.get(f"/sweeps/{started['key']}/book", params={"run": str(row["key"])})
    assert book.status_code == 200, book.text


def test_a_margin_simulation_is_listed_as_margin_and_current(service: TestClient) -> None:
    key = service.post("/sweeps", json=SWEEP).json()["key"]
    wait(service, key)
    stored = {s.key: s for s in map(StoredSweep.model_validate, service.get("/sweeps/stored").json())}
    assert (stored[key].margin, stored[key].engine) == (True, ENGINE_VERSION)
    label = views.choice(stored[key], "SMA cross")
    assert label.endswith("· margin")
    older = stored[key].model_copy(update={"engine": ENGINE_VERSION - 1, "margin": False})
    assert views.choice(older, "SMA cross").endswith("· outdated engine")


def test_runs_outside_a_margin_book_have_no_account_and_single_margin_runs_are_refused(
    service: TestClient,
) -> None:
    plain = {**SWEEP, "execution": {"fill_on": "minute"}}
    key = service.post("/sweeps", json=plain).json()["key"]
    wait(service, key)
    run = str(summary(service, key)["key"].iloc[0])
    assert service.get(f"/runs/{run}/account").status_code == 404
    single = {
        "ticker": "EURUSD",
        "start": SWEEP["start"],
        "end": SWEEP["end"],
        "strategy": "sma_cross",
        "params": {"timeframe": "1h", "fast": 5, "slow": 20},
        "execution": SWEEP["execution"],
        "features": "compute",
    }
    refused = service.post("/runs", json=single)
    assert refused.status_code == 422
    assert "book" in refused.json()["detail"]


def test_the_lab_margin_choice_forces_minute_fills_and_round_trips() -> None:
    spec = ExecutionChoice(margin=True, leverage=20, maintenance=0.4).spec()
    assert spec.fill_on is FillOn.MINUTE
    assert spec.margin == MarginSpec(leverage=20, maintenance=0.4)
    assert ExecutionChoice.of(spec) == ExecutionChoice(
        margin=True, minute_fills=True, leverage=20, maintenance=0.4
    )
    plain = ExecutionChoice().spec()
    assert (plain.fill_on, plain.margin) == (FillOn.BAR, None)
    assert ExecutionChoice(minute_fills=True).spec().fill_on is FillOn.MINUTE


def test_the_book_panel_states_the_liquidation() -> None:
    at = pd.Timestamp("2024-03-05 14:32")
    view = AccountView.model_validate(
        {
            "book": "b",
            "report": {
                "liquidated_ns": at.value,
                "liquidations": 1,
                "rejections": 7,
                "max_locked": 120.5,
                "min_free": 0.0,
            },
            "date": [date(2024, 3, 5)],
            "equity": [100.0],
            "locked": [0.0],
            "free": [100.0],
            "maintenance": [0.0],
        }
    )
    shown = views.account_stats(view, MARGIN)
    for cell in ("30x", "50%", "Yes", "05 Mar 2024 14:32", "<td>7</td>", "$120.50", "$0.00"):
        assert cell in shown
    curve = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-03-04", "2024-03-05"]),
            "equity": [250.0, 100.0],
            "drawdown_pct": [0.0, -60.0],
            "below_capital_pct": [0.0, -60.0],
        }
    )
    lines = [s for s in charts.book(curve, at).layout.shapes if s.type == "line"]
    assert len(lines) == 3 and {pd.Timestamp(s.x0) for s in lines} == {at}
    assert not [s for s in charts.book(curve).layout.shapes if s.type == "line"]
