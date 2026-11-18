from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import pandas as pd

from tape.book import parameters
from tape.core.types import DATE
from tape.engine.exits import applies
from tape.lab.markup import EN_DASH, MINUS, day, fmt
from tape.lab.schema import label

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from datetime import datetime

    import plotly.graph_objects as go

    from tape.book import BookStats
    from tape.engine.exits import Exit
    from tape.service.models import Catalog, StoredSweep, TickerInfo, VenueInfo
    from tape.service.sweeps import SweepStatus
    from tape.sweep import SweepSpec

DASH: Final = "&ndash;"


@dataclass(frozen=True, slots=True)
class Column:
    label: str
    pattern: str


COLUMNS: Final[dict[str, Column | None]] = {
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
    return f"{strategy} · {s.timeframe} · {tickers} · {day(s.start)} {EN_DASH} {day(s.end)} · {s.key[:8]}"


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


def header(key: str, spec: SweepSpec, frame: pd.DataFrame, strategy: str) -> str:
    count = len(spec.tickers)
    held = " · ".join(f"{label(c)} {label(frame[c].iloc[0])}" for c in fixed(frame, spec))
    runs = f"{count} ticker{'s' if count != 1 else ''} &middot; {len(frame):,} runs"
    held_line = f'<div class="t-meta">held fixed &middot; {html.escape(held.lower())}</div>' if held else ""
    return (
        '<div class="t-head"><div>'
        f'<div class="t-ticker">{html.escape(strategy)} simulation</div>'
        f'<div class="t-meta">{day(spec.start)} {DASH} {day(spec.end)} &middot; '
        f"{spec.timeframe} &middot; {runs}</div>"
        f"{held_line}"
        f'<div class="t-ids">simulation {key[:8]}</div></div>{stats(frame)}</div>'
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


def sheet(best: pd.DataFrame, spec: SweepSpec, exits: Collection[Exit]) -> pd.DataFrame:
    hidden = set(fixed(best, spec))
    keep = [c for c in best.columns if c not in hidden and (c not in COLUMNS or COLUMNS[c] is not None)]
    keep = [c for c in keep if applies(c, exits)]
    names = {c: col.label for c in keep if (col := COLUMNS.get(c)) is not None}
    names |= {c: label(c) for c in keep if c not in COLUMNS and c not in {"ticker", "key"}}
    names["ticker"] = "Ticker"
    shown = best[keep].rename(columns=names)
    for c in ("first_signal", "last_signal"):
        if c in names:
            shown[names[c]] = [stamp(None if pd.isna(v) else v.to_pydatetime()) for v in best[c]]
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


def still(fig: go.Figure) -> go.Figure:
    fig.update_layout(dragmode=False)
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
    ("Return", lambda b: b.total_return_pct, "{:.2f}%"),
    ("CAGR", lambda b: b.cagr_pct, "{:.2f}%"),
    ("Sharpe", lambda b: b.sharpe, "{:.2f}"),
    ("Max DD", lambda b: b.max_drawdown_pct, "{:.2f}%"),
    ("Longest DD, sessions", lambda b: float(b.max_drawdown_sessions), "{:,.0f}"),
    ("Max loss on capital", lambda b: b.max_capital_loss_pct, "{:.2f}%"),
    ("DD now", lambda b: b.drawdown_now_pct, "{:.2f}%"),
)


def book_parameters(row: pd.Series, frame: pd.DataFrame) -> str:
    varied = [c for c in parameters(frame.columns) if frame[c].nunique() > 1]
    return " · ".join(f"{label(c)} {label(row[c])}" for c in varied)


def book_stats(stats: BookStats) -> str:
    head = "".join(f"<th>{h}</th>" for h, _, _ in BOOK_STATS) + "<th>Trough</th>"
    cells = "".join(f"<td>{fmt(fn(stats), pattern)}</td>" for _, fn, pattern in BOOK_STATS)
    return table(head, cells + f"<td>{day(stats.trough)}</td>")


def listed(catalog: Catalog, ticker: str) -> TickerInfo:
    found = next((t for t in catalog.tickers if t.ticker == ticker), None)
    if found is None:
        raise KeyError(f"{ticker} is in no catalog")
    return found


def venue(catalog: Catalog, ticker: str) -> VenueInfo:
    wanted = listed(catalog, ticker).venue
    return next(v for v in catalog.venues if v.venue == wanted)
