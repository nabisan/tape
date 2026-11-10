from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tape.core.types import Bps, PerShare, Probability
from tape.engine.orders import Fill, Order, OrderSide, OrderStatus, OrderType

if TYPE_CHECKING:
    from collections.abc import Iterator


type OHLC = tuple[float, float, float, float]


class FillModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commission_per_share: PerShare = 0.005
    min_commission: float = Field(default=0.0, ge=0)
    slippage_bps: Bps = 1.0
    prob_fill_on_limit: Probability = 1.0
    adaptive_high_low: bool = True
    seed: int = 0

    def commission(self, quantity: int) -> float:
        return max(self.min_commission, self.commission_per_share * quantity)

    def slipped(self, price: float, side: OrderSide) -> float:
        return price * (1 + side.sign * self.slippage_bps / 1e4)


class MatchingEngine:
    __slots__ = ("_rng", "model")

    def __init__(self, model: FillModel) -> None:
        self.model = model
        self._rng = np.random.default_rng(model.seed)

    def path(self, bar: OHLC) -> OHLC:
        o, h, lo, c = bar
        high_first = not self.model.adaptive_high_low or abs(h - o) < abs(lo - o)
        return (o, h, lo, c) if high_first else (o, lo, h, c)

    def market(self, order: Order, time_ns: int, price: float) -> Fill | None:
        return self._fill(order, time_ns, price, self.model.slipped(price, order.side))

    def process(self, orders: list[Order], time_ns: int, bar: OHLC) -> Iterator[Fill]:
        for i, p in enumerate(self.path(bar)):
            for order in orders:
                if not order.working:
                    continue
                touched = self._touch(order, p, gap=i == 0)
                if touched is not None and (fill := self._fill(order, time_ns, *touched)) is not None:
                    yield fill

    def _touch(self, order: Order, p: float, gap: bool) -> tuple[float, float] | None:
        buy = order.side is OrderSide.BUY
        match order.type:
            case OrderType.MARKET:
                return p, self.model.slipped(p, order.side)
            case OrderType.LIMIT:
                assert order.price is not None
                through = p < order.price if buy else p > order.price
                touched = p == order.price and self._rng.random() < self.model.prob_fill_on_limit
                if through or touched:
                    level = p if gap and through else order.price
                    return level, level
                return None
            case OrderType.STOP_MARKET:
                assert order.price is not None
                if p >= order.price if buy else p <= order.price:
                    level = p if gap else order.price
                    return level, self.model.slipped(level, order.side)
                return None

    def _fill(self, order: Order, time_ns: int, reference: float, price: float) -> Fill | None:
        quantity = order.resolve(reference)
        order.closed_ns = time_ns
        if quantity <= 0:
            order.status = OrderStatus.REJECTED
            order.reason = "notional below one share"
            return None
        order.status = OrderStatus.FILLED
        order.quantity = quantity
        order.fill_price = price
        return Fill(
            order_id=order.id,
            time_ns=time_ns,
            side=order.side,
            quantity=quantity,
            price=price,
            commission=self.model.commission(quantity),
            tag=order.tag,
        )
