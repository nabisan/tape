from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tape.core.types import Ticker, Timeframe
from tape.features.catalog import FeatureCatalog, Origin
from tape.features.grid import FeatureGrid
from tape.features.kinds import Sma
from tape.runner import MissingFeaturesError, RunSpec, execute
from tape.strategies.sma_cross import SmaCross
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from pathlib import Path

    from tape.catalog.store import Store

GRID = FeatureGrid(timeframes=(), specs=(Sma(window=5), Sma(window=20)), on_demand=(Timeframe.M1,))
AAPL = Ticker("AAPL")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return make_store(tmp_path)


def run(fast: int, slow: int) -> RunSpec:
    return RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-05-01",
            "end": "2024-05-31",
            "strategy": "sma_cross",
            "params": {"timeframe": "1m", "fast": fast, "slow": slow},
        }
    )


def test_one_minute_is_offered_on_demand(store: Store) -> None:
    space = SmaCross.parameters.resolve(FeatureCatalog(store), AAPL, GRID)
    assert space.timeframes == [Timeframe.M1]
    assert space.on_demand == [Timeframe.M1]
    assert space.choices[Timeframe.M1] == {"fast": [5, 20], "slow": [5, 20]}


def test_first_run_materializes_and_registers(store: Store) -> None:
    result = execute(store, run(5, 20), GRID)
    assert result.metrics.sessions > 0
    catalog = FeatureCatalog(store)
    entries = catalog.entries(ticker=AAPL, timeframe=Timeframe.M1)
    assert {e.spec for e in entries} == {Sma(window=5), Sma(window=20)}
    assert {e.origin for e in entries} == {Origin.BUILT}
    assert SmaCross.parameters.resolve(catalog, AAPL, GRID).on_demand == []
    again = execute(store, run(5, 20), GRID)
    assert len(FeatureCatalog(store)) == 2
    assert again.metrics == result.metrics


def test_window_off_the_grid_is_refused(store: Store) -> None:
    with pytest.raises(MissingFeaturesError, match="not precomputed: sma_50"):
        execute(store, run(5, 50), GRID)
    assert len(FeatureCatalog(store)) == 0
