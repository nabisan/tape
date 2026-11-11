from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from tape.core.types import Notional, Quantity, Timeframe
from tape.engine.orders import Order, OrderSide, OrderType, TimeInForce
from tape.engine.parameters import ParameterSpace

if TYPE_CHECKING:
    from tape.core.types import F64
    from tape.engine.backtest import Context
    from tape.engine.orders import Fill
    from tape.features.kinds import FeatureSpec


class Direction(StrEnum):
    LONG_SHORT = "long_short"
    LONG_ONLY = "long_only"
    SHORT_ONLY = "short_only"

    def allows(self, sign: int) -> bool:
        match self:
            case Direction.LONG_SHORT:
                return True
            case Direction.LONG_ONLY:
                return sign >= 0
            case Direction.SHORT_ONLY:
                return sign <= 0


class Sizing(StrEnum):
    NOTIONAL = "notional"
    SHARES = "shares"


class StrategyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: Timeframe = Timeframe.D1
    direction: Direction = Direction.LONG_SHORT
    sizing: Sizing = Sizing.NOTIONAL
    notional: Annotated[
        Notional, Field(json_schema_extra={"when": {"sizing": "notional"}, "step": 10_000})
    ] = 100_000.0
    shares: Annotated[Quantity, Field(json_schema_extra={"when": {"sizing": "shares"}, "step": 10})] = 100


@dataclass(frozen=True, slots=True)
class Bar:
    index: int
    time_ns: int
    session: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class Indicator:
    __slots__ = ("_ctx", "_values", "spec")

    def __init__(self, spec: FeatureSpec, values: F64, ctx: Context) -> None:
        self.spec = spec
        self._values = values
        self._ctx = ctx

    @property
    def value(self) -> float:
        return float(self._values[self._ctx.index])

    def ago(self, bars: int) -> float:
        assert bars >= 0
        i = self._ctx.index - bars
        return float(self._values[i]) if i >= 0 else math.nan

    @property
    def ready(self) -> bool:
        return not math.isnan(self.value)


class Strategy[C: StrategyConfig]:
    name: ClassVar[str]
    title: ClassVar[str]
    version: ClassVar[int] = 1
    config_type: ClassVar[type[StrategyConfig]]
    parameters: ClassVar[ParameterSpace] = ParameterSpace()
    intraday: ClassVar[bool] = True

    def __init__(self, config: C) -> None:
        self.config = config
        self._ctx: Context | None = None

    @property
    def ctx(self) -> Context:
        assert self._ctx is not None, "strategy not attached to an engine"
        return self._ctx

    def attach(self, ctx: Context) -> None:
        self._ctx = ctx

    def on_start(self) -> None:
        pass

    def on_bar(self, bar: Bar) -> None:
        pass

    def on_fill(self, fill: Fill) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def indicator(self, spec: FeatureSpec) -> Indicator:
        return Indicator(spec, self.ctx.feature(spec), self.ctx)

    @property
    def position(self) -> int:
        return self.ctx.portfolio.quantity

    @property
    def working_orders(self) -> list[Order]:
        return [o for o in (*self.ctx.working, *self.ctx.incoming) if o.working]

    def opening(self) -> tuple[int, float | None]:
        match self.config.sizing:
            case Sizing.NOTIONAL:
                return 0, self.config.notional
            case Sizing.SHARES:
                return self.config.shares, None

    def submit(
        self,
        side: OrderSide,
        quantity: int,
        *,
        kind: OrderType = OrderType.MARKET,
        price: float | None = None,
        tif: TimeInForce = TimeInForce.GTC,
        tag: str = "",
        notional: float | None = None,
        stop_loss: float | None = None,
        through: bool = False,
    ) -> Order | None:
        if quantity <= 0 and notional is None:
            return None
        return self.ctx.submit(
            side,
            quantity,
            kind=kind,
            price=price,
            tif=tif,
            tag=tag,
            notional=notional,
            stop_loss=stop_loss,
            through=through,
        )

    def enter(self, side: OrderSide, *, close: int = 0, **order: Any) -> Order | None:
        shares, notional = self.opening()
        return self.submit(side, close + shares, notional=notional, **order)

    def cancel_all(self) -> None:
        self.ctx.cancel_all()

    def target(self, sign: int, tag: str = "signal") -> None:
        if not self.config.direction.allows(sign):
            sign = 0
        current = self.position
        if sign == (current > 0) - (current < 0):
            return
        if sign == 0:
            self.submit(OrderSide.of(-current), abs(current), tag=tag)
        else:
            self.enter(OrderSide.of(sign), close=abs(current), tag=tag)

    def flatten(self, tag: str = "flatten") -> None:
        if self.position:
            self.submit(OrderSide.of(-self.position), abs(self.position), tag=tag)
