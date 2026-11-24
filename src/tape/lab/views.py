from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import pandas as pd

from tape.book import parameters
from tape.core.types import DATE
from tape.engine.backtest import ENGINE_VERSION
from tape.engine.exits import applies
from tape.lab.markup import EN_DASH, MINUS, day, fmt
from tape.lab.schema import label
from tape.strategies.registry import stop_loss_pct

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from datetime import datetime

    import plotly.graph_objects as go

    from tape.book import BookStats
    from tape.engine.exits import Exit
    from tape.engine.margin import MarginSpec
    from tape.portfolio import Line, Portfolio
    from tape.service.models import AccountView, Catalog, StoredSweep, TickerInfo, VenueInfo
    from tape.service.sweeps import SweepStatus
    from tape.sweep import SweepSpec

DASH: Final = "&ndash;"
RANK: Final = "#"


@dataclass(frozen=True, slots=True)
class Column:
    label: str
    pattern: str


COLUMNS: Final[dict[str, Column | None]] = {
    "start": Column("Start", ""),
    "end": Column("End", ""),
    "sessions": Column("Sessions", "{:,.0f}"),
    "years": Column("Years", "{:.2f}"),
    "initial_cash": Column("Capital $", "{:,.0f}"),
    "final_equity": Column("Final equity $", "{:,.2f}"),
    "net_pnl": Column("Net P&L $", "{:,.0f}"),
    "total_return_pct": Column("Return %", "{:.2f}"),
    "cagr_pct": Column("CAGR %", "{:.2f}"),
    "volatility_pct": Column("Ann. vol %", "{:.2f}"),
    "sharpe": Column("Sharpe", "{:.2f}"),
    "sortino": None,
    "max_drawdown_pct": Column("Max DD %", "{:.2f}"),
    "max_capital_loss_pct": Column("Max loss on capital %", "{:.2f}"),
    "calmar": None,
    "trades": Column("Trades", "{:,.0f}"),
    "first_signal": Column("First signal", ""),
    "last_signal": Column("Last signal", ""),
    "win_rate_pct": Column("Win %", "{:.1f}"),
    "profit_factor": None,
    "time_in_market_pct": Column("Time in market %", "{:.1f}"),
    "buy_hold_return_pct": Column("Buy & hold %", "{:.1f}"),
}

type Stat = tuple[str, Callable[[pd.DataFrame], float], str]
STATS: Final[tuple[Stat, ...]] = (
    ("Runs", lambda f: float(len(f)), "{:,.0f}"),
    ("Profitable", lambda f: 100 * float((f["total_return_pct"] > 0).mean()), "{:.0f}%"),
    ("Median return", lambda f: float(f["total_return_pct"].median()), "{:.2f}%"),
    ("Best return", lambda f: float(f["total_return_pct"].max()), "{:.2f}%"),
    ("Median Sharpe", lambda f: float(f["sharpe"].median()), "{:.2f}"),
    ("Best Sharpe", lambda f: float(f["sharpe"].max()), "{:.2f}"),
    ("Worst DD", lambda f: float(f["max_drawdown_pct"].min()), "{:.2f}%"),
)


def stamp(moment: datetime | None) -> str:
    return "" if moment is None else f"{moment:{DATE} %H:%M:%S}"


def choice(s: StoredSweep, strategy: str) -> str:
    tickers = f"{s.tickers} ticker{'s' if s.tickers != 1 else ''}"
    parts = [strategy, s.timeframe, tickers, f"{day(s.start)} {EN_DASH} {day(s.end)}", s.key[:8]]
    if s.margin:
        parts.append("margin")
    if s.engine != ENGINE_VERSION:
        parts.append("outdated engine")
    return " · ".join(parts)


def swept(frame: pd.DataFrame, spec: SweepSpec) -> list[str]:
    return [c for c in frame.columns if c in spec.grid]


def fixed(frame: pd.DataFrame, spec: SweepSpec) -> list[str]:
    known = {"ticker", "key", *swept(frame, spec), *COLUMNS}
    return [c for c in frame.columns if c not in known and frame[c].nunique() == 1]


def table(head: str, cells: str) -> str:
    body = f'<tbody><tr class="run">{cells}</tr></tbody>'
    return f'<table class="t-cmp"><thead><tr>{head}</tr></thead>{body}</table>'


def stats(frame: pd.DataFrame) -> str:
    head = "".join(f"<th>{h}</th>" for h, _, _ in STATS)
    cells = "".join(f"<td>{fmt(fn(frame), pattern)}</td>" for _, fn, pattern in STATS)
    return table(head, cells)


LINE_COLUMNS: Final[tuple[tuple[str, Callable[[Line], float], str], ...]] = (
    ("Net P&L", lambda line: line.net_pnl, "${:,.0f}"),
    ("Total return", lambda line: line.total_return_pct, "{:.2f}%"),
    ("CAGR", lambda line: line.cagr_pct, "{:.2f}%"),
    ("Ann. vol", lambda line: line.volatility_pct, "{:.2f}%"),
    ("Ann. Sharpe", lambda line: line.sharpe, "{:.2f}"),
    ("Max DD", lambda line: line.max_drawdown_pct, "{:.2f}%"),
)


def portfolio_table(folio: Portfolio) -> str:
    ref = html.escape(folio.reference)
    head = "".join(f"<th>{h}</th>" for h, _, _ in LINE_COLUMNS)
    head += f"<th>&beta; {ref}</th><th>&rho; {ref}</th>"
    rows = []
    for line in folio.lines:
        cells = "".join(f"<td>{fmt(get(line), pattern)}</td>" for _, get, pattern in LINE_COLUMNS)
        e = line.exposure
        cells += f"<td>{fmt(e.beta, '{:.2f}')}</td><td>{fmt(e.correlation, '{:.2f}')}</td>"
        style = ' class="run"' if line.kind == "book" else ""
        name = html.escape(line.label)
        if line.ends is not None:
            name += f' <span class="t-ids">to {day(line.ends)}</span>'
        rows.append(f"<tr{style}><td>{name}</td>{cells}</tr>")
    body = "".join(rows)
    table = f'<table class="t-cmp"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>'
    return f"<div>{table}{capital_line(folio)}</div>"


def capital_line(folio: Portfolio) -> str:
    c = folio.capital
    parts = [
        f"book: {folio.trades:,} trades",
        f"capital {fmt(c.capital, '${:,.0f}')}",
        f"final {fmt(c.final_equity, '${:,.0f}')}",
        f"max DD from capital {fmt(c.max_capital_loss_pct, '{:.2f}%')}",
        f"capital used avg {fmt(c.avg_used, '${:,.0f}')}, max {fmt(c.max_used, '${:,.0f}')}",
        f"overnight avg {fmt(c.avg_overnight, '${:,.0f}')}",
    ]
    return f'<div class="t-meta">{" &middot; ".join(parts)}</div>'


def header(
    key: str, spec: SweepSpec, frame: pd.DataFrame, strategy: str, *, folio: Portfolio | None, source: str
) -> str:
    held = " · ".join(f"{label(c)} {label(frame[c].iloc[0])}" for c in fixed(frame, spec))
    tickers = folio.tickers if folio is not None else len(spec.tickers)
    cash = spec.execution.initial_cash
    runs = f"{tickers} ticker{'s' if tickers != 1 else ''} &times; ${cash:,.0f} &middot; {len(frame):,} runs"
    stop = None if "stop_loss_pct" in spec.grid else stop_loss_pct(spec.strategy, spec.params)
    if stop is not None:
        runs += f" &middot; stop loss {stop:g}%"
    held_line = f'<div class="t-meta">held fixed &middot; {html.escape(held.lower())}</div>' if held else ""
    right = portfolio_table(folio) if folio is not None else stats(frame)
    book = f" &middot; book: {html.escape(source)}" if folio is not None else ""
    return (
        '<div class="t-head"><div>'
        f'<div class="t-ticker">{html.escape(strategy)} simulation</div>'
        f'<div class="t-meta">{day(spec.start)} {DASH} {day(spec.end)} &middot; '
        f"{spec.timeframe} &middot; {runs}</div>"
        f"{held_line}"
        f'<div class="t-ids">simulation {key[:8]}{book}</div></div>{right}</div>'
    )


def leaders(best: pd.DataFrame, spec: SweepSpec) -> dict[str, str]:
    names = swept(best, spec)
    multi = len(spec.tickers) > 1
    out = {}
    for _, row in best.head(3).iterrows():
        values = "/".join(str(row[c]) for c in names)
        out[str(row["key"])] = f"{row['ticker']} {values}".strip() if multi or not values else values
    return out


def within(frame: pd.DataFrame, column: str, bounds: Collection[float] | None) -> pd.DataFrame:
    if not bounds:
        return frame
    lo, hi = min(bounds), max(bounds)
    return frame[frame[column].between(lo, hi)]


def sheet(best: pd.DataFrame, spec: SweepSpec, exits: Collection[Exit], periods: int) -> pd.DataFrame:
    hidden = set(fixed(best, spec))
    keep = [c for c in best.columns if c not in hidden and (c not in COLUMNS or COLUMNS[c] is not None)]
    keep = [c for c in keep if applies(c, exits)]
    names = {c: col.label for c in keep if (col := COLUMNS.get(c)) is not None}
    names |= {c: label(c) for c in keep if c not in COLUMNS and c not in {"ticker", "key"}}
    names["ticker"] = "Ticker"
    shown = best[keep].rename(columns=names)
    shown.insert(0, RANK, best.index.to_numpy() + 1)
    for c in ("first_signal", "last_signal"):
        if c in names:
            shown[names[c]] = [stamp(None if pd.isna(v) else v.to_pydatetime()) for v in best[c]]
    for c in ("start", "end"):
        if c in names:
            shown[names[c]] = ["" if pd.isna(v) else day(pd.Timestamp(v).date()) for v in best[c]]
    if "sessions" in names:
        years = COLUMNS["years"]
        assert years is not None
        at = list(shown.columns).index(names["sessions"]) + 1
        shown.insert(at, years.label, best["sessions"].to_numpy(dtype=float) / periods)
    return shown


def cell(pattern: str) -> Callable[[object], str]:
    def render(value: object) -> str:
        number = float(value) if isinstance(value, int | float) else math.nan
        return EN_DASH if math.isnan(number) else pattern.format(number).replace("-", MINUS)

    return render


def formats(
    shown: pd.DataFrame, columns: Mapping[str, Column | None] = COLUMNS
) -> dict[str, str | Callable[..., Any]]:
    return {
        col.label: cell(col.pattern)
        for col in columns.values()
        if col is not None and col.pattern and col.label in shown.columns
    }


def zoomable(fig: go.Figure) -> go.Figure:
    fig.update_layout(dragmode="zoom")
    return fig


def progress(status: SweepStatus) -> str:
    total = max(1, status.total)
    share = 100 * status.done / total
    return (
        f'<div class="t-progress"><i style="width:{share:.1f}%"></i></div>'
        f'<div class="t-meta">Simulation {status.key[:8]} &middot; {status.done:,} of {total:,} runs'
        f"{f' &middot; {status.failed:,} failed' if status.failed else ''}</div>"
    )


BOOK_STATS: Final[tuple[tuple[str, Callable[[BookStats], float], str], ...]] = (
    ("Capital", lambda b: b.capital, "${:,.0f}"),
    ("Final value", lambda b: b.final_equity, "${:,.2f}"),
    ("Return", lambda b: b.total_return_pct, "{:.2f}%"),
    ("CAGR", lambda b: b.cagr_pct, "{:.2f}%"),
    ("Volatility", lambda b: b.volatility_pct, "{:.2f}%"),
    ("Sharpe", lambda b: b.sharpe, "{:.2f}"),
    ("Max DD", lambda b: b.max_drawdown_pct, "{:.2f}%"),
    ("Longest DD, sessions", lambda b: float(b.max_drawdown_sessions), "{:,.0f}"),
    ("Max loss on capital", lambda b: b.max_capital_loss_pct, "{:.2f}%"),
    ("DD now", lambda b: b.drawdown_now_pct, "{:.2f}%"),
)


def pinned(values: Mapping[str, str], frame: pd.DataFrame) -> str:
    return " · ".join(f"{label(c)} {label(v)}" for c, v in values.items() if frame[c].nunique() > 1)


def book_parameters(row: pd.Series, frame: pd.DataFrame) -> str:
    varied = [c for c in parameters(frame.columns) if frame[c].nunique() > 1]
    return " · ".join(f"{label(c)} {label(row[c])}" for c in varied)


def book_stats(stats: BookStats, periods: int) -> str:
    period = f"{day(stats.start)} {DASH} {day(stats.end)}"
    head = (
        "<th>Period</th><th>Years</th>"
        + "".join(f"<th>{h}</th>" for h, _, _ in BOOK_STATS)
        + "<th>Trough</th>"
    )
    cells = f"<td>{period}</td><td>{fmt(stats.sessions / periods, '{:.2f}')}</td>"
    cells += "".join(f"<td>{fmt(fn(stats), pattern)}</td>" for _, fn, pattern in BOOK_STATS)
    return table(head, cells + f"<td>{day(stats.trough)}</td>")


def account_stats(view: AccountView, margin: MarginSpec) -> str:
    r = view.report
    at = pd.Timestamp(r.liquidated_ns, unit="ns") if r.liquidated_ns is not None else None
    head = "".join(
        f"<th>{h}</th>"
        for h in (
            "Leverage",
            "Maintenance",
            "Liquidated",
            "Liquidated at",
            "Margin rejections",
            "Max locked",
            "Lowest free",
        )
    )
    cells = (
        f"<td>{fmt(margin.leverage, '{:.0f}x')}</td>"
        f"<td>{fmt(100 * margin.maintenance, '{:.0f}%')}</td>"
        f"<td>{'Yes' if at is not None else 'No'}</td>"
        f"<td>{DASH if at is None else f'{at:{DATE} %H:%M}'}</td>"
        f"<td>{fmt(float(r.rejections), '{:,.0f}')}</td>"
        f"<td>{fmt(r.max_locked, '${:,.2f}')}</td>"
        f"<td>{fmt(r.min_free, '${:,.2f}')}</td>"
    )
    return table(head, cells)


def listed(catalog: Catalog, ticker: str) -> TickerInfo:
    found = next((t for t in catalog.tickers if t.ticker == ticker), None)
    if found is None:
        raise KeyError(f"{ticker} is in no catalog")
    return found


def venue(catalog: Catalog, ticker: str) -> VenueInfo:
    wanted = listed(catalog, ticker).venue
    return next(v for v in catalog.venues if v.venue == wanted)
