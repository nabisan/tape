from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from tape.core.instruments import Valuation
    from tape.engine.orders import Fill


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class Trade:
    id: int
    side: PositionSide
    entry_ns: int
    entry_price: float
    quantity: int
    max_quantity: int
    exit_quantity: int = 0
    exit_value: float = 0.0
    realized: float = 0.0
    commission: float = 0.0
    exit_ns: int | None = None
    exit_tag: str = ""
    fills: list[int] = field(default_factory=list)

    @property
    def sign(self) -> int:
        return 1 if self.side is PositionSide.LONG else -1

    @property
    def closed(self) -> bool:
        return self.exit_ns is not None

    @property
    def exit_price(self) -> float:
        return self.exit_value / self.exit_quantity if self.exit_quantity else float("nan")


class Account(Protocol):
    cash: float

    def settle(self, signed: int, price: float, commission: float, realized: float) -> None: ...

    def equity(self, quantity: int, entry: float, mark: float) -> float: ...


class CashAccount:
    __slots__ = ("cash",)

    def __init__(self, cash: float) -> None:
        self.cash = cash

    def settle(self, signed: int, price: float, commission: float, realized: float) -> None:
        self.cash -= signed * price + commission

    def equity(self, quantity: int, entry: float, mark: float) -> float:
        return self.cash + quantity * mark


class MarginAccount:
    __slots__ = ("_valuation", "cash")

    def __init__(self, cash: float, valuation: Valuation) -> None:
        self.cash = cash
        self._valuation = valuation

    def settle(self, signed: int, price: float, commission: float, realized: float) -> None:
        self.cash += realized - commission

    def equity(self, quantity: int, entry: float, mark: float) -> float:
        return self.cash + (self._valuation.pnl(quantity, entry, mark) if quantity else 0.0)


def account(cash: float, valuation: Valuation) -> Account:
    return CashAccount(cash) if valuation.cash_settled else MarginAccount(cash, valuation)


class Portfolio:
    __slots__ = ("account", "closed", "open", "quantity", "valuation")

    def __init__(self, cash: float, valuation: Valuation) -> None:
        self.valuation = valuation
        self.account = account(cash, valuation)
        self.quantity = 0
        self.open: Trade | None = None
        self.closed: list[Trade] = []

    @property
    def cash(self) -> float:
        return self.account.cash

    def equity(self, mark: float) -> float:
        entry = self.open.entry_price if self.open is not None else math.nan
        return self.account.equity(self.quantity, entry, mark)

    def unrealized(self, mark: float) -> float:
        if self.open is None:
            return 0.0
        return self.valuation.pnl(self.quantity, self.open.entry_price, mark)

    def apply(self, fill: Fill) -> None:
        signed = fill.side.sign * fill.quantity
        remaining, realized = fill.quantity, 0.0
        if self.open is not None and fill.side.sign != self.open.sign:
            remaining, realized = self._reduce(self.open, fill)
        self.account.settle(signed, fill.price, fill.commission, realized)
        if remaining:
            share = fill.commission * remaining / fill.quantity
            self._increase(fill, remaining, share)
        self.quantity += signed

    def _reduce(self, t: Trade, fill: Fill) -> tuple[int, float]:
        q = min(fill.quantity, t.quantity)
        realized = self.valuation.pnl(t.sign * q, t.entry_price, fill.price)
        t.realized += realized
        t.commission += fill.commission * q / fill.quantity
        t.exit_quantity += q
        t.exit_value += q * fill.price
        t.quantity -= q
        t.fills.append(fill.order_id)
        if t.quantity == 0:
            t.exit_ns = fill.time_ns
            t.exit_tag = fill.tag
            self.closed.append(t)
            self.open = None
        return fill.quantity - q, realized

    def _increase(self, fill: Fill, quantity: int, commission: float) -> None:
        t = self.open
        if t is None:
            side = PositionSide.LONG if fill.side.sign > 0 else PositionSide.SHORT
            t = Trade(len(self.closed) + 1, side, fill.time_ns, fill.price, 0, 0)
            self.open = t
        t.entry_price = (t.entry_price * t.quantity + fill.price * quantity) / (t.quantity + quantity)
        t.quantity += quantity
        t.max_quantity = max(t.max_quantity, t.quantity)
        t.commission += commission
        t.fills.append(fill.order_id)
