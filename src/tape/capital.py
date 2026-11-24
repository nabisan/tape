from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

from tape.core.instruments import Valuation
from tape.engine.orders import Fill, OrderSide
from tape.engine.portfolio import Portfolio
from tape.service.results import Table

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tape.catalog.store import Store
    from tape.core.hashing import Digest
    from tape.core.types import F64, I64, Ticker
    from tape.service.results import ResultStore

NEVER: Final = np.iinfo(np.int64).max


@dataclass(frozen=True, slots=True)
class Usage:
    avg_used: float
    max_used: float
    avg_overnight: float
    used: pd.Series


def cost_path(fills: pd.DataFrame, valuation: Valuation, cash: float) -> tuple[I64, F64]:
    portfolio = Portfolio(cash, valuation)
    times = fills["time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
    cost = np.zeros(len(fills), dtype=np.float64)
    sides = [OrderSide(v) for v in fills["side"].astype(str)]
    quantities = fills["quantity"].to_numpy(dtype=np.int64).tolist()
    prices = fills["price"].to_numpy(dtype=np.float64).tolist()
    fees = fills["commission"].to_numpy(dtype=np.float64).tolist()
    for i, (side, quantity, p, fee) in enumerate(zip(sides, quantities, prices, fees, strict=True)):
        portfolio.apply(Fill(i + 1, int(times[i]), side, quantity, p, fee, "", p))
        if portfolio.open is not None:
            cost[i] = valuation.notional(abs(portfolio.quantity), portfolio.open.entry_price)
    return times, cost


def held_before(times: I64, cost: F64, at: I64) -> F64:
    last = np.searchsorted(times, at, side="left") - 1
    return np.where(last >= 0, cost[np.maximum(last, 0)], 0.0)


def usage(results: ResultStore, key: Digest, store: Store, ticker: Ticker, cash: float) -> Usage:
    valuation = Valuation.of(store.instrument(ticker))
    times, cost = cost_path(results.frame(key, Table.FILLS), valuation, cash)
    stamps = results.frame(key, Table.BARS)["time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
    used = pd.Series(held_before(times, cost, np.r_[stamps[1:], NEVER]), index=pd.to_datetime(stamps))
    days = results.frame(key, Table.EQUITY)["date"].to_numpy(dtype="datetime64[D]")
    sessions = store.sessions()
    inside = (sessions.day >= days[0]) & (sessions.day <= days[-1])
    overnight = held_before(times, cost, sessions.close_ns[inside])
    return Usage(
        avg_used=float(used.mean()),
        max_used=float(used.max()),
        avg_overnight=float(overnight.mean()) if len(overnight) else 0.0,
        used=used,
    )


def combined_max(used: Mapping[Ticker, pd.Series]) -> float:
    return float(pd.DataFrame(dict(used)).sort_index().ffill().fillna(0.0).sum(axis=1).max())
