from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.features.build import FeatureGrid, build
from tape.features.catalog import FeatureCatalog, Origin
from tape.features.kinds import ChannelHigh, ChannelLow, Sma
from tape.runner import RunSpec, execute
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from pathlib import Path

GRID = FeatureGrid(
    timeframes=(Timeframe.H1, Timeframe.D1),
    specs=(Sma(window=5), Sma(window=20), ChannelHigh(window=10), ChannelLow(window=10)),
)
AAPL = Ticker("AAPL")


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    make_store(tmp_path)
    return tmp_path / "store"


def test_build_registers_every_missing_entry_once(store_dir: Path) -> None:
    assert build(store_dir, GRID) == 2 * 2 * 4
    assert build(store_dir, GRID) == 0
    catalog = FeatureCatalog(Store.open(store_dir))
    assert len(catalog) == 16
    assert {e.origin for e in catalog.entries()} == {Origin.BUILT}


def test_build_fills_only_gaps_and_force_rebuilds(store_dir: Path) -> None:
    build(store_dir, GRID.model_copy(update={"specs": GRID.specs[:2]}))
    assert build(store_dir, GRID) == 2 * 2 * 2
    assert build(store_dir, GRID, force=True) == 16
    assert len(FeatureCatalog(Store.open(store_dir))) == 16


def test_built_values_equal_direct_computation(store_dir: Path) -> None:
    build(store_dir, GRID, tickers=[AAPL])
    store = Store.open(store_dir)
    catalog = FeatureCatalog(store)
    bars = store.bars(AAPL, Timeframe.H1)
    for spec in GRID.specs:
        entry = catalog.lookup(AAPL, Timeframe.H1, spec)
        assert entry is not None
        np.testing.assert_array_equal(catalog.values(entry), spec.compute(bars))


def test_precomputed_run_after_build(store_dir: Path) -> None:
    build(store_dir, GRID)
    spec = RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-02-01",
            "end": "2024-06-28",
            "strategy": "sma_cross",
            "params": {"timeframe": "1h", "fast": 5, "slow": 20},
        }
    )
    assert execute(Store.open(store_dir), spec).metrics.sessions > 0
