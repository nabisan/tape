from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

from tape.catalog.bars import bar_ends
from tape.core.types import Ticker, Timeframe
from tape.features.align import as_of
from tape.features.catalog import FeatureCatalog
from tape.features.grid import DEFAULT_GRID, MOVING
from tape.features.kinds import Sma
from tape.runner import execute
from tape.strategies.sma_breakout import SmaBreakout, SmaBreakoutConfig
from tests.test_engine_runs import SB_1D, SB_1H, make_store, spec

if TYPE_CHECKING:
    from pathlib import Path

    from tape.catalog.store import Store
    from tape.runner import Result

FIVE = pd.Timedelta(minutes=5)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return make_store(tmp_path_factory.mktemp("sma_breakout"))


def test_as_of_uses_only_completed_source_bars() -> None:
    values = np.array([1.0, 2.0, 3.0])
    source_ends = np.array([60, 120, 180], dtype=np.int64)
    targets = np.array([5, 60, 65, 119, 120, 500], dtype=np.int64)
    np.testing.assert_array_equal(as_of(values, source_ends, targets), [np.nan, 1.0, 1.0, 1.0, 2.0, 3.0])


def test_a_5m_bar_sees_the_hourly_sma_of_the_last_completed_hour(store: Store) -> None:
    r = execute(store, spec(SB_1H))
    hourly = store.bars(Ticker("AAPL"), Timeframe.H1)
    sma = Sma(window=20).compute(hourly)
    hour_end = bar_ends(hourly, store.sessions(), Timeframe.H1)
    bars = r.bars.set_index("time")
    for stamp in ("2024-04-10 13:30", "2024-04-10 14:25", "2024-04-10 14:30"):
        t = pd.Timestamp(stamp)
        close = (t + FIVE).value
        completed = int(np.searchsorted(hour_end, close, side="right")) - 1
        assert hour_end[completed] <= close < hour_end[completed + 1]
        assert bars.loc[t, "sma_20_1h"] == pytest.approx(sma[completed])


def _decided(r: Result, column: str) -> pd.DataFrame:
    bars = r.bars.assign(end=r.bars["time"] + FIVE)[["end", "close", column]]
    return r.orders.merge(bars, left_on="submitted", right_on="end", how="left")


@pytest.mark.parametrize(
    ("strategy", "column"), [(SB_1H, "sma_20_1h"), (SB_1D, "sma_5_1d")], ids=["1h", "1d"]
)
def test_entries_follow_the_sma_gate(
    store: Store, strategy: tuple[str, dict[str, object]], column: str
) -> None:
    r = execute(store, spec(strategy))
    orders = _decided(r, column)
    trend = orders[orders["tag"] == "trend"]
    breakout = orders[orders["tag"] == "breakout"]
    assert len(trend) > 0 and len(breakout) > 0
    assert bool((trend["close"] > trend[column]).all())
    assert bool((breakout["close"] >= breakout[column]).all())
    withdrawn = breakout[breakout["status"] == "canceled"]
    gate = r.bars.assign(end=r.bars["time"] + FIVE).set_index("end")
    assert bool((gate.loc[withdrawn["closed"], "close"] < gate.loc[withdrawn["closed"], column]).all())
    buys = r.fills[r.fills["side"] == "buy"]
    stops = r.orders[r.orders["origin"] == "stop_loss"]
    assert sorted(stops["submitted"]) == sorted(buys["time"])
    exits = orders[orders["tag"] == "sma_exit"]
    assert len(exits) > 0
    assert bool((exits["close"] < exits[column]).all())
    for at in exits["submitted"]:
        assert not bool(((stops["submitted"] < at) & ((stops["closed"] > at) | stops["closed"].isna())).any())
    assert bool((r.equity["position"] >= 0).all())
    assert bool((r.trades["side"] == "long").all())


def test_the_sma_window_is_a_precomputed_choice_needed_on_5m_only_for_the_5m_book() -> None:
    space = SmaBreakout.parameters
    assert set(space.windows) == {"window"}
    five = SmaBreakoutConfig.model_validate({"trend": "5m", "window": 50})
    hourly = SmaBreakoutConfig.model_validate({"trend": "1h", "window": 50})
    assert space.requires(five) == [Sma(window=50)]
    assert space.requires(hourly) == []


def test_the_strategy_offers_only_5m_with_precomputed_windows(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    resolved = SmaBreakout.parameters.resolve(FeatureCatalog(store), Ticker("AAPL"), DEFAULT_GRID)
    assert resolved.timeframes == [Timeframe.M5]
    assert resolved.choices[Timeframe.M5]["window"] == list(MOVING)
