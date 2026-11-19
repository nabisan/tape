from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from tape.engine import books
from tape.engine.books import SHORT_OF_MARGIN, SharedAccount, wake_price
from tape.engine.margin import MarginSpec
from tape.engine.orders import Fill, OrderOrigin, OrderSide, OrderStatus, OrderType
from tape.features.grid import DEFAULT_GRID
from tape.runner import BookResult, RunSpec, execute_book, prepare, session_range
from tests.test_fx import stores

if TYPE_CHECKING:
    from tape.catalog.store import Store
    from tape.engine.backtest import BacktestEngine

__all__ = ["stores"]
MARGIN = MarginSpec()


def run_spec(ticker: str, cash: float, **execution: object) -> RunSpec:
    return RunSpec.model_validate(
        {
            "ticker": ticker,
            "start": "2024-01-02",
            "end": "2024-06-28",
            "strategy": "sma_cross",
            "params": {"timeframe": "1h", "fast": 5, "slow": 20},
            "execution": {"initial_cash": cash, "fill_on": "minute", "margin": MARGIN.model_dump()}
            | execution,
            "features": "compute",
        }
    )


def engines(fx: Store, cash: float) -> tuple[SharedAccount, BacktestEngine, BacktestEngine]:
    built = []
    for t in ("EURUSD", "USDJPY"):
        s = run_spec(t, cash)
        p = prepare(fx, s, DEFAULT_GRID)
        first, last = session_range(fx.sessions(), s.start, s.end)
        p.engine.begin(first, last)
        built.append(p.engine)
    account = SharedAccount(built, MARGIN)
    for e in built:
        e.ctx.gate = account.gate(e)
    return account, built[0], built[1]


def hold(engine: BacktestEngine, side: OrderSide, quantity: int, price: float) -> None:
    engine.ctx.portfolio.apply(Fill(9_999, 0, side, quantity, price, 0.0, "seed", price))
    engine.last_close = price


def test_an_entry_is_accepted_while_free_margin_covers_it_and_locks_its_initial_margin(
    stores: tuple[Store, Store],
) -> None:
    account, eur, _ = engines(stores[1], 10_000)
    assert account.free() == pytest.approx(20_000)
    order = eur.ctx.submit(OrderSide.BUY, 0, notional=10_000)
    assert order.status is OrderStatus.ACCEPTED
    assert account.locked() == pytest.approx(10_000 / 30)


def test_the_second_pair_is_refused_when_the_first_used_the_free_margin(stores: tuple[Store, Store]) -> None:
    account, eur, jpy = engines(stores[1], 250)
    first = eur.ctx.submit(OrderSide.BUY, 0, notional=10_000)
    second = jpy.ctx.submit(OrderSide.BUY, 0, notional=10_000)
    assert first.status is OrderStatus.ACCEPTED
    assert (second.status, second.reason) == (OrderStatus.REJECTED, SHORT_OF_MARGIN)
    assert account.rejections == 1


def test_closing_needs_no_margin_and_a_flip_releases_the_old_side_first(stores: tuple[Store, Store]) -> None:
    account, eur, _ = engines(stores[1], 100)
    hold(eur, OrderSide.BUY, 8_000, 1.1)
    held_maintenance = MARGIN.maintenance_margin(8_000 * 1.1)
    assert account.locked() == pytest.approx(held_maintenance)
    close = eur.ctx.submit(OrderSide.SELL, 8_000)
    assert close.status is OrderStatus.ACCEPTED
    eur.ctx.cancel_all()
    assert account.free() == pytest.approx(200 - held_maintenance)
    flip = eur.ctx.submit(OrderSide.SELL, 8_000, notional=5_000)
    assert MARGIN.initial(5_000) <= 200 < MARGIN.initial(5_000) + held_maintenance
    assert flip.status is OrderStatus.ACCEPTED


def test_balance_moves_only_when_a_loss_is_realized(stores: tuple[Store, Store]) -> None:
    account, eur, _ = engines(stores[1], 10_000)
    before = account.balance()
    hold(eur, OrderSide.BUY, 8_000, 1.1)
    eur.last_close = 1.0
    assert account.balance() == before
    assert account.equity() == pytest.approx(before - 800)
    eur.ctx.portfolio.apply(Fill(10_000, 1, OrderSide.SELL, 8_000, 1.0, 0.0, "close", 1.0))
    assert account.balance() == pytest.approx(before - 800)


@pytest.mark.parametrize(
    ("ticker", "side", "price"), [("EURUSD", OrderSide.BUY, 1.1), ("USDJPY", OrderSide.SELL, 150.0)]
)
def test_the_wake_price_is_where_this_pair_has_lost_its_share(
    stores: tuple[Store, Store], ticker: str, side: OrderSide, price: float
) -> None:
    _, eur, jpy = engines(stores[1], 10_000)
    engine = eur if ticker == "EURUSD" else jpy
    hold(engine, side, 10_000, price)
    p = engine.ctx.portfolio
    found = wake_price(engine, 75.0, price)
    assert found is not None
    level, falling = found
    assert falling is (side is OrderSide.BUY)
    lost = p.valuation.pnl(p.quantity, price, price) - p.valuation.pnl(p.quantity, price, level)
    assert lost == pytest.approx(75.0, abs=1e-9)


SB: dict[str, object] = {
    "strategy": "sma_breakout",
    "params": {"timeframe": "5m", "trend": "1h", "window": 20},
}
HB: dict[str, object] = {"strategy": "high_breakout", "params": {"timeframe": "5m", "stop_loss_pct": 10.0}}


def margin_book(
    fx: Store, cash: float, *, fast_forward: bool = True, strategy: dict[str, object] | None = None
) -> BookResult:
    specs = [run_spec(t, cash) for t in ("USDJPY", "EURUSD")]
    if strategy is not None:
        specs = [s.model_copy(update=strategy) for s in specs]
    return execute_book(fx, specs, fast_forward=fast_forward)


def test_a_thin_account_is_liquidated_once_equity_reaches_maintenance(stores: tuple[Store, Store]) -> None:
    book = margin_book(stores[1], 250)
    account = book.account
    assert account is not None and account.liquidated_ns is not None
    at = pd.Timestamp(account.liquidated_ns, unit="ns")
    for r in book.sleeves.values():
        orders = r.orders
        liq = orders[orders["origin"] == OrderOrigin.LIQUIDATION.value]
        later = orders[(orders["submitted"] > at) & (orders["status"] != OrderStatus.CANCELED.value)]
        assert (later["status"] == OrderStatus.REJECTED.value).all()
        assert (later["reason"] == SHORT_OF_MARGIN).all()
        if len(liq):
            assert (liq["submitted"] == at).all()
            assert (liq["type"] == OrderType.MARKET.value).all()
    assert account.rejections > 0 and account.max_locked > 0


def test_a_well_funded_account_is_never_liquidated_and_matches_separate_accounts(
    stores: tuple[Store, Store],
) -> None:
    book = margin_book(stores[1], 10_000)
    assert book.account is not None and book.account.liquidated_ns is None and book.account.rejections == 0
    plain = execute_book(
        stores[1],
        [
            s.model_copy(update={"execution": s.execution.model_copy(update={"margin": None})})
            for s in (run_spec("USDJPY", 10_000), run_spec("EURUSD", 10_000))
        ],
    )
    for t, r in book.sleeves.items():
        pd.testing.assert_frame_equal(r.fills, plain.sleeves[t].fills, check_exact=True)
        pd.testing.assert_frame_equal(r.equity, plain.sleeves[t].equity, check_exact=True)


@pytest.mark.parametrize("strategy", [None, SB, HB], ids=["sma_cross", "sma_breakout", "high_breakout"])
@pytest.mark.parametrize("cash", [180, 250, 300, 400])
def test_skipping_bars_never_changes_a_margin_book(
    stores: tuple[Store, Store], cash: float, strategy: dict[str, object] | None
) -> None:
    fast = margin_book(stores[1], cash, strategy=strategy)
    slow = margin_book(stores[1], cash, fast_forward=False, strategy=strategy)
    assert fast.account == slow.account
    for t, r in fast.sleeves.items():
        for name in ("fills", "orders", "equity", "trades"):
            pd.testing.assert_frame_equal(getattr(r, name), getattr(slow.sleeves[t], name), check_exact=True)


def test_without_wake_prices_skipping_would_liquidate_late(
    stores: tuple[Store, Store], monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = margin_book(stores[1], 400, fast_forward=False, strategy=HB)
    monkeypatch.setattr(books, "wake_price", lambda *_: None)
    blind = margin_book(stores[1], 400, strategy=HB)
    assert slow.account is not None and blind.account is not None
    assert slow.account.liquidated_ns is not None
    assert blind.account.liquidated_ns != slow.account.liquidated_ns


def test_margin_books_are_refused_on_bar_fills_and_on_cash_instruments(stores: tuple[Store, Store]) -> None:
    stock, fx = stores
    with pytest.raises(ValueError, match="minute bars"):
        execute_book(fx, [run_spec("EURUSD", 1_000, fill_on="bar")])
    equity = run_spec("AAPL", 1_000).model_copy(update={"start": pd.Timestamp("2024-02-01").date()})
    with pytest.raises(ValueError, match="FX pairs only"):
        execute_book(stock, [equity])
