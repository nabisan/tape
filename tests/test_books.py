from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from tape.core.types import Ticker
from tape.engine.backtest import BacktestEngine
from tape.engine.books import BookEngine
from tape.runner import Result, RunSpec, execute, execute_book
from tests.test_engine_runs import ALL, SMA_H1, spec, store
from tests.test_fx import stores

if TYPE_CHECKING:
    from tape.catalog.store import Store
    from tape.engine.margin import MarginSpec

__all__ = ["store", "stores"]
FRAMES = ("equity", "trades", "orders", "fills", "bars")


def same(book: Result, solo: Result) -> None:
    assert book.key == solo.key
    assert book.metrics.model_dump_json() == solo.metrics.model_dump_json()
    for name in FRAMES:
        pd.testing.assert_frame_equal(getattr(book, name), getattr(solo, name), check_exact=True)


def sleeves(base: RunSpec, tickers: list[str]) -> list[RunSpec]:
    return [base.model_copy(update={"ticker": Ticker(t)}) for t in tickers]


CASES = [(s, f, False) for s in ALL for f in ("bar", "minute")] + [
    (SMA_H1, "bar", True),
    (SMA_H1, "minute", True),
]


@pytest.mark.parametrize(
    ("strategy", "fill_on", "intraday"), CASES, ids=lambda v: str(v[0]) if isinstance(v, tuple) else str(v)
)
def test_a_book_of_separate_accounts_matches_each_pair_run_alone(
    store: Store, strategy: tuple[str, dict[str, object]], fill_on: str, intraday: bool
) -> None:
    specs = sleeves(spec(strategy, fill_on=fill_on, intraday=intraday), ["SPY", "AAPL"])
    book = execute_book(store, specs)
    assert sorted(book.sleeves) == ["AAPL", "SPY"] and book.account is None
    for s in specs:
        same(book.sleeves[s.ticker], execute(store, s))


@pytest.mark.parametrize("fill_on", ["bar", "minute"])
def test_an_fx_book_matches_each_pair_alone(stores: tuple[Store, Store], fill_on: str) -> None:
    _, fx = stores
    base = RunSpec.model_validate(
        {
            "ticker": "EURUSD",
            "start": "2024-01-02",
            "end": "2024-06-28",
            "strategy": "sma_breakout",
            "params": {"timeframe": "5m", "trend": "1h", "window": 20},
            "execution": {"fill_on": fill_on},
            "features": "compute",
        }
    )
    specs = sleeves(base, ["USDJPY", "EURUSD"])
    book = execute_book(fx, specs)
    for s in specs:
        same(book.sleeves[s.ticker], execute(fx, s))


def test_the_book_steps_every_bar_once_in_time_order(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    steps: list[tuple[int, int]] = []
    original = BacktestEngine.advance

    def record(engine: BacktestEngine) -> None:
        steps.append((id(engine), int(engine.time[engine.next_j])))
        original(engine)

    monkeypatch.setattr(BacktestEngine, "advance", record)
    specs = sleeves(spec(SMA_H1, fill_on="minute"), ["SPY", "AAPL"])
    execute_book(store, specs)
    together = list(steps)
    times = [t for _, t in together]
    assert times == sorted(times)
    alone = 0
    for s in specs:
        steps.clear()
        execute(store, s)
        alone += len(steps)
    assert len(together) == alone
    engines = {e for e, _ in together}
    for e in engines:
        own = [t for x, t in together if x == e]
        assert len(own) == len(set(own))


def test_ties_are_taken_in_ticker_order(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    steps: list[tuple[int, int]] = []
    names: dict[int, str] = {}
    advance, init = BacktestEngine.advance, BookEngine.__init__

    def record(engine: BacktestEngine) -> None:
        steps.append((id(engine), int(engine.time[engine.next_j])))
        advance(engine)

    def named(
        book: BookEngine, sleeves: dict[Ticker, BacktestEngine], margin: MarginSpec | None = None
    ) -> None:
        init(book, sleeves, margin)
        names.update({id(e): t for t, e in book.sleeves})

    monkeypatch.setattr(BacktestEngine, "advance", record)
    monkeypatch.setattr(BookEngine, "__init__", named)
    execute_book(store, sleeves(spec(SMA_H1, fill_on="minute"), ["SPY", "AAPL"]))
    by_time: dict[int, list[str]] = {}
    for e, t in steps:
        by_time.setdefault(t, []).append(names[e])
    shared = [who for who in by_time.values() if len(who) > 1]
    assert shared
    assert all(who == ["AAPL", "SPY"] for who in shared)


def test_a_book_refuses_duplicate_tickers_and_mismatched_windows(store: Store) -> None:
    base = spec(SMA_H1)
    with pytest.raises(ValueError, match="each ticker once"):
        execute_book(store, [base, base])
    shorter = base.model_copy(update={"ticker": Ticker("SPY"), "end": pd.Timestamp("2024-05-31").date()})
    with pytest.raises(ValueError, match="one spec apart from its ticker"):
        execute_book(store, [base, shorter])
    with pytest.raises(ValueError, match="at least one sleeve"):
        BookEngine({})
