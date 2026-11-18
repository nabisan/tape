from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tape.core.types import Ticker


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    AUD = "AUD"
    CAD = "CAD"
    CHF = "CHF"
    NZD = "NZD"


ACCOUNT: Final = Currency.USD


class AssetClass(StrEnum):
    EQUITY = "equity"
    FX = "fx"

    @property
    def unit(self) -> str:
        match self:
            case AssetClass.EQUITY:
                return "share"
            case AssetClass.FX:
                return "unit"


class InstrumentClass(StrEnum):
    SPOT = "spot"


class InstrumentError(ValueError):
    pass


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Ticker
    asset_class: AssetClass
    instrument_class: InstrumentClass = InstrumentClass.SPOT
    quote_currency: Currency
    base_currency: Currency | None = None
    multiplier: Annotated[float, Field(gt=0)] = 1.0
    price_precision: Annotated[int, Field(ge=0, le=9)]

    @model_validator(mode="after")
    def _currencies(self) -> Self:
        match self.asset_class:
            case AssetClass.EQUITY:
                if self.base_currency is not None:
                    raise InstrumentError(f"{self.symbol}: an equity has no base currency")
            case AssetClass.FX:
                if self.base_currency is None or self.base_currency is self.quote_currency:
                    raise InstrumentError(f"{self.symbol}: a currency pair needs two different currencies")
        return self

    def notional(self, quantity: int, price: float) -> float:
        return quantity * price * self.multiplier

    def pnl(self, signed_quantity: int, entry: float, mark: float) -> float:
        return signed_quantity * (mark - entry) * self.multiplier


class Conversion(StrEnum):
    IDENTITY = "identity"
    RECIPROCAL = "reciprocal"

    def convert(self, amount: float, price: float) -> float:
        match self:
            case Conversion.IDENTITY:
                return amount
            case Conversion.RECIPROCAL:
                return amount / price

    def growth(self, entry: float, mark: float) -> float:
        match self:
            case Conversion.IDENTITY:
                return mark / entry - 1
            case Conversion.RECIPROCAL:
                return 1 - entry / mark

    def unit_value(self, price: float, multiplier: float) -> float:
        match self:
            case Conversion.IDENTITY:
                return price * multiplier
            case Conversion.RECIPROCAL:
                return multiplier


def conversion(instrument: Instrument, account: Currency = ACCOUNT) -> Conversion:
    if instrument.quote_currency is account:
        return Conversion.IDENTITY
    if instrument.base_currency is account:
        return Conversion.RECIPROCAL
    raise InstrumentError(
        f"{instrument.symbol}: no {instrument.quote_currency} to {account} rate; "
        "cross rates are not supported yet"
    )


@dataclass(frozen=True, slots=True)
class Valuation:
    instrument: Instrument
    conversion: Conversion

    @classmethod
    def of(cls, instrument: Instrument, account: Currency = ACCOUNT) -> Valuation:
        return cls(instrument, conversion(instrument, account))

    @property
    def cash_settled(self) -> bool:
        return (
            self.conversion is Conversion.IDENTITY
            and self.instrument.instrument_class is InstrumentClass.SPOT
        )

    def units(self, notional: float, price: float) -> int:
        return math.floor(notional / self.conversion.unit_value(price, self.instrument.multiplier))

    def notional(self, quantity: int, price: float) -> float:
        return self.conversion.convert(self.instrument.notional(quantity, price), price)

    def pnl(self, signed_quantity: int, entry: float, mark: float) -> float:
        return self.conversion.convert(self.instrument.pnl(signed_quantity, entry, mark), mark)


def equity(symbol: Ticker) -> Instrument:
    return Instrument(
        symbol=symbol, asset_class=AssetClass.EQUITY, quote_currency=Currency.USD, price_precision=2
    )
