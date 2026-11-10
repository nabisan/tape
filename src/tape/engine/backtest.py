from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tape.catalog.bars import last_minutes
from tape.core.types import NS_PER_MINUTE
from tape.engine.matching import FillModel, MatchingEngine
from tape.engine.orders import Fill, Order, OrderSide, OrderStatus, OrderType, TimeInForce
from tape.engine.portfolio import Portfolio, Trade
from tape.engine.strategy import Bar

if TYPE_CHECKING:
    from collections.abc import Callable

    from tape.calendar import Sessions
    from tape.catalog.bars import Bars
    from tape.core.types import F64, I32, I64
    from tape.engine.strategy import Strategy, StrategyConfig
    from tape.features.kinds import FeatureSpec

ENGINE_VERSION = 1

type Stored = Callable[[FeatureSpec], F64 | None]


class ExecutionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_cash: float = Field(default=100_000.0, gt=0)
    fill: FillModel = FillModel()
    intraday: bool = False
    entry_delay_minutes: int = Field(default=0, ge=0)
    squareoff_minutes_before_close: int = Field(default=5, ge=1)


class Context:
    __slots__ = (
        "_features",
        "allow_entries",
        "bars",
        "incoming",
        "index",
        "now_ns",
        "orders",
        "portfolio",
        "session",
        "stored",
        "working",
    )

    def __init__(self, bars: Bars, initial_cash: float, stored: Stored | None = None) -> None:
        self.bars = bars
        self.stored = stored
        self.portfolio = Portfolio(initial_cash)
        self.orders: list[Order] = []
        self.incoming: list[Order] = []
        self.working: list[Order] = []
        self.index = 0
        self.now_ns = 0
        self.session = 0
        self.allow_entries = True
        self._features: dict[str, tuple[FeatureSpec, F64]] = {}

    def feature(self, spec: FeatureSpec) -> F64:
        if spec.id not in self._features:
            values = self.stored(spec) if self.stored is not None else None
            if values is None or len(values) != len(self.bars):
                values = spec.compute(self.bars)
            self._features[spec.id] = (spec, values)
        return self._features[spec.id][1]

    def features(self) -> list[tuple[FeatureSpec, F64]]:
        return list(self._features.values())

    def submit(
        self,
        side: OrderSide,
        quantity: int,
        *,
        kind: OrderType = OrderType.MARKET,
        price: float | None = None,
        tif: TimeInForce = TimeInForce.GTC,
        tag: str = "",
    ) -> Order:
        order = Order(len(self.orders) + 1, side, kind, quantity, tif, self.now_ns, self.session, price, tag)
        self.orders.append(order)
        held = self.portfolio.quantity
        increases = held == 0 or (side.sign > 0) == (held > 0) or quantity > abs(held)
        if increases and not self.allow_entries:
            order.status = OrderStatus.REJECTED
            order.closed_ns = self.now_ns
            order.reason = "outside entry window"
        else:
            self.incoming.append(order)
        return order

    def cancel_all(self) -> None:
        for o in (*self.working, *self.incoming):
            if o.working:
                o.status = OrderStatus.CANCELED
                o.closed_ns = self.now_ns
        self.working.clear()
        self.incoming.clear()

    def expire_day_orders(self) -> None:
        for o in (*self.working, *self.incoming):
            if o.working and o.tif is TimeInForce.DAY:
                o.status = OrderStatus.EXPIRED
                o.closed_ns = self.now_ns
        self.prune()

    def prune(self) -> None:
        self.working = [o for o in self.working if o.working]
        self.incoming = [o for o in self.incoming if o.working]


@dataclass(frozen=True, slots=True)
class SessionMark:
    session: I32
    equity: F64
    position: I64
    close: F64


@dataclass(frozen=True, slots=True)
class Run:
    orders: list[Order]
    fills: list[Fill]
    trades: list[Trade]
    sessions: SessionMark
    bar_time_ns: I64
    bar_equity: F64
    first_bar: int
    last_bar: int


def window(decision: Bars, first_session: int, last_session: int) -> tuple[int, int]:
    k0 = int(np.searchsorted(decision.session, first_session, side="left"))
    k1 = int(np.searchsorted(decision.session, last_session, side="right")) - 1
    if k1 < k0:
        raise ValueError(f"no bars between sessions {first_session} and {last_session}")
    return k0, k1


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy[StrategyConfig],
        *,
        m1: Bars,
        decision: Bars,
        sessions: Sessions,
        execution: ExecutionSpec,
        fast_forward: bool = True,
        stored: Stored | None = None,
    ) -> None:
        ends = last_minutes(m1, sessions, strategy.config.timeframe)
        assert len(ends) == len(decision)
        self.strategy = strategy
        self.execution = execution
        self.ends = ends
        self.decision = decision
        self.ctx = Context(decision, execution.initial_cash, stored)
        self.matcher = MatchingEngine(execution.fill)
        self.fills: list[Fill] = []
        self.marks: tuple[list[int], list[float], list[int], list[float]] = ([], [], [], [])
        self.bar_time: list[int] = []
        self.bar_equity: list[float] = []
        self.squared = -1

        self.time = m1.time_ns.tolist()
        self.sess = m1.session.tolist()
        self.ohlc = list(
            zip(m1.open.tolist(), m1.high.tolist(), m1.low.tolist(), m1.close.tolist(), strict=True)
        )
        self.session_end = np.r_[m1.session[1:] != m1.session[:-1], True].tolist()
        decision_end = np.zeros(len(m1), dtype=bool)
        decision_end[ends] = True
        self.decision_end = decision_end.tolist()
        self.entry_from = (sessions.open_ns + execution.entry_delay_minutes * NS_PER_MINUTE).tolist()
        squareoff_ns = sessions.close_ns - execution.squareoff_minutes_before_close * NS_PER_MINUTE
        self.squareoff = squareoff_ns.tolist()

        self.fast_forward = fast_forward
        self.high, self.low = m1.high, m1.low
        boundary = decision_end | np.r_[m1.session[1:] != m1.session[:-1], True]
        if execution.intraday:
            late = m1.time_ns >= squareoff_ns[m1.session]
            opening = np.r_[True, m1.session[1:] != m1.session[:-1]]
            boundary |= late & (opening | ~np.r_[False, late[:-1]])
        marks = np.flatnonzero(boundary)
        self.next_boundary: list[int] = marks[np.searchsorted(marks, np.arange(len(m1)))].tolist()

    def run(self, first_session: int, last_session: int) -> Run:
        k0, k1 = window(self.decision, first_session, last_session)
        j0 = int(self.ends[k0 - 1]) + 1 if k0 else 0
        j1 = int(self.ends[k1])
        self.strategy.attach(self.ctx)
        self.strategy.on_start()
        k = k0
        intraday = self.execution.intraday
        j = j0
        while j <= j1:
            ctx = self.ctx
            if ctx.incoming:
                ctx.working.extend(ctx.incoming)
                ctx.incoming.clear()
            if self.fast_forward and ctx.working:
                j = self._next_event(j)
            t, s = self.time[j], self.sess[j]
            self.ctx.session = s
            if intraday and self.squared != s and t >= self.squareoff[s]:
                self._square_off(s, t, self.ohlc[j][0])
            if ctx.working:
                for f in self.matcher.process(ctx.working, t, self.ohlc[j]):
                    self._book(f)
                ctx.working = [w for w in ctx.working if w.working]
            if self.decision_end[j]:
                self._decision_bar(k, j, t, s)
                k += 1
            if self.session_end[j]:
                self._session_end(j, t, s)
            j += 1
        self.strategy.on_stop()
        return self._result(k0, k1)

    def _next_event(self, j: int) -> int:
        b = self.next_boundary[j]
        working = self.ctx.working
        if b == j or any(w.type is OrderType.MARKET for w in working):
            return j
        hit = b
        for w in working:
            assert w.price is not None
            rising = (w.side is OrderSide.BUY) == (w.type is OrderType.STOP_MARKET)
            reached = self.high[j:hit] >= w.price if rising else self.low[j:hit] <= w.price
            if reached.any():
                hit = j + int(reached.argmax())
        return hit

    def _book(self, f: Fill) -> None:
        self.fills.append(f)
        self.ctx.portfolio.apply(f)
        self.strategy.on_fill(f)

    def _square_off(self, s: int, t: int, price: float) -> None:
        self.squared = s
        self.ctx.now_ns = t
        self.ctx.cancel_all()
        held = self.ctx.portfolio.quantity
        if held:
            order = Order(
                len(self.ctx.orders) + 1,
                OrderSide.of(-held),
                OrderType.MARKET,
                abs(held),
                TimeInForce.DAY,
                t,
                s,
                tag="squareoff",
            )
            self.ctx.orders.append(order)
            self._book(self.matcher.market(order, t, price))

    def _decision_bar(self, k: int, j: int, t: int, s: int) -> None:
        now = t + NS_PER_MINUTE
        ctx, d = self.ctx, self.decision
        ctx.index, ctx.now_ns = k, now
        ctx.allow_entries = not self.execution.intraday or self.entry_from[s] <= now < self.squareoff[s]
        self.bar_time.append(int(d.time_ns[k]))
        self.bar_equity.append(ctx.portfolio.equity(self.ohlc[j][3]))
        bar = Bar(
            k,
            int(d.time_ns[k]),
            s,
            float(d.open[k]),
            float(d.high[k]),
            float(d.low[k]),
            float(d.close[k]),
            int(d.volume[k]),
        )
        self.strategy.on_bar(bar)

    def _session_end(self, j: int, t: int, s: int) -> None:
        close = self.ohlc[j][3]
        self.ctx.now_ns = t + NS_PER_MINUTE
        self.ctx.expire_day_orders()
        if self.execution.intraday and self.squared != s:
            self._square_off(s, t, close)
        sessions, equity, position, closes = self.marks
        sessions.append(s)
        equity.append(self.ctx.portfolio.equity(close))
        position.append(self.ctx.portfolio.quantity)
        closes.append(close)

    def _result(self, k0: int, k1: int) -> Run:
        p = self.ctx.portfolio
        sessions, equity, position, closes = self.marks
        return Run(
            orders=self.ctx.orders,
            fills=self.fills,
            trades=[*p.closed, *([p.open] if p.open else [])],
            sessions=SessionMark(
                np.asarray(sessions, dtype=np.int32),
                np.asarray(equity, dtype=np.float64),
                np.asarray(position, dtype=np.int64),
                np.asarray(closes, dtype=np.float64),
            ),
            bar_time_ns=np.asarray(self.bar_time, dtype=np.int64),
            bar_equity=np.asarray(self.bar_equity, dtype=np.float64),
            first_bar=k0,
            last_bar=k1,
        )
