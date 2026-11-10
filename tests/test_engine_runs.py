from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.calendar import sessions
from tape.catalog.build import build
from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import BacktestEngine
from tape.engine.matching import FillModel
from tape.runner import Result, RunSpec, execute, session_range
from tape.strategies.registry import create
from tests.conftest import minutes

if TYPE_CHECKING:
    from pathlib import Path

DAYS = [str(d) for d in sessions(date(2024, 1, 2), date(2024, 6, 28)).day]
CUT = date(2024, 4, 1)

SMA_D1 = ("sma_cross", {"timeframe": "1d", "fast": 3, "slow": 8})
SMA_H1 = ("sma_cross", {"timeframe": "1h", "fast": 4, "slow": 12})
CHANNEL_STOP = ("channel_breakout", {"timeframe": "1h", "entry_window": 10, "exit_window": 5})
CHANNEL_CLOSE = (
    "channel_breakout",
    {"timeframe": "15m", "entry_window": 20, "exit_window": 10, "trigger": "close"},
)
ALL = [SMA_D1, SMA_H1, CHANNEL_STOP, CHANNEL_CLOSE]


def make_store(root: Path, perturb_after: date | None = None) -> Store:
    src = root / "source"
    src.mkdir(parents=True)
    for i, t in enumerate(["SPY", "AAPL"]):
        table = minutes(DAYS, seed=10 + i)
        if perturb_after is not None:
            later = table["time"].cast(pa.int64()).to_numpy() >= np.datetime64(perturb_after, "ms").astype(
                np.int64
            )
            factor = np.where(later, 1.07, 1.0)
            for col in ("open", "high", "low", "close"):
                table = table.set_column(
                    table.schema.get_field_index(col), col, pa.array(table[col].to_numpy() * factor)
                )
        pq.write_table(table, src / f"{t}.parquet")
    build(src, root / "store")
    return Store.open(root / "store")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return make_store(tmp_path_factory.mktemp("base"))


def spec(strategy: tuple[str, dict[str, object]], **execution: object) -> RunSpec:
    name, params = strategy
    return RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-02-01",
            "end": "2024-06-28",
            "strategy": name,
            "params": params,
            "execution": execution,
        }
    )


@pytest.mark.parametrize("intraday", [False, True])
@pytest.mark.parametrize("strategy", ALL, ids=lambda s: f"{s[0]}-{s[1]['timeframe']}")
def test_ledger_reconciles(store: Store, strategy: tuple[str, dict[str, object]], intraday: bool) -> None:
    if intraday and strategy is SMA_D1:
        pytest.skip("daily decisions cannot trade intraday")
    r = execute(store, spec(strategy, intraday=intraday))
    assert len(r.fills) > 0
    assert r.metrics.final_equity - r.metrics.initial_cash == pytest.approx(
        r.trades["net_pnl"].sum(), abs=1e-6
    )
    assert r.metrics.total_commission == pytest.approx(r.fills["commission"].sum())


@pytest.mark.parametrize("strategy", ALL, ids=lambda s: f"{s[0]}-{s[1]['timeframe']}")
def test_no_look_ahead(store: Store, tmp_path: Path, strategy: tuple[str, dict[str, object]]) -> None:
    shifted = make_store(tmp_path, perturb_after=CUT)
    a, b = execute(store, spec(strategy)), execute(shifted, spec(strategy))
    cut = pd.Timestamp(CUT)
    before_a = a.fills[a.fills["time"] < cut].reset_index(drop=True)
    before_b = b.fills[b.fills["time"] < cut].reset_index(drop=True)
    assert len(before_a) > 0
    pd.testing.assert_frame_equal(before_a, before_b)


@pytest.mark.parametrize("strategy", [SMA_H1, CHANNEL_STOP, CHANNEL_CLOSE], ids=lambda s: s[0])
def test_intraday_is_flat_every_close(store: Store, strategy: tuple[str, dict[str, object]]) -> None:
    r = execute(store, spec(strategy, intraday=True))
    assert bool((r.equity["position"] == 0).all())
    squares = r.fills[r.fills["tag"] == "squareoff"]
    assert len(squares) > 0
    clock = squares["time"].dt.strftime("%H:%M")
    half_day = squares["time"].dt.date == date(2024, 7, 3)
    assert bool((clock[~half_day] >= "15:55").all())


def test_entry_delay_blocks_early_entries(store: Store) -> None:
    r = execute(store, spec(SMA_H1, intraday=True, entry_delay_minutes=120))
    entries = r.orders[(r.orders["status"] == "filled") & (r.orders["tag"] == "signal")]
    rejected = r.orders[r.orders["status"] == "rejected"]
    assert len(rejected) > 0
    assert bool((rejected["reason"] == "outside entry window").all())
    assert bool((entries["submitted"].dt.strftime("%H:%M") >= "11:30").all())


def test_daily_signal_fills_at_next_session_open(store: Store) -> None:
    r = execute(store, spec(SMA_D1))
    m1 = store.bars(Ticker("AAPL"), Timeframe.M1)
    first_minutes = set(m1.time_ns[np.r_[True, m1.session[1:] != m1.session[:-1]]].tolist())
    fill_ns = r.fills["time"].astype("int64").tolist()
    assert all(t in first_minutes for t in fill_ns)
    assert bool(
        (
            r.fills["time"].dt.date
            > r.orders.set_index("order_id").loc[r.fills["order_id"], "submitted"].dt.date.to_numpy()
        ).all()
    )


def test_stop_orders_fill_at_level_or_gap(store: Store) -> None:
    free = FillModel(slippage_bps=0.0, commission_per_share=0.0)
    r = execute(store, spec(CHANNEL_STOP, fill=free.model_dump()))
    stops = r.orders[(r.orders["type"] == "stop_market") & (r.orders["status"] == "filled")]
    assert len(stops) > 0
    m1 = store.bars(Ticker("AAPL"), Timeframe.M1)
    opens = dict(zip(m1.time_ns.tolist(), m1.open.tolist(), strict=True))
    for _, o in stops.iterrows():
        at = opens[int(o["closed"].value)]
        assert o["fill_price"] == pytest.approx(o["price"]) or o["fill_price"] == pytest.approx(at)


def test_deterministic(store: Store) -> None:
    a: Result = execute(store, spec(CHANNEL_STOP))
    b: Result = execute(store, spec(CHANNEL_STOP))
    pd.testing.assert_frame_equal(a.fills, b.fills)
    assert a.key == b.key
    assert a.metrics == b.metrics


@pytest.mark.parametrize("intraday", [False, True])
@pytest.mark.parametrize("strategy", [CHANNEL_STOP, SMA_H1], ids=lambda s: s[0])
def test_fast_forward_is_exact(store: Store, strategy: tuple[str, dict[str, object]], intraday: bool) -> None:
    s = spec(strategy, intraday=intraday)
    sessions_ = store.sessions()
    first, last = session_range(sessions_, s.start, s.end)
    runs = []
    for fast in (True, False):
        strat = create(s.strategy, s.params)
        m1 = store.bars(Ticker("AAPL"), Timeframe.M1)
        decision = store.bars(Ticker("AAPL"), strat.config.timeframe)
        engine = BacktestEngine(
            strat, m1=m1, decision=decision, sessions=sessions_, execution=s.execution, fast_forward=fast
        )
        runs.append(engine.run(first, last))
    a, b = runs
    assert a.fills == b.fills
    np.testing.assert_array_equal(a.sessions.equity, b.sessions.equity)
    np.testing.assert_array_equal(a.bar_equity, b.bar_equity)
