from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tape.core.types import Ticker
    from tape.engine.backtest import BacktestEngine, Run


class BookEngine:
    __slots__ = ("sleeves",)

    def __init__(self, sleeves: Mapping[Ticker, BacktestEngine]) -> None:
        if not sleeves:
            raise ValueError("a book needs at least one sleeve")
        self.sleeves = sorted(sleeves.items())

    def run(self, first_session: int, last_session: int) -> dict[Ticker, Run]:
        for _, engine in self.sleeves:
            engine.begin(first_session, last_session)
        while True:
            due = [(t, engine) for _, engine in self.sleeves if (t := engine.pending()) is not None]
            if not due:
                break
            now = min(t for t, _ in due)
            for t, engine in due:
                if t == now:
                    engine.advance()
        return {ticker: engine.finish() for ticker, engine in self.sleeves}
