from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.book import BOOK, BookError, combine, decode, encode, members, stats
from tape.core.types import Ticker
from tape.engine.tables import SchemaError
from tape.ui import charts
from tests.test_sweep import client, root, sma, wait  # noqa: F401

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

A, B = Ticker("AAA"), Ticker("BBB")


def marks(days: list[str], values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(days), dtype=float)


def test_sleeves_sum_and_drawdown_starts_from_capital() -> None:
    frame = combine(
        {
            A: marks(["2024-01-02", "2024-01-03", "2024-01-04"], [110.0, 90.0, 120.0]),
            B: marks(["2024-01-02", "2024-01-03", "2024-01-04"], [95.0, 100.0, 100.0]),
        },
        100.0,
    )
    assert frame["equity"].tolist() == [205.0, 190.0, 220.0]
    assert frame["pnl"].tolist() == [5.0, -15.0, 30.0]
    assert frame["drawdown"].tolist() == [0.0, -15.0, 0.0]
    assert frame["drawdown_pct"].iloc[1] == pytest.approx(100 * (190 / 205 - 1))
    assert frame["below_capital_pct"].tolist() == pytest.approx([0.0, -5.0, 0.0])


def test_a_book_that_never_makes_a_high_draws_down_from_capital() -> None:
    frame = combine({A: marks(["2024-01-02", "2024-01-03"], [95.0, 90.0])}, 100.0)
    assert frame["drawdown_pct"].tolist() == pytest.approx([-5.0, -10.0])
    assert frame["drawdown_pct"].tolist() == pytest.approx(frame["below_capital_pct"].tolist())


def test_sleeves_hold_cash_before_their_first_mark_and_their_last_mark_after() -> None:
    frame = combine(
        {
            A: marks(["2024-01-02", "2024-01-03", "2024-01-04"], [101.0, 102.0, 103.0]),
            B: marks(["2024-01-03"], [80.0]),
        },
        100.0,
    )
    assert frame["equity"].tolist() == [201.0, 182.0, 183.0]
    assert frame["live"].tolist() == [1, 2, 1]


def test_stats_name_the_worst_point_and_the_current_drawdown() -> None:
    frame = combine(
        {A: marks(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"], [120, 90, 96, 108])}, 100.0
    )
    found = stats(frame)
    assert found.capital == 100.0
    assert found.max_drawdown_pct == pytest.approx(-25.0)
    assert str(found.trough) == "2024-01-03"
    assert found.max_drawdown_sessions == 3
    assert found.max_capital_loss_pct == pytest.approx(-10.0)
    assert found.drawdown_now_pct == pytest.approx(-10.0)
    assert found.total_return_pct == pytest.approx(8.0)


def test_empty_and_cashless_books_are_refused() -> None:
    with pytest.raises(BookError):
        combine({}, 100.0)
    with pytest.raises(BookError):
        combine({A: marks(["2024-01-02"], [1.0])}, 0.0)
    with pytest.raises(BookError):
        combine({A: marks([], [])}, 100.0)


def test_members_share_every_parameter_of_the_chosen_run() -> None:
    summary = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "AAA", "BBB"],
            "fast": [5, 5, 10, 10],
            "slow": [20, 20, 20, 20],
            "key": ["k1", "k2", "k3", "k4"],
            "sharpe": [1.0, 0.5, 0.2, 0.1],
        }
    )
    assert members(summary, "k4") == {A: "k3", B: "k4"}
    with pytest.raises(BookError, match="not part"):
        members(summary, "nope")
    with pytest.raises(BookError, match="repeat a ticker"):
        members(summary.assign(ticker="AAA"), "k1")


def test_the_wire_format_is_declared_and_checked_both_ways() -> None:
    frame = combine({A: marks(["2024-01-02"], [101.0])}, 100.0)
    assert decode(encode(frame)).equals(frame)
    assert decode(encode(frame.iloc[0:0])).empty
    with pytest.raises(SchemaError):
        encode(frame.drop(columns="live"))
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame.drop(columns="capital"), preserve_index=False), buffer)
    with pytest.raises(SchemaError):
        decode(buffer.getvalue())
    assert BOOK.names[0] == "date"


def test_book_through_the_api_matches_its_runs(client: TestClient) -> None:  # noqa: F811
    spec = sma()
    key = client.post("/sweeps", json=spec.model_dump(mode="json")).json()["key"]
    wait(client, key)
    summary = pd.read_parquet(io.BytesIO(client.get(f"/sweeps/{key}/summary").content))
    run = str(summary["key"].iloc[0])
    response = client.get(f"/sweeps/{key}/book", params={"run": run})
    assert response.status_code == 200
    frame = decode(response.content)
    chosen = members(summary, run)
    assert set(chosen) == {"AAPL", "SPY"}
    cash = spec.execution.initial_cash
    assert frame["capital"].iloc[0] == 2 * cash
    final = sum(
        pd.read_parquet(io.BytesIO(client.get(f"/runs/{k}/equity").content))["equity"].iloc[-1]
        for k in chosen.values()
    )
    assert frame["equity"].iloc[-1] == pytest.approx(final)
    assert np.all(frame["drawdown_pct"] <= 0)
    assert client.get(f"/sweeps/{key}/book", params={"run": "nope"}).status_code == 422
    assert client.get("/sweeps/0123/book", params={"run": run}).status_code == 404


def test_both_underwater_panels_share_one_scale() -> None:
    frame = combine({A: marks(["2024-01-02", "2024-01-03", "2024-01-04"], [130.0, 104.0, 95.0])}, 100.0)
    fig = charts.book(frame)
    assert [t.name for t in fig.data] == ["Book", "Drawdown from peak", "Drawdown from capital"]
    assert list(fig.data[2].y) == pytest.approx([0.0, 0.0, -5.0])
    assert fig.layout.yaxis2.range == fig.layout.yaxis3.range
    assert fig.layout.yaxis2.range[0] <= float(frame["drawdown_pct"].min())
