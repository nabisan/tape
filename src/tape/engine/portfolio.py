from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


class Portfolio:
    __slots__ = ("cash", "closed", "open", "quantity")

    def __init__(self, cash: float) -> None:
        self.cash = cash
        self.quantity = 0
        self.open: Trade | None = None
        self.closed: list[Trade] = []

    def equity(self, mark: float) -> float:
        return self.cash + self.quantity * mark

    def apply(self, fill: Fill) -> None:
        signed = fill.side.sign * fill.quantity
        self.cash -= signed * fill.price + fill.commission
        remaining = fill.quantity
        if self.open is not None and fill.side.sign != self.open.sign:
            remaining = self._reduce(self.open, fill)
        if remaining:
            share = fill.commission * remaining / fill.quantity
            self._increase(fill, remaining, share)
        self.quantity += signed

    def _reduce(self, t: Trade, fill: Fill) -> int:
        q = min(fill.quantity, t.quantity)
        t.realized += t.sign * q * (fill.price - t.entry_price)
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
        return fill.quantity - q

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
