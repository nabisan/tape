from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tape.engine.orders import OrderSide
from tape.service.results import Table, thin

if TYPE_CHECKING:
    import pandas as pd

    from tape.core.hashing import Digest
    from tape.service.results import ResultStore

OHLCV: Final = frozenset({"time", "open", "high", "low", "close", "volume", "equity"})
POINTS: Final = 2_500
MAX_POINTS: Final = 6_000
FILL_LIMIT: Final = 4_000
NS_PER_MS: Final = 1_000_000


class FillMark(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    time: int
    side: OrderSide
    price: float
    quantity: int
    tag: str


class PriceWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int
    first: int
    last: int
    factor: int
    index: list[int]
    time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    indicators: dict[str, list[float | None]]
    fills: list[FillMark]
    fills_in_range: int
    fill_limit: int = Field(default=FILL_LIMIT)


def _ms(times: pd.Series) -> list[int]:
    return (times.to_numpy(dtype="datetime64[ns]").view(np.int64) // NS_PER_MS).tolist()


def _values(column: pd.Series) -> list[float | None]:
    return [None if math.isnan(v) else v for v in column.to_numpy(dtype=np.float64).tolist()]


def window(
    bars: pd.DataFrame, fills: pd.DataFrame, first: int | None, last: int | None, points: int
) -> PriceWindow:
    count = len(bars)
    lo = max(0, first if first is not None else 0)
    hi = min(count - 1, last if last is not None else count - 1)
    if count == 0 or hi < lo:
        lo, hi = 0, -1
    budget = max(1, min(points, MAX_POINTS))
    part = bars.iloc[lo : hi + 1].reset_index(drop=True)
    shown, factor = thin(part, budget)
    index = (lo + np.arange(len(shown)) * factor).tolist()
    stamps = bars["time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
    at = (
        np.searchsorted(stamps, fills["time"].to_numpy(dtype="datetime64[ns]").view(np.int64), side="right")
        - 1
    )
    inside = (at >= lo) & (at <= hi)
    marks = fills[inside]
    listed = (
        [
            FillMark(
                index=int(i), time=t, side=OrderSide(str(s)), price=float(p), quantity=int(q), tag=str(g)
            )
            for i, t, s, p, q, g in zip(
                at[inside],
                _ms(marks["time"]),
                marks["side"],
                marks["price"],
                marks["quantity"],
                marks["tag"],
                strict=True,
            )
        ]
        if int(inside.sum()) <= FILL_LIMIT
        else []
    )
    return PriceWindow(
        count=count,
        first=lo,
        last=hi,
        factor=factor,
        index=index,
        time=_ms(shown["time"]),
        open=shown["open"].astype(float).tolist(),
        high=shown["high"].astype(float).tolist(),
        low=shown["low"].astype(float).tolist(),
        close=shown["close"].astype(float).tolist(),
        indicators={c: _values(shown[c]) for c in shown.columns if c not in OHLCV},
        fills=listed,
        fills_in_range=int(inside.sum()),
    )


def price(results: ResultStore, key: Digest, first: int | None, last: int | None, points: int) -> PriceWindow:
    return window(results.frame(key, Table.BARS), results.frame(key, Table.FILLS), first, last, points)
