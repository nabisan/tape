from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from tape.core.instruments import AssetClass, Conversion
from tape.engine.orders import OrderOrigin

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tape.core.types import Ticker
    from tape.engine.backtest import BacktestEngine, Gate, Run
    from tape.engine.margin import MarginSpec
    from tape.engine.orders import Order

SHORT_OF_MARGIN: Final = "insufficient margin"


@dataclass(frozen=True, slots=True)
class AccountReport:
    liquidated_ns: int | None
    liquidations: int
    rejections: int
    max_locked: float
    min_free: float


@dataclass(frozen=True, slots=True)
class BookRun:
    sleeves: dict[Ticker, Run]
    account: AccountReport | None


def opening(engine: BacktestEngine, order: Order) -> tuple[float, bool]:
    held = engine.ctx.portfolio.quantity
    same = held == 0 or (order.side.sign > 0) == (held > 0)
    if order.notional is not None:
        return order.notional, not same
    quantity = order.quantity if same else max(0, order.quantity - abs(held))
    if quantity == 0:
        return 0.0, False
    price = order.price if order.price is not None else engine.last_close
    return engine.ctx.portfolio.valuation.notional(quantity, price), not same


def wake_price(engine: BacktestEngine, share: float, mark: float) -> tuple[float, bool] | None:
    p = engine.ctx.portfolio
    if p.open is None or share <= 0:
        return None
    q, entry = p.quantity, p.open.entry_price
    k = q * p.valuation.instrument.multiplier
    target = p.valuation.pnl(q, entry, mark) - share
    match p.valuation.conversion:
        case Conversion.IDENTITY:
            level = entry + target / k
        case Conversion.RECIPROCAL:
            level = k * entry / (k - target) if k != target else math.nan
    if not math.isfinite(level) or level <= 0:
        return None
    return level, q > 0


class SharedAccount:
    __slots__ = ("engines", "liquidated_ns", "liquidations", "max_locked", "min_free", "rejections", "spec")

    def __init__(self, engines: list[BacktestEngine], spec: MarginSpec) -> None:
        for e in engines:
            if e.ctx.portfolio.valuation.instrument.asset_class is not AssetClass.FX:
                raise ValueError("a shared margin account covers FX pairs only for now")
        self.engines = engines
        self.spec = spec
        self.liquidated_ns: int | None = None
        self.liquidations = 0
        self.rejections = 0
        self.max_locked = 0.0
        self.min_free = math.inf

    def balance(self) -> float:
        return sum(
            e.ctx.portfolio.equity(e.last_close) - e.ctx.portfolio.unrealized(e.last_close)
            for e in self.engines
        )

    def equity(self, now: int | None = None) -> float:
        return sum(
            e.ctx.portfolio.equity(e.last_close if now is None else e.mark_at(now)) for e in self.engines
        )

    def maintenance(self, engine: BacktestEngine) -> float:
        p = engine.ctx.portfolio
        if p.open is None or not p.quantity:
            return 0.0
        return self.spec.maintenance_margin(p.valuation.notional(abs(p.quantity), p.open.entry_price))

    def locked(self, release: BacktestEngine | None = None) -> float:
        total = 0.0
        for e in self.engines:
            if e is not release:
                total += self.maintenance(e)
            for o in (*e.ctx.incoming, *e.ctx.working):
                if o.working and o.origin is OrderOrigin.STRATEGY:
                    total += self.spec.initial(opening(e, o)[0])
        return total

    def free(self, release: BacktestEngine | None = None) -> float:
        return max(0.0, self.balance() - self.locked(release))

    def gate(self, engine: BacktestEngine) -> Gate:
        def check(order: Order) -> str | None:
            notional, flips = opening(engine, order)
            if notional <= 0:
                return None
            if self.spec.initial(notional) > self.free(engine if flips else None):
                self.rejections += 1
                return SHORT_OF_MARGIN
            return None

        return check

    def watch(self, now: int) -> None:
        locked = self.locked()
        self.max_locked = max(self.max_locked, locked)
        self.min_free = min(self.min_free, max(0.0, self.balance() - locked))
        held = [e for e in self.engines if e.ctx.portfolio.quantity]
        for e in self.engines:
            e.wake = None
        if not held:
            return
        maintenance = sum(self.maintenance(e) for e in held)
        cushion = self.equity(now) - maintenance
        if cushion <= 0:
            for e in self.engines:
                e.liquidate(now)
            self.liquidations += 1
            if self.liquidated_ns is None:
                self.liquidated_ns = now
            return
        share = cushion / len(held)
        for e in held:
            e.wake = wake_price(e, share, e.mark_at(now))

    def report(self) -> AccountReport:
        return AccountReport(
            liquidated_ns=self.liquidated_ns,
            liquidations=self.liquidations,
            rejections=self.rejections,
            max_locked=self.max_locked,
            min_free=self.min_free if math.isfinite(self.min_free) else self.balance(),
        )


class BookEngine:
    __slots__ = ("account", "sleeves")

    def __init__(self, sleeves: Mapping[Ticker, BacktestEngine], margin: MarginSpec | None = None) -> None:
        if not sleeves:
            raise ValueError("a book needs at least one sleeve")
        self.sleeves = sorted(sleeves.items())
        engines = [e for _, e in self.sleeves]
        self.account = SharedAccount(engines, margin) if margin is not None else None
        if self.account is not None:
            for e in engines:
                e.ctx.gate = self.account.gate(e)

    def run(self, first_session: int, last_session: int) -> BookRun:
        for _, engine in self.sleeves:
            engine.begin(first_session, last_session)
        while True:
            due = [(t, engine) for _, engine in self.sleeves if (t := engine.pending()) is not None]
            if not due:
                break
            now = min(t for t, _ in due)
            for t, engine in due:
                if t == now:
                    engine.advance()
            if self.account is not None:
                self.account.watch(now)
            for _, engine in self.sleeves:
                engine.floor = bisect_right(engine.time, now)
        runs = {ticker: engine.finish() for ticker, engine in self.sleeves}
        return BookRun(runs, self.account.report() if self.account is not None else None)
