from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from tape.core.types import Ticker
from tape.engine import tables
from tape.engine.ledger import IN_POSITION, BarState, state
from tape.engine.matching import FillModel
from tape.runner import execute, record
from tests.test_engine_runs import ALL, spec, store

if TYPE_CHECKING:
    from tape.catalog.store import Store

__all__ = ["store"]

COSTS = FillModel(slippage_bps=2.0, commission_per_share=0.005).model_dump()


def test_state_machine() -> None:
    assert state(0, 0, None, None, filled=False) is BarState.FLAT
    assert state(5, 5, 1, 1, filled=False) is BarState.HELD
    assert state(0, 5, None, 1, filled=True) is BarState.OPENED
    assert state(5, 0, 1, None, filled=True) is BarState.CLOSED
    assert state(0, 0, None, None, filled=True) is BarState.ROUND_TRIP
    assert state(5, -5, 1, 2, filled=True) is BarState.REVERSED
    assert state(5, 5, 1, 2, filled=True) is BarState.REOPENED
    assert state(5, 8, 1, 1, filled=True) is BarState.RESIZED


@pytest.mark.parametrize("strategy", ALL, ids=lambda s: f"{s[0]}:{s[1]['timeframe']}")
@pytest.mark.parametrize("costs", [False, True], ids=["free", "costs"])
def test_ledger_reconciles_with_the_engine(
    store: Store, strategy: tuple[str, dict[str, object]], costs: bool
) -> None:
    s = spec(strategy, **({"fill": COSTS} if costs else {})).model_copy(update={"ticker": Ticker("SPY")})
    result = execute(store, s)
    rec = record(store, s)
    t = rec.table
    assert t.schema.equals(tables.ledger([f for f in t.schema.names if f.startswith(("sma_", "ema_"))]))
    assert rec.net_pnl == pytest.approx(result.metrics.net_pnl, abs=1e-6)
    assert len(t) == len(result.bars)
    assert np.array_equal(t["time"].to_numpy(), result.bars["time"].to_numpy())
    equity = t["equity"].to_numpy()
    assert equity == pytest.approx(result.bars["equity"].to_numpy(), abs=1e-6)
    cash = s.execution.initial_cash
    realized = np.cumsum(t["realized_pnl"].to_numpy(zero_copy_only=False))
    commission = np.cumsum(t["commission"].to_numpy(zero_copy_only=False))
    unrealized = t["unrealized_pnl"].to_numpy(zero_copy_only=False)
    assert equity == pytest.approx(cash + realized - commission + unrealized, abs=1e-6)
    assert t["cumulative_pnl"].to_numpy() == pytest.approx(equity - cash)
    states = [BarState(v) for v in t["state"].to_pylist()]
    positions = t["position"].to_pylist()
    for st, q in zip(states, positions, strict=True):
        assert (q != 0) == (st in IN_POSITION)
    fills = sum(len(e.split(" | ")) for e in t["event"].to_pylist() if e)
    assert fills == len(result.fills)
    trades = {v for v in t["trade"].to_pylist() if v is not None}
    assert trades == set(rec.trades)
    assert sum(x.net_pnl for x in rec.trades.values() if not x.open) == pytest.approx(
        float(result.trades.loc[~result.trades["open"], "net_pnl"].sum()), abs=1e-6
    )
