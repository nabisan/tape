from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from tape.benchmarks import MARKET, REFERENCE, Exposure, exposure, hold, prices
from tape.book import combine, members, stats
from tape.capital import combined_max, usage
from tape.core.hashing import Digest, digest
from tape.core.instruments import Conversion, Valuation
from tape.core.types import Ticker, Timeframe
from tape.engine.reports import Measure
from tape.service.results import Table
from tape.sweep import SweepSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tape.book import BookStats
    from tape.catalog.store import Store
    from tape.core.types import F64
    from tape.service.results import ResultStore

VERSION: Final = 3

type Kind = Literal["book", "hold", "benchmark"]


class Line(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    kind: Kind
    net_pnl: Measure
    total_return_pct: Measure
    cagr_pct: Measure
    volatility_pct: Measure
    sharpe: Measure
    max_drawdown_pct: Measure
    exposure: Exposure
    ends: date | None = None


class CapitalUse(BaseModel):
    model_config = ConfigDict(frozen=True)

    capital: Measure
    final_equity: Measure
    max_capital_loss_pct: Measure
    avg_used: Measure
    max_used: Measure
    avg_overnight: Measure


class Portfolio(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    sweep: str
    run: str
    book: str
    start: date
    end: date
    sessions: int
    periods: int
    tickers: int
    trades: int
    reference: str
    lines: list[Line]
    capital: CapitalUse


def book_id(chosen: Mapping[Ticker, Digest]) -> Digest:
    return digest({"version": VERSION, "runs": [str(k) for k in sorted(chosen.values())]})


def cached(results: ResultStore, sweep: Digest, run: str, store: Store, market: Store | None) -> Portfolio:
    summary = pd.read_parquet(results.sweep_summary(sweep))
    path = results.sweep_portfolio(sweep, book_id(members(summary, run)))
    if path.is_file():
        return Portfolio.model_validate_json(path.read_text())
    found = portfolio(results, sweep, run, store, market)
    results.save_text(path, found.model_dump_json())
    return found


def portfolio(results: ResultStore, sweep: Digest, run: str, store: Store, market: Store | None) -> Portfolio:
    summary_path = results.sweep_summary(sweep)
    spec = SweepSpec.model_validate_json(summary_path.with_suffix(".json").read_text())
    summary = pd.read_parquet(summary_path)
    chosen = members(summary, run)
    cash = spec.execution.initial_cash
    periods = store.venue.periods
    equity = {t: results.frame(k, Table.EQUITY).set_index("date") for t, k in chosen.items()}
    frame = combine({t: e["equity"] for t, e in equity.items()}, cash)
    days = pd.DatetimeIndex(frame["date"])
    reference = _reference(market, days)
    held = combine({t: _held(store, t, e.index, cash) for t, e in equity.items()}, cash)
    uses = {t: usage(results, k, store, t, cash) for t, k in chosen.items()}
    book = stats(frame, periods)
    lines = [
        _line("Book", "book", book, _returns(frame), reference),
        _line("Hold", "hold", stats(held, periods), _returns(held), reference),
        *benchmark_lines(market, days, reference, book.capital),
    ]
    trades = summary.loc[summary["key"].isin(list(chosen.values())), "trades"]
    return Portfolio(
        version=VERSION,
        sweep=sweep,
        run=run,
        book=book_id(chosen),
        start=book.start,
        end=book.end,
        sessions=book.sessions,
        periods=periods,
        tickers=len(chosen),
        trades=int(trades.sum()),
        reference=REFERENCE,
        lines=lines,
        capital=CapitalUse(
            capital=book.capital,
            final_equity=book.final_equity,
            max_capital_loss_pct=book.max_capital_loss_pct,
            avg_used=sum(u.avg_used for u in uses.values()),
            max_used=combined_max({t: u.used for t, u in uses.items()}),
            avg_overnight=sum(u.avg_overnight for u in uses.values()),
        ),
    )


def _held(store: Store, ticker: Ticker, dates: pd.Index, cash: float) -> pd.Series:
    bars = store.bars(ticker, Timeframe.D1)
    sessions = pd.to_datetime(store.sessions().day[bars.session])
    days = pd.DatetimeIndex(dates)
    close: F64 = np.asarray(pd.Series(bars.close, index=sessions).reindex(days).ffill(), dtype=np.float64)
    entry = pd.Series(bars.open, index=sessions).reindex(days[:1]).to_numpy(dtype=np.float64)
    if not len(entry) or not math.isfinite(entry[0]):
        return pd.Series(np.full(len(days), cash), index=days)
    conversion = Valuation.of(store.instrument(ticker)).conversion
    return pd.Series(cash * hold_growth(conversion, float(entry[0]), close), index=days)


def hold_growth(conversion: Conversion, entry: float, marks: F64) -> F64:
    match conversion:
        case Conversion.IDENTITY:
            return marks / entry
        case Conversion.RECIPROCAL:
            return 2.0 - entry / marks


def _returns(frame: pd.DataFrame) -> F64:
    equity: F64 = np.asarray(frame["equity"], dtype=np.float64)
    capital = float(frame["capital"].iloc[0])
    returns: F64 = np.diff(equity, prepend=capital) / np.r_[capital, equity[:-1]]
    return returns


def _reference(market: Store | None, days: pd.DatetimeIndex) -> F64:
    if market is None or REFERENCE not in market.manifest.available:
        return np.full(len(days), math.nan)
    return prices(market, REFERENCE, days).returns()


def _line(label: str, kind: Kind, s: BookStats, returns: F64, reference: F64) -> Line:
    return Line(
        label=label,
        kind=kind,
        net_pnl=s.final_equity - s.capital,
        total_return_pct=s.total_return_pct,
        cagr_pct=s.cagr_pct,
        volatility_pct=s.volatility_pct,
        sharpe=s.sharpe,
        max_drawdown_pct=s.max_drawdown_pct,
        exposure=exposure(returns, reference),
    )


def benchmark_lines(
    market: Store | None, days: pd.DatetimeIndex, reference: F64, capital: float
) -> list[Line]:
    if market is None:
        return []
    found = []
    for ticker in MARKET:
        if ticker not in market.manifest.available:
            continue
        b = hold(ticker, prices(market, ticker, days), reference, market.venue.periods)
        last = _last_session(market, ticker)
        found.append(
            Line(
                label=ticker,
                kind="benchmark",
                net_pnl=capital * b.total_return_pct / 100,
                total_return_pct=b.total_return_pct,
                cagr_pct=b.cagr_pct,
                volatility_pct=b.volatility_pct,
                sharpe=b.sharpe,
                max_drawdown_pct=b.max_drawdown_pct,
                exposure=b.exposure,
                ends=last if last is not None and last < days[-1].date() else None,
            )
        )
    return found


def _last_session(store: Store, ticker: Ticker) -> date | None:
    bars = store.bars(ticker, Timeframe.D1)
    if not len(bars):
        return None
    found: date = pd.Timestamp(store.sessions().day[bars.session[-1]]).date()
    return found
