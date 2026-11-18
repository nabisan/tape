from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from tape.book import combine, parameters, stats
from tape.core.hashing import Digest
from tape.core.instruments import Conversion, Valuation
from tape.core.types import Ticker, Timeframe
from tape.engine.orders import Fill, OrderSide
from tape.engine.portfolio import Portfolio
from tape.lab.nav import link
from tape.service.results import Table
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date
    from pathlib import Path

    from tape.catalog.catalogs import Catalogs
    from tape.catalog.store import Store
    from tape.core.types import F64, I64
    from tape.service.results import ResultStore

NEVER: Final = np.iinfo(np.int64).max
GENERIC: Final = frozenset({"timeframe", "direction", "sizing", "notional", "shares", "stop_loss_pct"})
ORDER: Final = {tf: i for i, tf in enumerate(Timeframe)}
TOLERANCE: Final = 1e-9
EXCEL_EPOCH: Final = pd.Timestamp("1899-12-30")
LINK: Final = "#0563C1"
ENGINE: Final = "Engine value, recomputable from the run's stored tables (open its link): "
FORMATS: Final = {
    "money": '$#,##0.00;($#,##0.00);"-"',
    "number": "#,##0.00",
    "count": "#,##0",
    "price": "#,##0.00000",
    "date": "dd mmm yyyy",
    "text": "@",
}
WIDE: Final = frozenset({"Strategy Name", "Link"})


class SheetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Pair:
    ticker: str
    key: Digest
    start: date
    end: date
    sessions: int
    capital: float
    final: float
    volatility: float
    trades: int
    first_open: float
    last_close: float
    conversion: Conversion
    avg_used: float
    max_used: float
    avg_overnight: float
    max_capital_loss: float
    peak_to_trough: float


@dataclass(slots=True)
class Book:
    strategy: str
    timeframe: Timeframe
    params: dict[str, Any]
    stop: float | None
    sweep: Digest
    periods: int
    pairs: list[Pair] = field(default_factory=list)
    max_used: float = math.nan
    max_capital_loss: float = math.nan
    peak_to_trough: float = math.nan

    @property
    def name(self) -> str:
        shown = " ".join(f"{k.replace('_', ' ')} {v}" for k, v in self.params.items())
        return f"{STRATEGIES[self.strategy].title} {self.timeframe} {shown}".strip()

    @property
    def order(self) -> tuple[str, int, tuple[str, ...]]:
        return self.strategy, ORDER[self.timeframe], tuple(f"{v!s:>8}" for v in self.params.values())


def return_pct(p: Pair) -> float:
    return 100 * (p.final / p.capital - 1)


def cagr_pct(p: Pair, periods: int) -> float:
    ratio = p.final / p.capital
    if ratio <= 0:
        return math.nan
    growth: float = ratio ** (periods / p.sessions)
    return 100 * (growth - 1)


def buy_hold_pct(p: Pair) -> float:
    return 100 * p.conversion.growth(p.first_open, p.last_close)


def cost_path(fills: pd.DataFrame, valuation: Valuation, cash: float) -> tuple[I64, F64]:
    portfolio = Portfolio(cash, valuation)
    times = fills["time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
    cost = np.zeros(len(fills), dtype=np.float64)
    sides = [OrderSide(v) for v in fills["side"].astype(str)]
    quantities = fills["quantity"].to_numpy(dtype=np.int64).tolist()
    prices = fills["price"].to_numpy(dtype=np.float64).tolist()
    fees = fills["commission"].to_numpy(dtype=np.float64).tolist()
    for i, (side, quantity, p, fee) in enumerate(zip(sides, quantities, prices, fees, strict=True)):
        portfolio.apply(Fill(i + 1, int(times[i]), side, quantity, p, fee, "", p))
        if portfolio.open is not None:
            cost[i] = valuation.notional(abs(portfolio.quantity), portfolio.open.entry_price)
    return times, cost


def held_before(times: I64, cost: F64, at: I64) -> F64:
    last = np.searchsorted(times, at, side="left") - 1
    return np.where(last >= 0, cost[np.maximum(last, 0)], 0.0)


def pair(results: ResultStore, key: Digest, store: Store, periods: int) -> tuple[Pair, pd.Series]:
    ticker = Ticker(results.spec(key).ticker)
    m = results.metrics(key)
    if m.start is None or m.end is None:
        raise SheetError(f"run {key[:8]} for {ticker} has no sessions")
    valuation = Valuation.of(store.instrument(ticker))
    times, cost = cost_path(results.frame(key, Table.FILLS), valuation, m.initial_cash)
    bars = results.frame(key, Table.BARS)
    stamps = bars["time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
    used = pd.Series(held_before(times, cost, np.r_[stamps[1:], NEVER]), index=pd.to_datetime(stamps))
    equity = results.frame(key, Table.EQUITY)
    days = equity["date"].to_numpy(dtype="datetime64[D]")
    s = store.sessions()
    inside = (s.day >= days[0]) & (s.day <= days[-1])
    found = Pair(
        ticker=ticker,
        key=key,
        start=m.start,
        end=m.end,
        sessions=m.sessions,
        capital=m.initial_cash,
        final=m.final_equity,
        volatility=m.volatility_pct,
        trades=m.trades,
        first_open=float(bars["open"].iloc[0]),
        last_close=float(equity["close"].iloc[-1]),
        conversion=valuation.conversion,
        avg_used=float(used.mean()),
        max_used=float(used.max()),
        avg_overnight=float(held_before(times, cost, s.close_ns[inside]).mean()),
        max_capital_loss=m.max_capital_loss_pct,
        peak_to_trough=m.max_drawdown_pct,
    )
    proofs = (
        ("Return %", m.total_return_pct, return_pct(found)),
        ("CAGR %", m.cagr_pct, cagr_pct(found, periods)),
        ("Buy & Hold Return %", m.buy_hold_return_pct, buy_hold_pct(found)),
    )
    for name, engine, formula in proofs:
        both_missing = math.isnan(engine) and math.isnan(formula)
        if not both_missing and not math.isclose(engine, formula, rel_tol=TOLERANCE, abs_tol=TOLERANCE):
            raise SheetError(f"{ticker} run {key[:8]}: {name} formula gives {formula}, engine {engine}")
    return found, used


def _book(catalogs: Catalogs, results: ResultStore, sweep: Digest) -> list[Book]:
    summary = results.sweep_summary(sweep)
    spec = SweepSpec.model_validate_json(summary.with_suffix(".json").read_text())
    frame = pd.read_parquet(summary).sort_values("ticker")
    store = catalogs.common(spec.tickers)
    cls = STRATEGIES[spec.strategy]
    names = [c for c in parameters(frame.columns) if c != "ticker"]
    cash = spec.execution.initial_cash
    found = []
    groups = [rows for _, rows in frame.groupby(names, sort=True, dropna=False)] if names else [frame]
    for rows in groups:
        first = rows.iloc[0]
        chosen = {**spec.params, **{c: first[c] for c in names if c in cls.config_type.model_fields}}
        config = cls.config_type.model_validate({**chosen, "timeframe": spec.timeframe.value})
        book = Book(
            strategy=spec.strategy,
            timeframe=spec.timeframe,
            params={k: v for k, v in config.model_dump(mode="json").items() if k not in GENERIC},
            stop=getattr(config, "stop_loss_pct", None),
            sweep=sweep,
            periods=store.venue.periods,
        )
        used = {}
        for key in rows["key"]:
            p, series = pair(results, Digest(str(key)), store, book.periods)
            book.pairs.append(p)
            used[p.ticker] = series
        sleeves = {
            Ticker(p.ticker): results.frame(p.key, Table.EQUITY).set_index("date")["equity"]
            for p in book.pairs
        }
        b = stats(combine(sleeves, cash), book.periods)
        book.max_used = float(pd.DataFrame(used).sort_index().ffill().fillna(0.0).sum(axis=1).max())
        book.max_capital_loss, book.peak_to_trough = b.max_capital_loss_pct, b.max_drawdown_pct
        summed = sum(p.final for p in book.pairs)
        if not math.isclose(summed, b.final_equity, rel_tol=TOLERANCE, abs_tol=1e-6):
            raise SheetError(
                f"{book.name}: tickers' final equity sums to {summed}, book ends at {b.final_equity}"
            )
        found.append(book)
    return found


def books(catalogs: Catalogs, results: ResultStore, sweeps: Sequence[Digest]) -> list[Book]:
    return sorted((b for s in sweeps for b in _book(catalogs, results, s)), key=lambda b: b.order)


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    note: str
    value: Callable[[Any], Any]
    kind: str = "number"
    formula: Callable[[int], str] | None = None


def _write(
    workbook: xlsxwriter.Workbook, title: str, columns: Sequence[Column], rows: Sequence[Any], label: str
) -> None:
    ws = workbook.add_worksheet(title)
    base = {"font_name": "JetBrains Mono", "font_size": 9}
    head = {**base, "bold": True, "text_wrap": True, "valign": "top"}
    plain, derived = (workbook.add_format({**head, "bg_color": c}) for c in ("#D9D9D9", "#CDEFD7"))
    styles = {k: workbook.add_format({**base, "num_format": f}) for k, f in FORMATS.items()}
    hyperlink = workbook.add_format({**base, "font_color": LINK, "underline": 1})
    for c, col in enumerate(columns):
        ws.write_string(0, c, col.header, derived if col.formula else plain)
        ws.write_comment(0, c, col.note, {"x_scale": 2.4, "y_scale": 1.6})
        ws.set_column(c, c, 40 if col.header in WIDE else 14)
    for r, row in enumerate(rows, 1):
        for c, col in enumerate(columns):
            value, style = col.value(row), styles[col.kind]
            missing = value is None or (isinstance(value, float) and math.isnan(value))
            if col.formula is not None:
                cached = "n/a" if missing else serial(value) if col.kind == "date" else value
                ws.write_formula(r, c, col.formula(r + 1), style, cached)
            elif missing:
                ws.write_blank(r, c, None, style)
            elif col.kind == "date":
                ws.write_datetime(r, c, pd.Timestamp(value).to_pydatetime(), style)
            elif col.header == "Link":
                ws.write_url(r, c, str(value), hyperlink, string=label, tip=str(value))
            elif col.kind == "text":
                ws.write_string(r, c, str(value), style)
            else:
                ws.write_number(r, c, float(value), style)
    ws.freeze_panes(1, 3)
    ws.set_row(0, 42)
    ws.autofilter(0, 0, len(rows), len(columns) - 1)


def serial(day: date) -> int:
    return (pd.Timestamp(day) - EXCEL_EPOCH).days


class Cells:
    def __init__(self, columns: Sequence[Column]) -> None:
        self._at = {col.header: xl_col_to_name(i) for i, col in enumerate(columns)}

    def __call__(self, header: str, row: int) -> str:
        return f"{self._at[header]}{row}"

    def span(self, header: str, rows: int) -> str:
        return f"Individual!${self._at[header]}$2:${self._at[header]}${rows + 1}"


def individual_columns(site: str, periods: int) -> list[Column]:
    head = [
        Column("Book No", "Book this run belongs to; joins Portfolio!Book No", lambda r: r[0], "count"),
        Column("Strategy Name", "Strategy, decision timeframe and parameters", lambda r: r[1].name, "text"),
        Column("Ticker", "Instrument", lambda r: r[2].ticker, "text"),
        Column("Start Date", "First session of the run's equity curve", lambda r: r[2].start, "date"),
        Column("End Date", "Last session of the run's equity curve", lambda r: r[2].end, "date"),
        Column("Sessions", "Sessions in the equity curve", lambda r: r[2].sessions, "count"),
        Column("Sessions per Year", "Annualisation constant of the venue", lambda r: periods, "count"),
        Column("Capital ($)", "Starting cash of this run", lambda r: r[2].capital, "money"),
        Column(
            "Final Equity ($)",
            "Equity at the last session close, open position marked",
            lambda r: r[2].final,
            "money",
        ),
        Column("Num of Trades", ENGINE + "closed round trips", lambda r: r[2].trades, "count"),
        Column(
            "Stop-Loss %", "Strategy stop-loss setting; blank if the strategy has none", lambda r: r[1].stop
        ),
        Column("First Open", "Open of the first bar of the run", lambda r: r[2].first_open, "price"),
        Column("Last Close", "Close of the last session", lambda r: r[2].last_close, "price"),
        Column(
            "Quote in USD",
            "1 if the price is in USD per unit (EURUSD); 0 if USD is the base currency (USDJPY)",
            lambda r: int(r[2].conversion is Conversion.IDENTITY),
            "count",
        ),
    ]
    at = Cells(head)
    return [
        *head,
        Column(
            "Return %",
            "= (Final Equity / Capital - 1) * 100",
            lambda r: return_pct(r[2]),
            formula=lambda n: f"=({at('Final Equity ($)', n)}/{at('Capital ($)', n)}-1)*100",
        ),
        Column(
            "CAGR %",
            "= ((Final Equity / Capital) ^ (Sessions per Year / Sessions) - 1) * 100; "
            "n/a when equity ends at or below zero, as in the engine. Portfolio averages skip n/a rows",
            lambda r: cagr_pct(r[2], periods),
            formula=lambda n: (
                f'=IF({at("Final Equity ($)", n)}<=0,"n/a",'
                f"(({at('Final Equity ($)', n)}/{at('Capital ($)', n)})"
                f"^({at('Sessions per Year', n)}/{at('Sessions', n)})-1)*100)"
            ),
        ),
        Column(
            "Volatility %",
            ENGINE + "stdev of daily equity returns * sqrt(Sessions per Year) * 100",
            lambda r: r[2].volatility,
        ),
        Column(
            "Buy & Hold Return %",
            "Dollar return of holding: (Last/First - 1)*100 if quoted in USD, else (1 - First/Last)*100",
            lambda r: buy_hold_pct(r[2]),
            formula=lambda n: (
                f"=IF({at('Quote in USD', n)}=1,({at('Last Close', n)}/{at('First Open', n)}-1)*100,"
                f"(1-{at('First Open', n)}/{at('Last Close', n)})*100)"
            ),
        ),
        Column(
            "Avg Capital Used at Cost ($)",
            ENGINE
            + "open quantity x average entry price in USD at each bar close, averaged over bars, flat = 0",
            lambda r: r[2].avg_used,
            "money",
        ),
        Column(
            "Max Capital Used at Cost ($)",
            ENGINE + "maximum of that per-bar series",
            lambda r: r[2].max_used,
            "money",
        ),
        Column(
            "Avg Overnight Capital Required ($)",
            ENGINE + "capital at cost held through each session close (17:00 New York for FX), "
            "averaged over sessions",
            lambda r: r[2].avg_overnight,
            "money",
        ),
        Column(
            "Max DD % from Initial Capital",
            ENGINE + "min over sessions of (equity / capital - 1) * 100",
            lambda r: r[2].max_capital_loss,
        ),
        Column(
            "Peak to Trough %",
            ENGINE + "min over sessions of (equity / running peak - 1) * 100, peak starting at capital",
            lambda r: r[2].peak_to_trough,
        ),
        Column("Link", "Opens this run", lambda r: site.rstrip("/") + link("run", run=r[2].key), "text"),
    ]


def _mean(values: Sequence[float]) -> float:
    kept = [v for v in values if not math.isnan(v)]
    return float(np.mean(kept)) if kept else math.nan


def _param(name: str) -> Callable[[tuple[int, Book]], Any]:
    return lambda b: b[1].params.get(name)


def portfolio_columns(
    site: str, individual: Sequence[Column], rows: int, params: Sequence[str]
) -> list[Column]:
    cells = Cells(individual)
    book = cells.span("Book No", rows)

    def ifs(fn: str, header: str) -> Callable[[int], str]:
        return lambda n: f"={fn}({cells.span(header, rows)},{book},$A{n})"

    def summed(header: str, attr: Callable[[Pair], float]) -> Column:
        return Column(
            header,
            f"= SUMIFS of Individual {header}",
            lambda b: sum(attr(p) for p in b[1].pairs),
            "money",
            ifs("SUMIFS", header),
        )

    def averaged(header: str, source: str, value: Callable[[tuple[int, Book]], float]) -> Column:
        return Column(
            header, f"= AVERAGEIFS of Individual {source}", value, formula=ifs("AVERAGEIFS", source)
        )

    columns = [
        Column("Book No", "Joins Individual!Book No", lambda b: b[0], "count"),
        Column("Strategy Name", "Strategy, decision timeframe and parameters", lambda b: b[1].name, "text"),
        Column("Timeframe", "Decision timeframe", lambda b: b[1].timeframe.value, "text"),
        *[Column(p.replace("_", " ").title(), f"Strategy parameter {p}", _param(p), "text") for p in params],
        Column(
            "Start Date",
            "= MINIFS of Individual Start Date",
            lambda b: min(p.start for p in b[1].pairs),
            "date",
            ifs("MINIFS", "Start Date"),
        ),
        Column(
            "End Date",
            "= MAXIFS of Individual End Date",
            lambda b: max(p.end for p in b[1].pairs),
            "date",
            ifs("MAXIFS", "End Date"),
        ),
        Column(
            "Num of Tickers",
            "= COUNTIFS of Individual rows in this book",
            lambda b: len(b[1].pairs),
            "count",
            lambda n: f"=COUNTIFS({book},$A{n})",
        ),
        Column(
            "Num of Trades",
            "= SUMIFS of Individual Num of Trades",
            lambda b: sum(p.trades for p in b[1].pairs),
            "count",
            ifs("SUMIFS", "Num of Trades"),
        ),
        Column("Stop-Loss %", "Strategy stop-loss setting; blank if none", lambda b: b[1].stop),
        Column(
            "Portfolio Capital ($)",
            "Start value of the book: = SUMIFS of Individual Capital",
            lambda b: sum(p.capital for p in b[1].pairs),
            "money",
            ifs("SUMIFS", "Capital ($)"),
        ),
        Column(
            "Portfolio Final Value ($)",
            "End value of the book: = SUMIFS of Individual Final Equity; "
            "equals the book equity curve's last value",
            lambda b: sum(p.final for p in b[1].pairs),
            "money",
            ifs("SUMIFS", "Final Equity ($)"),
        ),
        Column(
            "Portfolio Return %",
            "= (Portfolio Final Value / Portfolio Capital - 1) * 100",
            lambda b: 100 * (sum(p.final for p in b[1].pairs) / sum(p.capital for p in b[1].pairs) - 1),
            formula=lambda n: (
                f"=({own('Portfolio Final Value ($)', n)}/{own('Portfolio Capital ($)', n)}-1)*100"
            ),
        ),
        averaged("Avg CAGR %", "CAGR %", lambda b: _mean([cagr_pct(p, b[1].periods) for p in b[1].pairs])),
        averaged("Avg Volatility %", "Volatility %", lambda b: _mean([p.volatility for p in b[1].pairs])),
        averaged(
            "Avg Buy & Hold Return %",
            "Buy & Hold Return %",
            lambda b: _mean([buy_hold_pct(p) for p in b[1].pairs]),
        ),
        summed("Avg Capital Used at Cost ($)", lambda p: p.avg_used),
        Column(
            "Max Capital Used at Cost ($)",
            ENGINE + "maximum over time of capital at cost summed across tickers, "
            "not the sum of each ticker's maximum",
            lambda b: b[1].max_used,
            "money",
        ),
        summed("Avg Overnight Capital Required ($)", lambda p: p.avg_overnight),
        Column(
            "Portfolio Max DD % from Initial Capital (Simulation)",
            ENGINE + "tickers' equity summed per session; min of (book equity / portfolio capital - 1) * 100",
            lambda b: b[1].max_capital_loss,
        ),
        Column(
            "Peak to Trough %",
            ENGINE + "min of (book equity / running peak - 1) * 100, peak starting at portfolio capital",
            lambda b: b[1].peak_to_trough,
        ),
        Column(
            "Link",
            "Opens the simulation this book belongs to",
            lambda b: site.rstrip("/") + link("simulation", sweep=b[1].sweep),
            "text",
        ),
    ]
    own = Cells(columns)
    return columns


def workbook(catalogs: Catalogs, results: ResultStore, sweeps: Sequence[Digest], site: str, out: Path) -> int:
    found = books(catalogs, results, sweeps)
    periods = {b.periods for b in found}
    if len(periods) != 1:
        raise SheetError("books must share one venue")
    numbered = list(enumerate(found, 1))
    pairs = [(n, b, p) for n, b in numbered for p in b.pairs]
    params = list(dict.fromkeys(k for b in found for k in b.params))
    individual = individual_columns(site, periods.pop())
    file = xlsxwriter.Workbook(str(out), {"use_future_functions": True})
    _write(
        file,
        "Portfolio",
        portfolio_columns(site, individual, len(pairs), params),
        numbered,
        "View simulation",
    )
    _write(file, "Individual", individual, pairs, "View run")
    file.close()
    return len(found)
