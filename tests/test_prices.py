from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

from tape.service.prices import FILL_LIMIT, window
from tests.test_sweep import client, root  # noqa: F401

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def bars(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.5, n))
    sma = pd.Series(close).rolling(5).mean().to_numpy()
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-02 09:30", periods=n, freq="5min"),
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
            "volume": rng.integers(1, 100, n).astype(np.uint64),
            "equity": np.full(n, 10_000.0),
            "sma_5": sma,
        }
    )


def fills(frame: pd.DataFrame, at: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": frame["time"].iloc[at].to_numpy() + np.timedelta64(1, "m"),
            "side": ["buy" if i % 2 == 0 else "sell" for i in range(len(at))],
            "price": frame["close"].iloc[at].to_numpy(),
            "quantity": [100] * len(at),
            "tag": ["signal"] * len(at),
        }
    )


def test_every_bar_is_shown_when_it_fits() -> None:
    frame = bars(50)
    w = window(frame, fills(frame, [3, 20]), None, None, 100)
    assert (w.count, w.first, w.last, w.factor) == (50, 0, 49, 1)
    assert w.index == list(range(50))
    assert w.close == pytest.approx(frame["close"].tolist())
    assert w.indicators["sma_5"][:4] == [None] * 4
    assert w.indicators["sma_5"][4] == pytest.approx(frame["sma_5"].iloc[4])
    assert "equity" not in w.indicators and "volume" not in w.indicators
    assert [(f.index, f.side) for f in w.fills] == [(3, "buy"), (20, "sell")]


def test_buckets_are_true_candles_of_the_bars_they_cover() -> None:
    frame = bars(1_000)
    w = window(frame, fills(frame, []), None, None, 100)
    assert w.factor == 10
    assert len(w.index) == 100
    for k in (0, 37, 99):
        part = frame.iloc[k * 10 : k * 10 + 10]
        assert w.index[k] == k * 10
        assert w.open[k] == part["open"].iloc[0]
        assert w.high[k] == part["high"].max()
        assert w.low[k] == part["low"].min()
        assert w.close[k] == part["close"].iloc[-1]
        assert w.time[k] == part["time"].iloc[0].value // 1_000_000


def test_a_zoomed_range_keeps_global_bar_positions() -> None:
    frame = bars(1_000)
    w = window(frame, fills(frame, [5, 500, 640, 900]), 480, 659, 1_000)
    assert (w.first, w.last, w.factor) == (480, 659, 1)
    assert w.index[0] == 480 and w.index[-1] == 659
    assert w.open[0] == frame["open"].iloc[480]
    assert [f.index for f in w.fills] == [500, 640]
    assert w.fills_in_range == 2


def test_out_of_range_requests_are_clamped() -> None:
    frame = bars(30)
    w = window(frame, fills(frame, []), -10, 10_000, 100)
    assert (w.first, w.last) == (0, 29)


def test_too_many_fills_are_counted_not_sent() -> None:
    frame = bars(FILL_LIMIT * 2 + 10)
    w = window(frame, fills(frame, list(range(0, FILL_LIMIT * 2 + 2, 2))), None, None, 500)
    assert w.fills == []
    assert w.fills_in_range == FILL_LIMIT + 1
    assert not math.isnan(w.close[-1])


def test_the_price_route_serves_a_run(client: TestClient) -> None:  # noqa: F811
    run = {
        "ticker": "AAPL",
        "start": "2024-02-01",
        "end": "2024-06-28",
        "strategy": "sma_cross",
        "params": {"timeframe": "5m", "fast": 5, "slow": 20},
        "features": "compute",
    }
    key = client.post("/runs", json=run).json()["key"]
    full = client.get(f"/runs/{key}/price", params={"points": 200})
    assert full.status_code == 200
    body = full.json()
    assert body["first"] == 0 and body["last"] == body["count"] - 1 and body["factor"] > 1
    part = client.get(f"/runs/{key}/price", params={"first": 100, "last": 199, "points": 500}).json()
    assert (part["first"], part["last"], part["factor"]) == (100, 199, 1)
    assert client.get("/runs/nope/price").status_code == 404
