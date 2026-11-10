from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1

    @staticmethod
    def of(delta: int) -> OrderSide:
        return OrderSide.BUY if delta > 0 else OrderSide.SELL


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"


class TimeInForce(StrEnum):
    GTC = "gtc"
    DAY = "day"


class OrderStatus(StrEnum):
    ACCEPTED = "accepted"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


@dataclass(slots=True)
class Order:
    id: int
    side: OrderSide
    type: OrderType
    quantity: int
    tif: TimeInForce
    submitted_ns: int
    session: int
    price: float | None = None
    tag: str = ""
    status: OrderStatus = OrderStatus.ACCEPTED
    closed_ns: int | None = None
    fill_price: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        assert self.quantity > 0
        assert (self.price is None) == (self.type is OrderType.MARKET)

    @property
    def working(self) -> bool:
        return self.status is OrderStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: int
    time_ns: int
    side: OrderSide
    quantity: int
    price: float
    commission: float
    tag: str
