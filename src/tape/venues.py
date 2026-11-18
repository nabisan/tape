from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from tape import calendar
from tape.core.instruments import AssetClass, Currency, Instrument, InstrumentError, equity
from tape.core.types import Ticker

if TYPE_CHECKING:
    from datetime import date

    from tape.calendar import Sessions

PAIR: Final = re.compile(r"[A-Z]{6}")
FX_PRICE_PRECISION: Final = 5
FX_JPY_PRICE_PRECISION: Final = 3


class VolumeKind(StrEnum):
    TRADED = "traded"
    TICKS = "ticks"


class Venue(StrEnum):
    XNYS = "XNYS"
    FX = "FX"

    @property
    def label(self) -> str:
        match self:
            case Venue.XNYS:
                return "US equities"
            case Venue.FX:
                return "FX"

    @property
    def zone(self) -> str:
        match self:
            case Venue.XNYS:
                return calendar.ZONE
            case Venue.FX:
                return "UTC"

    @property
    def library(self) -> str:
        match self:
            case Venue.XNYS:
                return calendar.LIBRARY
            case Venue.FX:
                return calendar.FX_LIBRARY

    @property
    def reference(self) -> Ticker:
        match self:
            case Venue.XNYS:
                return Ticker("SPY")
            case Venue.FX:
                return Ticker("EURUSD")

    @property
    def volume(self) -> VolumeKind:
        match self:
            case Venue.XNYS:
                return VolumeKind.TRADED
            case Venue.FX:
                return VolumeKind.TICKS

    @property
    def periods(self) -> int:
        match self:
            case Venue.XNYS:
                return 252
            case Venue.FX:
                return 260

    def sessions(self, start: date, end: date) -> Sessions:
        match self:
            case Venue.XNYS:
                return calendar.sessions(start, end)
            case Venue.FX:
                return calendar.fx_sessions(start, end)

    def instrument(self, symbol: Ticker) -> Instrument:
        match self:
            case Venue.XNYS:
                return equity(symbol)
            case Venue.FX:
                return currency_pair(symbol)


def currency_pair(symbol: Ticker) -> Instrument:
    if PAIR.fullmatch(symbol) is None:
        raise InstrumentError(f"{symbol!r} is not a six-letter currency pair")
    try:
        base, quote = Currency(symbol[:3]), Currency(symbol[3:])
    except ValueError as e:
        raise InstrumentError(f"{symbol}: {e}") from e
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.FX,
        base_currency=base,
        quote_currency=quote,
        price_precision=FX_JPY_PRICE_PRECISION if quote is Currency.JPY else FX_PRICE_PRECISION,
    )
