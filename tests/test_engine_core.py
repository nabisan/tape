from __future__ import annotations

import pytest

from tape.engine.matching import BarPath, FillModel, MatchingEngine
from tape.engine.orders import Fill, Order, OrderSide, OrderStatus, OrderType, TimeInForce
from tape.engine.portfolio import Portfolio, PositionSide

FREE = FillModel(commission_per_share=0.0, slippage_bps=0.0)


def order(side: OrderSide, kind: OrderType, qty: int = 100, price: float | None = None) -> Order:
    return Order(1, side, kind, qty, TimeInForce.GTC, 0, 0, price=price)


def run(o: Order, bar: tuple[float, float, float, float], model: FillModel = FREE) -> list[Fill]:
    return list(MatchingEngine(model).process([o], 1, bar))


def test_market_fills_at_open_with_slippage() -> None:
    costly = FillModel(slippage_bps=10, commission_per_share=0.005)
    [f] = run(order(OrderSide.BUY, OrderType.MARKET), (100, 101, 99, 100.5), costly)
    assert f.price == pytest.approx(100.1)
    assert f.commission == pytest.approx(0.5)


def test_costs_are_off_by_default() -> None:
    [f] = run(order(OrderSide.BUY, OrderType.MARKET), (100, 101, 99, 100.5), FillModel())
    assert f.price == 100
    assert f.commission == 0


def test_limit_buy_fills_at_limit_when_touched_intrabar() -> None:
    [f] = run(order(OrderSide.BUY, OrderType.LIMIT, price=99.5), (100, 101, 99, 100.5))
    assert f.price == 99.5


def test_limit_buy_gap_through_fills_at_open() -> None:
    [f] = run(order(OrderSide.BUY, OrderType.LIMIT, price=99.5), (98, 99, 97, 98.5))
    assert f.price == 98


def test_limit_not_reached_stays_working() -> None:
    o = order(OrderSide.SELL, OrderType.LIMIT, price=102)
    assert run(o, (100, 101, 99, 100.5)) == []
    assert o.status is OrderStatus.ACCEPTED


def test_stop_buy_triggers_at_stop_and_gap_at_open() -> None:
    [f] = run(order(OrderSide.BUY, OrderType.STOP_MARKET, price=100.8), (100, 101, 99, 100.5))
    assert f.price == 100.8
    [g] = run(order(OrderSide.BUY, OrderType.STOP_MARKET, price=100.8), (102, 103, 101.5, 102.5))
    assert g.price == 102


def test_stop_sell_slips_against_seller() -> None:
    [f] = run(
        order(OrderSide.SELL, OrderType.STOP_MARKET, price=99.5),
        (100, 101, 99, 100),
        FillModel(slippage_bps=10),
    )
    assert f.price == pytest.approx(99.5 * 0.999)


def test_bar_paths() -> None:
    bar = (100, 102, 99.9, 101)
    assert MatchingEngine(FillModel(path=BarPath.LOW_FIRST)).path(bar) == (100, 99.9, 102, 101)
    assert MatchingEngine(FillModel(path=BarPath.HIGH_FIRST)).path(bar) == (100, 102, 99.9, 101)
    adverse = MatchingEngine(FillModel(path=BarPath.ADVERSE_FIRST))
    assert adverse.path(bar, 1) == (100, 99.9, 102, 101)
    assert adverse.path(bar, -1) == (100, 102, 99.9, 101)
    assert adverse.path(bar, 0) == (100, 99.9, 102, 101)
    adaptive = MatchingEngine(FillModel(path=BarPath.ADAPTIVE))
    assert adaptive.path((100, 100.2, 98, 99)) == (100, 100.2, 98, 99)
    assert adaptive.path(bar) == (100, 99.9, 102, 101)


def test_stop_through_needs_a_strict_break() -> None:
    touch = order(OrderSide.BUY, OrderType.STOP_MARKET, price=101)
    assert run(touch, (100, 101, 99, 100), FREE) != []
    strict = order(OrderSide.BUY, OrderType.STOP_MARKET, price=101)
    strict.through = True
    assert run(strict, (100, 101, 99, 100), FREE) == []
    assert run(strict, (100, 101.01, 99, 100), FREE) != []


def test_limit_touch_respects_probability() -> None:
    o = order(OrderSide.BUY, OrderType.LIMIT, price=99)
    assert run(o, (100, 101, 99, 100), FillModel(prob_fill_on_limit=0.0)) == []


def fill(side: OrderSide, qty: int, price: float, commission: float = 1.0, oid: int = 1) -> Fill:
    return Fill(oid, oid, side, qty, price, commission, "t", price)


def test_portfolio_flip_realizes_and_reopens() -> None:
    p = Portfolio(10_000)
    p.apply(fill(OrderSide.BUY, 100, 10.0, oid=1))
    p.apply(fill(OrderSide.SELL, 150, 12.0, commission=1.5, oid=2))
    [t] = p.closed
    assert (t.side, t.realized, t.exit_price, t.commission) == (PositionSide.LONG, 200.0, 12.0, 2.0)
    assert p.open is not None
    assert (p.open.side, p.open.quantity, p.open.entry_price, p.open.commission) == (
        PositionSide.SHORT,
        50,
        12.0,
        0.5,
    )
    assert p.quantity == -50
    assert p.equity(12.0) == pytest.approx(10_000 + 200 - 2.5)


def test_portfolio_scale_in_averages_entry() -> None:
    p = Portfolio(0)
    p.apply(fill(OrderSide.BUY, 100, 10.0, commission=0))
    p.apply(fill(OrderSide.BUY, 100, 12.0, commission=0))
    assert p.open is not None
    assert (p.open.entry_price, p.open.max_quantity) == (11.0, 200)
    p.apply(fill(OrderSide.SELL, 50, 13.0, commission=0))
    assert p.open.realized == pytest.approx(100.0)
    assert p.open.quantity == 150


def test_notional_is_sized_at_the_fill_price() -> None:
    o = Order(1, OrderSide.BUY, OrderType.MARKET, 0, TimeInForce.GTC, 0, 0, notional=10_000.0)
    [f] = list(MatchingEngine(FillModel(slippage_bps=10)).process([o], 1, (99.0, 101, 98, 100)))
    assert f.quantity == 101
    assert f.price == pytest.approx(99.0 * 1.001)
    assert o.quantity == 101


def test_flip_closes_and_opens_at_the_same_fill() -> None:
    o = Order(1, OrderSide.SELL, OrderType.MARKET, 150, TimeInForce.GTC, 0, 0, notional=10_000.0)
    [f] = list(MatchingEngine(FREE).process([o], 1, (50.0, 51, 49, 50)))
    assert f.quantity == 150 + 200


def test_notional_below_one_share_is_rejected() -> None:
    o = Order(1, OrderSide.BUY, OrderType.MARKET, 0, TimeInForce.GTC, 0, 0, notional=50.0)
    assert list(MatchingEngine(FREE).process([o], 1, (99.0, 101, 98, 100))) == []
    assert (o.status, o.reason) == (OrderStatus.REJECTED, "notional below one share")


def test_stop_with_notional_sizes_at_the_stop_level() -> None:
    o = Order(
        1, OrderSide.BUY, OrderType.STOP_MARKET, 0, TimeInForce.GTC, 0, 0, price=100.8, notional=10_080.0
    )
    [f] = list(MatchingEngine(FREE).process([o], 1, (100.0, 101, 99, 100.5)))
    assert (f.quantity, f.price) == (100, 100.8)
