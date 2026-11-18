from __future__ import annotations

import pytest
from pydantic import ValidationError

from tape.core.instruments import (
    AssetClass,
    Conversion,
    Currency,
    Instrument,
    InstrumentError,
    Valuation,
    conversion,
    equity,
)
from tape.core.types import Ticker
from tape.engine.orders import Fill, OrderSide
from tape.engine.portfolio import CashAccount, MarginAccount, Portfolio
from tape.venues import Venue, currency_pair

EURUSD = currency_pair(Ticker("EURUSD"))
USDJPY = currency_pair(Ticker("USDJPY"))


def fill(side: OrderSide, qty: int, price: float, oid: int, commission: float = 0.0) -> Fill:
    return Fill(oid, oid, side, qty, price, commission, "t", price)


def test_pairs_parse_into_base_and_quote() -> None:
    assert (EURUSD.base_currency, EURUSD.quote_currency, EURUSD.price_precision) == (
        Currency.EUR,
        Currency.USD,
        5,
    )
    assert (USDJPY.base_currency, USDJPY.quote_currency, USDJPY.price_precision) == (
        Currency.USD,
        Currency.JPY,
        3,
    )
    for bad in ("EURUS", "EURXXX", "usdjpy"):
        with pytest.raises(InstrumentError):
            currency_pair(Ticker(bad))


def test_illegal_instruments_are_refused() -> None:
    with pytest.raises(ValidationError, match="two different currencies"):
        Instrument(
            symbol=Ticker("USDUSD"),
            asset_class=AssetClass.FX,
            base_currency=Currency.USD,
            quote_currency=Currency.USD,
            price_precision=5,
        )
    with pytest.raises(ValidationError, match="no base currency"):
        Instrument(
            symbol=Ticker("AAPL"),
            asset_class=AssetClass.EQUITY,
            base_currency=Currency.EUR,
            quote_currency=Currency.USD,
            price_precision=2,
        )


def test_conversion_into_the_account_currency() -> None:
    assert conversion(equity(Ticker("AAPL"))) is Conversion.IDENTITY
    assert conversion(EURUSD) is Conversion.IDENTITY
    assert conversion(USDJPY) is Conversion.RECIPROCAL
    with pytest.raises(InstrumentError, match="cross rates"):
        conversion(currency_pair(Ticker("EURJPY")))


def test_sizing_buys_the_notional_in_account_currency() -> None:
    assert Valuation.of(equity(Ticker("AAPL"))).units(10_000.0, 99.0) == 101
    assert Valuation.of(EURUSD).units(10_000.0, 1.25) == 8_000
    assert Valuation.of(USDJPY).units(10_000.0, 150.123) == 10_000


def test_quote_currency_pnl_converts_at_the_closing_rate() -> None:
    v = Valuation.of(USDJPY)
    assert v.pnl(10_000, 150.0, 151.0) == pytest.approx(10_000 / 151.0)
    assert v.pnl(-10_000, 150.0, 151.0) == pytest.approx(-10_000 / 151.0)
    assert v.notional(10_000, 150.0) == pytest.approx(10_000.0)


def test_multiplier_scales_notional_and_pnl() -> None:
    contract = equity(Ticker("ES")).model_copy(update={"multiplier": 50.0})
    assert contract.notional(2, 5_000.0) == 500_000.0
    assert contract.pnl(-2, 5_000.0, 4_990.0) == 1_000.0


def test_the_account_follows_the_quote_currency() -> None:
    assert isinstance(Portfolio(0.0, Valuation.of(EURUSD)).account, CashAccount)
    assert isinstance(Portfolio(0.0, Valuation.of(USDJPY)).account, MarginAccount)


def test_a_yen_quoted_round_trip_books_dollars() -> None:
    p = Portfolio(10_000.0, Valuation.of(USDJPY))
    p.apply(fill(OrderSide.BUY, 10_000, 150.0, 1, commission=2.0))
    assert p.cash == pytest.approx(9_998.0)
    assert p.equity(150.0) == pytest.approx(9_998.0)
    assert p.equity(152.0) == pytest.approx(9_998.0 + 20_000 / 152.0)
    p.apply(fill(OrderSide.SELL, 10_000, 151.0, 2, commission=2.0))
    [t] = p.closed
    assert t.realized == pytest.approx(10_000 / 151.0)
    assert p.quantity == 0
    assert p.equity(140.0) == p.equity(160.0) == pytest.approx(10_000 + 10_000 / 151.0 - 4.0)


def test_a_flip_realizes_the_old_side_and_marks_the_new() -> None:
    p = Portfolio(10_000.0, Valuation.of(USDJPY))
    p.apply(fill(OrderSide.SELL, 10_000, 150.0, 1))
    p.apply(fill(OrderSide.BUY, 20_000, 148.0, 2))
    assert p.closed[0].realized == pytest.approx(20_000 / 148.0)
    assert p.quantity == 10_000
    assert p.unrealized(149.0) == pytest.approx(10_000 / 149.0)


def test_venues_name_their_rules() -> None:
    assert (Venue.XNYS.periods, Venue.FX.periods) == (252, 260)
    assert (Venue.XNYS.reference, Venue.FX.reference) == ("SPY", "EURUSD")
    assert Venue.FX.instrument(Ticker("GBPUSD")).quote_currency is Currency.USD
    assert Venue.XNYS.instrument(Ticker("AAPL")).asset_class is AssetClass.EQUITY


def test_buy_and_hold_growth_is_in_account_currency() -> None:
    assert Conversion.IDENTITY.growth(100.0, 110.0) == 110.0 / 100.0 - 1
    assert Conversion.RECIPROCAL.growth(150.0, 160.0) == pytest.approx(10.0 / 160.0)
    assert Valuation.of(USDJPY).pnl(10_000, 150.0, 160.0) / 10_000 == pytest.approx(
        Conversion.RECIPROCAL.growth(150.0, 160.0)
    )
