from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from tape.core.types import Timeframe
from tape.engine.costs import Cost
from tape.engine.exits import Exit
from tape.lab import blotter, nav, views
from tape.lab.form.model import HERE, ExecutionChoice, Form, RunRequest, SweepRequest, seed
from tape.lab.mount import PLOTLY
from tape.lab.widgets.ledger import HERE as WIDGETS
from tape.lab.widgets.ledger import Ledger
from tape.lab.widgets.paths import Paths, spec
from tape.service.models import PathsMeta, StoredSweep
from tape.strategies.sma_cross import SmaCrossConfig
from tape.ui import charts
from tests.test_sweep import client, root, sma, wait

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

__all__ = ["client", "root"]


def test_stored_simulations_are_listed_newest_first_with_their_spec(client: TestClient) -> None:
    keys = [
        client.post("/sweeps", json=sma(tickers=[t]).model_dump(mode="json")).json()["key"]
        for t in ("AAPL", "SPY")
    ]
    for key in keys:
        wait(client, key)
    stored = [StoredSweep.model_validate(s) for s in client.get("/sweeps/stored").json()]
    assert [s.key for s in stored][:2] == keys[::-1]
    assert {s.strategy for s in stored} == {"sma_cross"} and stored[0].tickers == 1
    assert client.get(f"/sweeps/{keys[0]}").status_code == 200


def test_the_pages_are_served_at_the_root_and_old_sweep_links_still_open(client: TestClient) -> None:
    r = client.get("/simulation?sweep=abc", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].endswith("/simulation/?sweep=abc")
    old = client.get("/?sweep=abc", follow_redirects=False)
    assert old.status_code == 307 and old.headers["location"] == "/simulation/?sweep=abc"
    page = client.get("/simulation/")
    assert page.status_code == 200
    assert "tape · Simulation" in page.text


FRAME = pd.DataFrame(
    {
        "ticker": ["AAPL", "SPY", "AAPL"],
        "fast": [5, 5, 10],
        "slow": [20, 20, 20],
        "key": ["a", "b", "c"],
        "total_return_pct": [-12.0, 3.5, float("nan")],
        "sharpe": [-1.0, 0.4, float("nan")],
        "win_rate_pct": [40.0, 50.0, 45.0],
        "first_signal": pd.Series(["2024-02-01 10:30", None, "2024-03-01 09:35"], dtype="datetime64[ns]"),
    }
)


def test_the_return_filter_keeps_only_runs_inside_the_dragged_range() -> None:
    assert list(views.within(FRAME, "total_return_pct", [-15.0, 0.0])["key"]) == ["a"]
    assert views.within(FRAME, "total_return_pct", None) is FRAME


def test_the_runs_sheet_labels_columns_drops_fixed_ones_and_gates_win_rate() -> None:
    spec = sma(grid={"fast": [5, 10]})
    shown = views.sheet(FRAME, spec, frozenset({Exit.SIGNAL}))
    assert "Slow" not in shown.columns
    assert {"Ticker", "Fast", "Return %", "Sharpe", "key"} <= set(shown.columns)
    assert "Win %" not in shown.columns
    assert list(shown["First signal"]) == ["01 Feb 2024 10:30:00", "", "01 Mar 2024 09:35:00"]
    with_tp = views.sheet(FRAME, spec, frozenset({Exit.SIGNAL, Exit.TAKE_PROFIT}))
    assert "Win %" in with_tp.columns


@pytest.mark.parametrize(
    ("value", "text"), [(-12.345, "\u221212.35"), (float("nan"), "\u2013"), ("x", "\u2013")]
)
def test_cells_format_numbers_with_true_minus_and_dash_for_missing(value: object, text: str) -> None:
    assert views.cell("{:.2f}")(value) == text


def test_simulation_choices_read_like_the_header() -> None:
    s = StoredSweep(
        key="47f38ab27c77f0ec",
        strategy="sma_cross",
        timeframe=Timeframe.H1,
        tickers=2,
        start=date(2024, 2, 1),
        end=date(2024, 6, 28),
        saved=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert (
        views.choice(s, "SMA cross")
        == "SMA cross · 1h · 2 tickers · 01 Feb 2024 \u2013 28 Jun 2024 · 47f38ab2"
    )


SMA = SmaCrossConfig.model_json_schema()


def test_single_line_is_the_home_page_next_to_the_api(client: TestClient) -> None:
    assert "tape · Single line" in client.get("/?run=abc").text
    assert client.get("/health").json()["status"] == "ok"
    assert nav.link("run", run="abc") == "/?run=abc"
    assert nav.link("simulation") == "/simulation/"


REQUEST = {
    "n": 1,
    "ticker": "AAPL",
    "start": "2024-02-01",
    "end": "2024-06-28",
    "legs": {"A": {"strategy": "sma_cross", "params": {"timeframe": "1h", "fast": 5, "slow": 20}}},
    "execution": {"commission": True, "commission_per_share": 0.01, "slippage": False, "slippage_bps": 3.0},
}


def test_a_submitted_form_becomes_typed_run_specs_with_capital_from_the_notional() -> None:
    specs = RunRequest.model_validate(REQUEST).specs()
    a = specs["A"]
    assert (a.ticker, a.strategy, a.timeframe) == ("AAPL", "sma_cross", Timeframe.H1)
    assert a.execution.fill.commission_per_share == 0.01 and a.execution.fill.slippage_bps == 0.0
    assert a.execution.initial_cash == SmaCrossConfig.model_validate(a.params).notional


def test_invalid_submissions_are_rejected_with_the_rule_that_failed() -> None:
    crossed = {**REQUEST, "legs": {"A": {"strategy": "sma_cross", "params": {"fast": 50, "slow": 20}}}}
    with pytest.raises(ValidationError, match="fast must be shorter than slow"):
        RunRequest.model_validate(crossed).specs()
    with pytest.raises(ValidationError):
        RunRequest.model_validate({**REQUEST, "legs": {}})


def test_a_submitted_simulation_becomes_a_typed_sweep_spec() -> None:
    sent = {
        "n": 2,
        "tickers": ["AAPL", "SPY"],
        "start": "2024-02-01",
        "end": "2024-06-28",
        "strategy": "sma_cross",
        "timeframe": "1h",
        "grid": {"fast": [5, 10], "slow": [20]},
        "params": {"direction": "long_only", "sizing": "notional", "notional": 50_000},
        "execution": {"slippage": True, "slippage_bps": 2.0},
    }
    spec = SweepRequest.model_validate(sent).spec()
    assert spec.tickers == ("AAPL", "SPY") and spec.timeframe is Timeframe.H1
    assert spec.grid == {"fast": (5, 10), "slow": (20,)}
    assert spec.execution.initial_cash == 50_000 and spec.execution.fill.slippage_bps == 2.0
    with pytest.raises(ValidationError):
        SweepRequest.model_validate({**sent, "tickers": []})


def test_execution_choices_round_trip_and_charge_only_what_is_switched_on() -> None:
    off = ExecutionChoice().spec()
    assert off.fill.costs == frozenset() and not off.intraday
    on = ExecutionChoice(commission=True, slippage=True, intraday=True, entry_delay_minutes=5).spec()
    assert (
        on.fill.commission_per_share == 0.005 and on.fill.slippage_bps == 1.0 and on.entry_delay_minutes == 5
    )
    assert ExecutionChoice.of(on).spec() == on


def test_an_opened_run_seeds_the_form() -> None:
    specs = RunRequest.model_validate(REQUEST).specs()
    found = seed(specs)
    assert found["ticker"] == "AAPL" and found["start"] == "2024-02-01" and not found["compare"]
    params = {"timeframe": "1h", "fast": 5, "slow": 20}
    assert found["legs"]["A"] == {"strategy": "sma_cross", "params": params}
    assert found["execution"]["commission"] and found["execution"]["commission_per_share"] == 0.01
    assert seed({}) == {}


def test_the_form_ships_its_own_script_and_styles() -> None:
    form = Form(page="run", pages=nav.PAGES)
    assert form.page == "run" and form.pages["simulation"] == "/simulation/"
    for path in (HERE / "form.js", HERE / "form.css"):
        assert path.is_file() and path.read_text().strip()


TRADES = pd.DataFrame(
    {
        "trade_id": [1],
        "side": ["long"],
        "entry_time": pd.Series(["2024-02-01 09:30"], dtype="datetime64[ns]"),
        "duration": pd.to_timedelta(["1 days 02:30:00"]),
        "commission": [1.5],
        "net_pnl": [-12.25],
        "open": [False],
    }
)


def test_the_trades_sheet_labels_stamps_and_drops_commission_when_not_charged() -> None:
    free = blotter.sheet(TRADES, blotter.TRADES, frozenset())
    assert list(free.columns) == ["Side", "Entry", "Held", "Net $"]
    assert list(free["Entry"]) == ["01 Feb 2024 09:30:00"] and list(free["Held"]) == ["1d 02h"]
    paid = blotter.sheet(TRADES, blotter.TRADES, frozenset({Cost.COMMISSION}))
    assert "Comm $" in paid.columns


def test_paths_stream_as_typed_dates_and_exact_float32_series(client: TestClient) -> None:
    key = client.post("/sweeps", json=sma(tickers=["AAPL"]).model_dump(mode="json")).json()["key"]
    wait(client, key)
    meta = PathsMeta.model_validate(client.get(f"/sweeps/{key}/paths/meta").json())
    r = client.get(f"/sweeps/{key}/paths/values", headers={"accept-encoding": "gzip"})
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-encoding"] == "gzip"
    values = np.frombuffer(r.content, "<f4").reshape(len(meta.series), len(meta.dates))
    stored = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{key}/paths").content))
    assert meta.dates == [d.date() for d in stored["date"]]
    for i, name in enumerate(meta.series):
        np.testing.assert_array_equal(values[i], stored[name].to_numpy(dtype="<f4"))


def test_plotly_is_served_by_our_own_server_and_cached_for_good(client: TestClient) -> None:
    r = client.get(PLOTLY)
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert len(r.content) > 1_000_000


def test_the_paths_widget_ships_style_and_roles_but_no_data() -> None:
    found = spec(["a", "b"], {"a": "AAPL 5/20"}, "QQQ")
    assert [t["meta"] for t in found["traces"]] == ["runs", "reference", "best:a", "median"]
    assert all("x" not in t and "y" not in t for t in found["traces"])
    assert found["layout"]["height"] == charts.EQUITY_HEIGHT
    widget = Paths.of("abc", ["a"], {}, None)
    assert widget.key == "abc" and [t["meta"] for t in widget.figure["traces"]] == ["runs", "median"]


def test_the_ledger_widget_ships_its_script_and_styles() -> None:
    widget = Ledger(key="abc")
    assert widget.key == "abc" and widget.api == ""
    for name in ("ledger.js", "widgets.css"):
        assert (WIDGETS / name).read_text().strip()
