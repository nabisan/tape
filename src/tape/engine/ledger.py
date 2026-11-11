from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from tape.engine.orders import OrderOrigin, OrderSide

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tape.engine.backtest import Context
    from tape.engine.orders import Fill, Order
    from tape.engine.portfolio import Portfolio, Trade


class BarState(StrEnum):
    FLAT = "flat"
    OPENED = "opened"
    HELD = "held"
    RESIZED = "resized"
    CLOSED = "closed"
    REOPENED = "reopened"
    REVERSED = "reversed"
    ROUND_TRIP = "round trip"


IN_POSITION: Final = frozenset(
    {BarState.OPENED, BarState.HELD, BarState.RESIZED, BarState.REOPENED, BarState.REVERSED}
)
ORIGIN_LABEL: Final = {OrderOrigin.STOP_LOSS: "stop-loss", OrderOrigin.SQUARE_OFF: "square-off"}


@dataclass(frozen=True, slots=True)
class Mark:
    bar: int
    time_ns: int
    close: float
    state: BarState
    trade: int | None
    position: int
    avg_cost: float
    buy_trigger: float
    sell_trigger: float
    stop: float
    fills: tuple[Fill, ...]
    realized: float
    commission: float
    unrealized: float
    equity: float


@dataclass(frozen=True, slots=True)
class TradeSummary:
    id: int
    long: bool
    quantity: int
    entry_price: float
    exit_price: float
    net_pnl: float
    open: bool


def state(
    before: int, after: int, before_trade: int | None, after_trade: int | None, filled: bool
) -> BarState:
    if not filled:
        return BarState.FLAT if after == 0 else BarState.HELD
    if before == 0:
        return BarState.ROUND_TRIP if after == 0 else BarState.OPENED
    if after == 0:
        return BarState.CLOSED
    if (before > 0) != (after > 0):
        return BarState.REVERSED
    return BarState.REOPENED if before_trade != after_trade else BarState.RESIZED


def _trigger(orders: Sequence[Order], side: OrderSide) -> float:
    prices = [
        o.price
        for o in orders
        if o.working and o.origin is OrderOrigin.STRATEGY and o.side is side and o.price is not None
    ]
    return (min(prices) if side is OrderSide.BUY else max(prices)) if prices else math.nan


def _stop(orders: Sequence[Order]) -> float:
    prices = [
        o.price for o in orders if o.working and o.origin is OrderOrigin.STOP_LOSS and o.price is not None
    ]
    return prices[-1] if prices else math.nan


def event(fill: Fill, order: Order) -> str:
    label = ORIGIN_LABEL.get(order.origin) or fill.tag.replace("_", " ") or order.type.value.replace("_", " ")
    return f"{fill.side.value.upper()} {fill.quantity:,} @ {fill.price:,.4f} · {label}"


def summarize(t: Trade) -> TradeSummary:
    return TradeSummary(
        id=t.id,
        long=t.sign > 0,
        quantity=t.max_quantity,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        net_pnl=t.realized - t.commission,
        open=not t.closed,
    )


class Ledger:
    __slots__ = (
        "_closed",
        "_closed_sum",
        "_cumulative",
        "_last_closed",
        "_position",
        "_seen",
        "_trade",
        "marks",
    )

    def __init__(self) -> None:
        self.marks: list[Mark] = []
        self._seen = 0
        self._position = 0
        self._trade: int | None = None
        self._closed = 0
        self._closed_sum = 0.0
        self._cumulative = 0.0
        self._last_closed: int | None = None

    def _realized(self, p: Portfolio) -> float:
        for t in p.closed[self._closed :]:
            self._closed_sum += t.realized
            self._last_closed = t.id
        self._closed = len(p.closed)
        return self._closed_sum + (p.open.realized if p.open is not None else 0.0)

    def mark(self, bar: int, time_ns: int, close: float, fills: Sequence[Fill], ctx: Context) -> None:
        p = ctx.portfolio
        new = tuple(fills[self._seen :])
        self._seen = len(fills)
        cumulative = self._realized(p)
        trade = p.open.id if p.open is not None else None
        working = (*ctx.working, *ctx.incoming)
        avg = p.open.entry_price if p.open is not None else math.nan
        self.marks.append(
            Mark(
                bar=bar,
                time_ns=time_ns,
                close=close,
                state=state(self._position, p.quantity, self._trade, trade, bool(new)),
                trade=trade if trade is not None else (self._last_closed if new else None),
                position=p.quantity,
                avg_cost=avg,
                buy_trigger=_trigger(working, OrderSide.BUY),
                sell_trigger=_trigger(working, OrderSide.SELL),
                stop=_stop(working),
                fills=new,
                realized=cumulative - self._cumulative,
                commission=sum(f.commission for f in new),
                unrealized=p.quantity * (close - avg) if p.open is not None else 0.0,
                equity=p.equity(close),
            )
        )
        self._cumulative = cumulative
        self._position, self._trade = p.quantity, trade

    def settle(self, fills: Sequence[Fill], ctx: Context) -> None:
        if not self.marks or self._seen == len(fills):
            return
        last = self.marks.pop()
        self._seen -= len(last.fills)
        self._cumulative -= last.realized
        prior = self.marks[-1] if self.marks else None
        self._position = prior.position if prior is not None else 0
        self._trade = prior.trade if prior is not None and prior.position else None
        self.mark(last.bar, last.time_ns, last.close, fills, ctx)

    def trades(self, p: Portfolio) -> dict[int, TradeSummary]:
        return {t.id: summarize(t) for t in (*p.closed, *([p.open] if p.open is not None else []))}
