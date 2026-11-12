from __future__ import annotations

import hashlib
import html
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx2
import numpy as np
import numpy.typing as npt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from tape import log
from tape.benchmarks import REFERENCE
from tape.config import Settings
from tape.core.types import DATE, STAMP, Ticker, Timeframe
from tape.engine.backtest import ExecutionSpec
from tape.engine.exits import Exit, applies
from tape.engine.matching import FillModel
from tape.log import Log
from tape.runner import RunSpec
from tape.service.ledgers import MEDIA, LedgerState, filename
from tape.service.results import Table
from tape.strategies.registry import STRATEGIES
from tape.sweep import SweepSpec
from tape.ui import basket, charts, forms
from tape.ui.client import ApiError, Client, GoneError
from tape.ui.theme import (
    CELL,
    CSS,
    DOWN,
    FAINT,
    GRID_STAMP,
    HEAT_DOWN,
    HEAT_UP,
    INDICATORS,
    INK,
    MUTED,
    SECONDARY,
    SERIES,
    SIDEBAR,
    SLIDER_DATE,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    from tape.benchmarks import Comparison
    from tape.engine.reports import Metrics
    from tape.fundamentals import Snapshot, Valuation
    from tape.service.models import Catalog, StrategyInfo, StrategySpace
    from tape.service.sweeps import SweepStatus

type Row = tuple[str, Callable[[Metrics], float], str]


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    get: Callable[[Metrics], float]
    fmt: str
    metric: str = ""


type Moment = tuple[str, Callable[[Metrics], datetime | None]]

API_URL: Final = os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000")
LABELS: Final = ("A", "B")
LINKS: Final = dict(zip(LABELS, ("run", "b"), strict=True))
SWEEP_LINK: Final = "sweep"
MODE: Final = "mode"
GROUPS: Final[list[list[Line]]] = [
    [
        Line("Net P&L $", lambda m: m.net_pnl, "{:,.0f}"),
        Line("Total return %", lambda m: m.total_return_pct, "{:.2f}"),
        Line("CAGR %", lambda m: m.cagr_pct, "{:.2f}"),
    ],
    [
        Line("Volatility, ann. %", lambda m: m.volatility_pct, "{:.2f}"),
        Line("Sharpe, ann.", lambda m: m.sharpe, "{:.2f}"),
        Line("Sortino, ann.", lambda m: m.sortino, "{:.2f}"),
        Line("Max drawdown %", lambda m: m.max_drawdown_pct, "{:.2f}"),
        Line("Longest DD, sessions", lambda m: m.max_drawdown_sessions, "{:,.0f}"),
        Line("Calmar", lambda m: m.calmar, "{:.2f}"),
        Line("Exposure %", lambda m: m.exposure_pct, "{:.1f}"),
    ],
    [
        Line("Trades", lambda m: m.trades, "{:,.0f}"),
        Line("Win rate %", lambda m: m.win_rate_pct, "{:.1f}", "win_rate_pct"),
        Line("Profit factor", lambda m: m.profit_factor, "{:.2f}"),
        Line("Payoff ratio", lambda m: m.payoff_ratio, "{:.2f}"),
        Line("Expectancy $", lambda m: m.expectancy, "{:,.2f}"),
        Line("Average win $", lambda m: m.avg_win, "{:,.2f}"),
        Line("Average loss $", lambda m: m.avg_loss, "{:,.2f}"),
        Line("Average hold, h", lambda m: m.avg_duration_hours, "{:,.1f}"),
    ],
    [
        Line("Commission $", lambda m: m.total_commission, "{:,.0f}"),
        Line("Final equity $", lambda m: m.final_equity, "{:,.0f}"),
    ],
]
LEDGER_POLL: Final = 3.0
SLOW_SECTION: Final = 2.0
UI: Final = Log("Ui")
SWEEP_POLL: Final = 2.0
BACKGROUND_POLL: Final = 5.0
MOMENTS: Final[dict[str, tuple[Moment, ...]]] = {
    "Trades": (("First signal", lambda m: m.first_signal), ("Last signal", lambda m: m.last_signal))
}


@st.cache_resource
def logging_ready() -> bool:
    log.setup(Settings.from_env().log_level)
    return True


@st.cache_resource
def client() -> Client:
    logging_ready()
    return Client(API_URL)


@st.cache_data(ttl=300)
def catalog() -> Catalog:
    return client().catalog()


@st.cache_data(ttl=300)
def strategies() -> dict[str, StrategyInfo]:
    return client().strategies()


@st.cache_resource(max_entries=64)
def table(key: str, name: Table) -> pd.DataFrame:
    return client().table(key, name)


@st.cache_resource(max_entries=64)
def market(key: str) -> Comparison:
    return client().comparison(key)


@st.cache_resource(max_entries=64)
def worth(key: str) -> Valuation:
    return client().valuation(key)


GROUP_TITLES: Final = ("Returns", "Risk", "Trades", "Costs")
COMPARE: Final[tuple[Row, ...]] = (
    ("Net P&L", lambda m: m.net_pnl, "${:,.0f}"),
    ("Total return", lambda m: m.total_return_pct, "{:.2f}%"),
    ("CAGR", lambda m: m.cagr_pct, "{:.2f}%"),
    ("Ann. vol", lambda m: m.volatility_pct, "{:.2f}%"),
    ("Ann. Sharpe", lambda m: m.sharpe, "{:.2f}"),
    ("Max DD", lambda m: m.max_drawdown_pct, "{:.2f}%"),
)
MINUS: Final = "\u2212"
TIMES: Final = "\u00d7"
EN_DASH: Final = "\u2013"
MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DASH: Final = {"solid": "solid", "dash": "dashed", "dot": "dotted"}


def _fmt(value: float, pattern: str, signed: bool = False) -> str:
    if math.isnan(value):
        return "&ndash;"
    text = pattern.format(value)
    return f"+{text}" if signed and value > 0 else text.replace("-", "&minus;")


def _plain(value: float, pattern: str) -> str:
    return EN_DASH if math.isnan(value) else pattern.format(value).replace("-", MINUS)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, int | float):
        return f"{value:,g}"
    return forms.label(value).lower()


def _day(day: date) -> str:
    return day.strftime(DATE)


def mark(label: str, compare: bool) -> str:
    return f'<span class="mark">{label}</span>' if compare else ""


def title(name: str, caption: str = "", keys: str = "") -> str:
    cap = f'<span class="cap">{caption}</span>' if caption else ""
    legend = f'<span class="keys">{keys}</span>' if keys else ""
    return f'<div class="t-title"><span class="name">{html.escape(name)}</span>{cap}{legend}</div>'


def key_line(name: str, color: str, dash: str = "solid") -> str:
    style = f"border-top: 2px {DASH[dash]} {color}"
    return f'<span class="t-key"><i style="{style}"></i>{html.escape(name)}</span>'


def key_marker(name: str, glyph: str) -> str:
    return f'<span class="t-key"><b>{glyph}</b>{html.escape(name)}</span>'


def notice(message: str) -> None:
    st.html(f'<div class="t-notice">{html.escape(message)}</div>')


def guarded(label: str, key: str, render: Callable[[], None]) -> None:
    started = time.perf_counter()
    try:
        with st.spinner(f"Loading {label}"):
            render()
    except GoneError:
        raise
    except ApiError as e:
        UI.warn("section failed", section=label, reason=e)
        notice(f"The {label} could not load: {e}")
        if st.button("Retry", key=f"retry:{key}"):
            st.rerun()
        return
    elapsed = time.perf_counter() - started
    if elapsed > SLOW_SECTION:
        UI.warn("slow section", section=label, ms=round(1000 * elapsed))


def execution_line(execution: ExecutionSpec) -> str:
    fill = execution.fill
    commission, slippage = fill.commission_per_share, fill.slippage_bps
    parts = [
        f"cash ${execution.initial_cash:,.0f}",
        f"commission ${commission:g}/share" if commission else "no commission",
        f"slippage {slippage:g} bps" if slippage else "no slippage",
        f"limit fill {fill.prob_fill_on_limit:.0%}",
    ]
    if execution.intraday:
        parts.append(
            f"intraday, enter {execution.entry_delay_minutes} min after open, "
            f"flat {execution.squareoff_minutes_before_close} min before close"
        )
    else:
        parts.append("held overnight")
    return " &middot; ".join(parts)


def header(
    specs: dict[str, RunSpec],
    results: dict[str, tuple[str, Metrics]],
    names: dict[str, str],
    version: str,
    markets: dict[str, Comparison],
) -> str:
    spec = next(iter(specs.values()))
    compare = len(specs) == 2
    sessions = next(iter(results.values()))[1].sessions
    legs = []
    for label, s in specs.items():
        params = " &middot; ".join(
            f"{html.escape(forms.label(k).lower())} {html.escape(_value(v))}"
            for k, v in s.params.items()
            if k != "timeframe"
        )
        legs.append(
            f'<div class="t-meta">{mark(label, compare)}<b>{html.escape(names[label])}</b>'
            f" &middot; {params}</div>"
        )
    keys = " &middot; ".join(
        f"run {label.lower() + ' ' if compare else ''}{key[:8]}" for label, (key, _) in results.items()
    )
    left = (
        f'<div><div class="t-ticker">{html.escape(spec.ticker)}</div>'
        f'<div class="t-meta">{_day(spec.start)} &ndash; {_day(spec.end)}'
        f" &middot; {sessions:,} sessions</div>"
        f"{''.join(legs)}"
        f'<div class="t-meta">{execution_line(spec.execution)}</div>'
        f'<div class="t-ids">{keys} &middot; data {version[:8]}</div></div>'
    )
    return f'<div class="t-head">{left}{comparison(results, names, markets, spec)}</div>'


def _cells(values: list[tuple[float, str]]) -> str:
    return "".join(f"<td>{_fmt(v, fmt)}</td>" for v, fmt in values)


def comparison(
    results: dict[str, tuple[str, Metrics]],
    names: dict[str, str],
    markets: dict[str, Comparison],
    spec: RunSpec,
) -> str:
    compare = len(results) == 2
    market = next(iter(markets.values()))
    ref = html.escape(market.reference)
    head = "".join(f"<th>{h}</th>" for h, _, _ in COMPARE) + f"<th>&beta; {ref}</th><th>&rho; {ref}</th>"
    rows = []
    for label, (_, m) in results.items():
        e = markets[label].run
        values = [(float(get(m)), fmt) for _, get, fmt in COMPARE]
        values += [(e.beta, "{:.2f}"), (e.correlation, "{:.2f}")]
        name = f"{mark(label, compare)}{html.escape(names[label])}"
        rows.append(f'<tr class="run"><td>{name}</td>{_cells(values)}</tr>')
    cash = spec.execution.initial_cash
    for b in market.benchmarks:
        held = " hold" if b.ticker == spec.ticker else ""
        values = [
            (cash * b.total_return_pct / 100, "${:,.0f}"),
            (b.total_return_pct, "{:.2f}%"),
            (b.cagr_pct, "{:.2f}%"),
            (b.volatility_pct, "{:.2f}%"),
            (b.sharpe, "{:.2f}"),
            (b.max_drawdown_pct, "{:.2f}%"),
            (b.exposure.beta, "{:.2f}"),
            (b.exposure.correlation, "{:.2f}"),
        ]
        rows.append(f"<tr><td>{html.escape(b.ticker)}{held}</td>{_cells(values)}</tr>")
    return (
        f'<table class="t-cmp"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _group(
    heading: str, group: list[Line], results: dict[str, tuple[str, Metrics]], exits: frozenset[Exit]
) -> str:
    labels = list(results)
    compare = len(labels) == 2
    head = ""
    if compare:
        head = (
            "<thead><tr><th></th>"
            + "".join(f"<th>{lb}</th>" for lb in labels)
            + "<th>B &minus; A</th></tr></thead>"
        )
    rows = []
    for line in group:
        if not applies(line.metric, exits):
            continue
        text, get, fmt = line.label, line.get, line.fmt
        values = [float(get(results[lb][1])) for lb in labels]
        cells = "".join(f"<td>{_fmt(v, fmt)}</td>" for v in values)
        if compare:
            cells += f'<td class="diff">{_fmt(values[1] - values[0], fmt, signed=True)}</td>'
        rows.append(f"<tr><td>{html.escape(text)}</td>{cells}</tr>")
    for text, moment in MOMENTS.get(heading, ()):
        moments = [moment(results[lb][1]) for lb in labels]
        cells = "".join(f"<td>{_stamp(m)}</td>" for m in moments)
        if compare:
            cells += '<td class="diff"></td>'
        rows.append(f"<tr><td>{html.escape(text)}</td>{cells}</tr>")
    return f'<div class="t-group"><h5>{heading}</h5><table>{head}<tbody>{"".join(rows)}</tbody></table></div>'


def _stamp(moment: datetime | None) -> str:
    return "&ndash;" if moment is None else f"{moment.strftime(DATE)}<br>{moment:%H:%M:%S}"


SCALES: Final = (("T", 1e12), ("B", 1e9), ("M", 1e6))


def _cap(s: Snapshot | None) -> str:
    if s is None:
        return "&ndash;"
    if not s.plausible:
        return "n/a"
    v = s.market_cap
    if math.isnan(v):
        return "&ndash;"
    unit, size = next(((u, n) for u, n in SCALES if v >= n), ("", 1.0))
    return f"${v / size:,.2f}{unit}"


def _pe(s: Snapshot | None) -> str:
    if s is None:
        return "&ndash;"
    if not s.plausible:
        return "n/a"
    if math.isnan(s.pe):
        return "loss" if s.net_income <= 0 else "&ndash;"
    return f"{s.pe:,.1f}"


def valuation(v: Valuation) -> str:
    ends = (v.start, v.end)
    head = "".join(
        f"<th>{s.day.strftime(DATE) if s is not None else name}</th>"
        for s, name in zip(ends, ("Start", "End"), strict=True)
    )
    rows = [
        ("Market cap $", [_cap(s) for s in ends]),
        ("P/E, TTM", [_pe(s) for s in ends]),
    ]
    body = "".join(
        f"<tr><td>{text}</td>{''.join(f'<td>{c}</td>' for c in cells)}</tr>" for text, cells in rows
    )
    return (
        f'<div class="t-group"><h5>Valuation &middot; {html.escape(v.ticker)}</h5>'
        f"<table><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def performance(results: dict[str, tuple[str, Metrics]], value: Valuation, exits: frozenset[Exit]) -> str:
    groups = [_group(h, g, results, exits) for h, g in zip(GROUP_TITLES, GROUPS, strict=True)]
    block = valuation(value)
    if len(results) == 2:
        body = f'<div class="t-perf compare">{"".join(groups)}{block}</div>'
    else:
        body = (
            f'<div class="t-perf"><div>{groups[0]}{groups[1]}{block}</div>'
            f"<div>{groups[2]}{groups[3]}</div></div>"
        )
    return title("Performance") + body


HEAT_SCALE: Final = 90
HEAT_FLOOR: Final = 1.0


def shade(value: float, cap: float) -> str:
    target = HEAT_UP if value > 0 else HEAT_DOWN
    t = min(1.0, abs(value) / cap)
    r, g, b = (round(255 + (c - 255) * t) for c in target)
    return f"#{r:02x}{g:02x}{b:02x}"


def heat_cap(values: npt.NDArray[np.float64]) -> float:
    finite = np.abs(values[~np.isnan(values)])
    return max(HEAT_FLOOR, float(np.percentile(finite, HEAT_SCALE))) if finite.size else HEAT_FLOOR


def monthly(eq: pd.DataFrame) -> str:
    s = eq.set_index("date")["equity"]
    month_end = s.resample("ME").last()
    m = month_end.pct_change().fillna(month_end.iloc[0] / s.iloc[0] - 1) * 100
    year_end = s.resample("YE").last()
    y = year_end.pct_change().fillna(year_end.iloc[0] / s.iloc[0] - 1) * 100
    months = pd.DatetimeIndex(m.index)
    grid = pd.DataFrame({"year": months.year, "month": months.month, "ret": m.to_numpy()})
    pivot = grid.pivot_table(index="year", columns="month", values="ret").reindex(columns=range(1, 13))

    caps = {
        "": heat_cap(np.asarray(pivot, dtype=np.float64)),
        "year": heat_cap(np.asarray(y, dtype=np.float64)),
    }

    def cell(v: float, tone: str = "") -> str:
        if math.isnan(v):
            return "<td></td>"
        down = " down" if v < 0 else ""
        fill = "" if v == 0 else f' style="background:{shade(v, caps[tone])}"'
        return f'<td class="{tone}{down}"{fill}>{_fmt(v, "{:.1f}", signed=True)}</td>'

    head = "".join(f"<th>{n}</th>" for n in MONTHS) + "<th>Year</th>"
    body = "".join(
        f"<tr><td>{year}</td>{''.join(cell(float(v)) for v in r)}{cell(float(y.iloc[i]), 'year')}</tr>"
        for i, (year, r) in enumerate(pivot.iterrows())
    )
    return f'<table class="t-months"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>'


TRADE_COLUMNS: Final = {
    "trade_id": None,
    "side": st.column_config.TextColumn("Side", width=56),
    "quantity": st.column_config.NumberColumn("Qty", format="%,d", width=64),
    "entry_time": st.column_config.DatetimeColumn("Entry", format=GRID_STAMP, width=156),
    "entry_price": st.column_config.NumberColumn("Entry px", format="%.2f", width=72),
    "exit_time": st.column_config.DatetimeColumn("Exit", format=GRID_STAMP, width=156),
    "exit_price": st.column_config.NumberColumn("Exit px", format="%.2f", width=72),
    "exit_tag": st.column_config.TextColumn("Exit on", width=56),
    "duration": st.column_config.TextColumn("Held", width=72),
    "gross_pnl": None,
    "commission": st.column_config.NumberColumn("Comm $", format="%,.2f", width=56),
    "net_pnl": st.column_config.NumberColumn("Net $", format="%,.2f", width=80),
    "return_pct": st.column_config.NumberColumn("Ret %", format="%.2f", width=56),
    "mae_pct": st.column_config.NumberColumn("MAE %", format="%.2f", width=56),
    "mfe_pct": st.column_config.NumberColumn("MFE %", format="%.2f", width=56),
    "open": None,
}
ORDER_COLUMNS: Final = {
    "order_id": st.column_config.NumberColumn("#", format="%d", width="small"),
    "submitted": st.column_config.DatetimeColumn("Submitted", format=GRID_STAMP),
    "closed": st.column_config.DatetimeColumn("Closed", format=GRID_STAMP),
    "price": st.column_config.NumberColumn("Price", format="%.2f"),
    "fill_price": st.column_config.NumberColumn("Fill px", format="%.2f"),
    "quantity": st.column_config.NumberColumn("Qty", format="%,d"),
}


def held(d: pd.Timedelta) -> str:
    minutes = int(d.total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return f"{days}d {hours:02d}h" if days else f"{hours}h {mins:02d}m"


@st.cache_data(ttl=300)
def space(strategy: str, ticker: str, end: date | None) -> StrategySpace:
    return client().space(strategy, ticker, end)


def execution_form(intraday_allowed: bool = True) -> ExecutionSpec:
    with st.expander("Execution", expanded=False):
        commission = 0.0
        if st.toggle("Commission", value=False):
            commission = st.number_input("Commission ($/share)", 0.0, 1.0, 0.005, 0.001, format="%.4f")
        slippage = 0.0
        if st.toggle("Slippage", value=False):
            slippage = st.number_input("Slippage (bps)", 0.0, 1_000.0, 1.0, 0.5)
        prob_limit = st.slider("Limit fill probability on touch", 0.0, 1.0, 1.0, 0.05)
        intraday = intraday_allowed and st.toggle("Intraday (flat at every close)")
        delay, squareoff = 0, 5
        if intraday:
            delay = st.number_input("Entry delay after open (min)", 0, 390, 0, 5)
            squareoff = st.number_input("Square-off before close (min)", 1, 390, 5, 1)
    return ExecutionSpec(
        fill=FillModel(commission_per_share=commission, slippage_bps=slippage, prob_fill_on_limit=prob_limit),
        intraday=intraday,
        entry_delay_minutes=int(delay),
        squareoff_minutes_before_close=int(squareoff),
    )


def _span(first: date, last: date, scope: str) -> tuple[date, date]:
    lo, hi, preset = f"{scope}:from", f"{scope}:to", f"{scope}:preset:{first}:{last}"
    offered = forms.presets(first, last)
    stored = lo in st.session_state
    wanted = (st.session_state.get(lo, first), st.session_state.get(hi, last))
    st.session_state[lo] = min(max(wanted[0], first), last)
    st.session_state[hi] = min(max(wanted[1], st.session_state[lo]), last)
    adjusted = stored and (st.session_state[lo], st.session_state[hi]) != wanted

    def pick() -> None:
        choice = st.session_state[preset]
        if choice is None:
            return
        st.session_state[lo], st.session_state[hi] = forms.preset_span(choice, first, last)

    def edited() -> None:
        st.session_state[preset] = None

    st.segmented_control("Range", offered, key=preset, on_change=pick, label_visibility="collapsed")
    left, right = st.columns(2)
    start = left.date_input(
        "From", min_value=first, max_value=st.session_state[hi], format="DD/MM/YYYY", key=lo, on_change=edited
    )
    end = right.date_input(
        "To", min_value=start, max_value=last, format="DD/MM/YYYY", key=hi, on_change=edited
    )
    if adjusted:
        st.caption(f"Data covers {first.strftime(DATE)} to {last.strftime(DATE)}; dates adjusted.")
    return start, end


def backtest_form(cat: Catalog, strats: dict[str, StrategyInfo]) -> dict[str, RunSpec] | None:
    info = {t.ticker: t for t in cat.tickers}
    tickers = list(info)
    ticker = st.selectbox("Ticker", tickers, index=tickers.index("AAPL") if "AAPL" in tickers else 0)
    span = _span(info[ticker].first_session, info[ticker].last_session, "dates:backtest")
    compare = st.toggle("Compare two strategies", value=False)
    runs: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for label in LABELS if compare else LABELS[:1]:
        if compare:
            st.subheader(f"Strategy {label}")
        names = list(strats)
        name = st.selectbox(
            "Strategy",
            names,
            index=LABELS.index(label) % len(names),
            format_func=lambda n: strats[n].title,
            key=f"strategy:{label}",
        )
        resolved = space(name, ticker, span[1])
        params = forms.strategy(resolved.config_schema, resolved.space, f"{label}:{name}")
        runs[label] = (name, params)
    execution = execution_form(all(strats[name].intraday for name, _ in runs.values()))
    ready = all(params is not None for _, params in runs.values())
    label_text = "Run comparison" if compare else "Run backtest"
    if not st.button(label_text, type="primary", width="stretch", disabled=not ready):
        return None
    try:
        return {
            label: RunSpec(
                ticker=Ticker(ticker),
                start=span[0],
                end=span[1],
                strategy=name,
                params=params or {},
                execution=forms.capital(name, params or {}, execution),
            )
            for label, (name, params) in runs.items()
        }
    except ValidationError as e:
        st.error("; ".join(str(err["msg"]) for err in e.errors()))
        return None


def sweep_form(cat: Catalog, strats: dict[str, StrategyInfo]) -> SweepSpec | None:
    tickers = [t.ticker for t in cat.tickers]
    universe, pool, note = basket.universe(client(), tickers)
    notes = [note]
    span = _span(cat.first_session, cat.last_session, "dates:sweep")
    if universe == basket.CUSTOM:
        chosen = st.multiselect("Tickers", tickers, key="basket:custom", placeholder="Choose tickers")
    else:
        pool, class_notes = basket.classify(client(), pool)
        notes += class_notes
        pool, vol_notes = basket.volatility(client(), pool, span[0])
        notes += vol_notes
        fingerprint = hashlib.blake2b("|".join(pool).encode(), digest_size=8).hexdigest()
        with st.expander(f"Tickers · {len(pool):,}"):
            chosen = st.multiselect(
                "Tickers",
                tickers,
                default=pool,
                key=f"basket:tickers:{fingerprint}",
                label_visibility="collapsed",
            )
    basket.summary(chosen, notes)
    if not chosen:
        notice("The basket is empty.")
        return None
    name = st.selectbox("Strategy", list(strats), format_func=lambda n: strats[n].title, key="sweep:strategy")
    resolved = space(name, chosen[0], span[1])
    typed = resolved.space
    timeframes = list(typed.timeframes)
    if not timeframes:
        st.warning(f"No precomputed features cover {strats[name].title} for {chosen[0]}.")
        return None
    tf = st.selectbox("Timeframe", timeframes, index=len(timeframes) - 1, key="sweep:timeframe")
    choices = typed.choices[tf]
    grid: dict[str, list[int]] = {}
    for param, domain in choices.items():
        options = typed.options(param, domain, {**choices, **grid})
        key = f"sweep:{name}:{tf}:{param}"
        if key in st.session_state:
            st.session_state[key] = [v for v in st.session_state[key] if v in options]
        grid[param] = st.multiselect(
            forms.label(param),
            options,
            default=None if key in st.session_state else options,
            key=key,
        )
    combos = typed.count(grid)
    runs = f"{len(chosen):,} tickers {TIMES} {combos:,} valid combinations = {len(chosen) * combos:,} runs"
    st.caption(runs)
    schema = resolved.config_schema
    fixed = forms.fields(
        schema["properties"], schema.get("$defs", {}), f"sweep:{name}", skip={"timeframe", *grid}
    )
    execution = execution_form(strats[name].intraday)
    ready = combos > 0
    if not st.button("Run sweep", type="primary", width="stretch", disabled=not ready):
        return None
    try:
        return SweepSpec(
            tickers=tuple(Ticker(t) for t in chosen),
            start=span[0],
            end=span[1],
            strategy=name,
            timeframe=tf,
            grid={k: tuple(v) for k, v in grid.items()},
            params=fixed,
            execution=forms.capital(name, fixed, execution),
        )
    except ValidationError as e:
        st.error("; ".join(str(err["msg"]) for err in e.errors()))
        return None


MAX_CANDLES: Final = 3_000
TABLE_ROWS: Final = 16
OHLCV: Final = frozenset({"time", "open", "high", "low", "close", "volume", "equity"})
BUY_GLYPH: Final = "&#9650;"
SELL_GLYPH: Final = "&#9661;"


def _height(rows: int) -> int:
    return CELL * (min(rows, TABLE_ROWS) + 1) + 2


def equity_block(results: dict[str, tuple[str, Metrics]]) -> None:
    curves = {label: table(key, Table.EQUITY) for label, (key, _) in results.items()}
    if len(results) == 2:
        keys = "".join(key_line(lb, c, d) for lb, (c, d) in zip(curves, SERIES, strict=False))
        keys += key_line("Drawdown A", DOWN)
    else:
        keys = key_line("Equity", INK) + key_line("Drawdown", DOWN)
    st.html(title("Equity", "session closes", keys))
    st.plotly_chart(charts.equity(curves), width="stretch", key="equity", config=charts.CONFIG)


@st.cache_data(max_entries=64, show_spinner=False)
def run_days(key: str) -> tuple[list[date], date | None]:
    found = client().days(key)
    return found.days, found.recent


@st.cache_data(max_entries=32, show_spinner=False)
def price_window(key: str, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    bars, factor = client().window(key, Table.BARS, start, end, MAX_CANDLES)
    fills, _ = client().window(key, Table.FILLS, start, end, MAX_CANDLES)
    return bars, fills, factor


@st.cache_data(max_entries=16, show_spinner=False)
def blotter_frame(key: str) -> pd.DataFrame:
    trades = table(key, Table.TRADES)
    return trades.assign(duration=trades["duration"].map(held)) if len(trades) else trades


@st.cache_data(max_entries=16, show_spinner=False)
def trades_csv(key: str) -> str:
    trades = table(key, Table.TRADES)
    stamped = {c: trades[c].dt.strftime(STAMP) for c in trades.select_dtypes("datetime").columns}
    return trades.assign(**stamped).to_csv(index=False)


@st.fragment
def price_view(label: str, key: str, spec: RunSpec) -> None:
    tf = spec.timeframe
    days, recent = run_days(key)
    if not days:
        notice("No bars were stored for this run.")
        return
    heading = st.empty()
    chart = st.empty()
    view = st.slider(
        "Window",
        min_value=days[0],
        max_value=days[-1],
        value=(recent or days[0], days[-1]),
        format=SLIDER_DATE,
        key=f"window:{label}",
        label_visibility="collapsed",
    )
    bars, fills, factor = price_window(key, view[0], view[1])
    indicators = [c for c in bars.columns if c not in OHLCV]
    keys = "".join(key_line(forms.label(n), c, d) for n, (c, d) in zip(indicators, INDICATORS, strict=False))
    keys += key_marker("Buy", BUY_GLYPH) + key_marker("Sell", SELL_GLYPH)
    caption = f"{html.escape(spec.ticker)} &middot; {tf} &middot; {len(bars):,} candles"
    if factor > 1:
        caption += f" &middot; each aggregates {factor:,} bars"
    heading.html(title("Price", caption, keys))
    chart.plotly_chart(
        charts.price(bars, fills, indicators, tf is not Timeframe.D1),
        width="stretch",
        key=f"price:{label}",
        config=charts.CONFIG,
    )


def ledger_link(label: str, key: str, spec: RunSpec) -> None:
    status = client().ledger(key)
    if status.state is LedgerState.ABSENT:
        status = client().build_ledger(key)
    if status.state is LedgerState.BUILDING:
        ledger_wait(label, key, spec)
    elif status.state is LedgerState.FAILED:
        st.caption(f"MTM ledger failed: {status.detail}")
        if st.button("Retry MTM ledger", key=f"mtm:retry:{label}"):
            client().build_ledger(key)
            st.rerun()
    else:
        ledger_button(label, key, spec, status.size)


def ledger_button(label: str, key: str, spec: RunSpec, size: int | None) -> None:
    st.download_button(
        f"MTM ledger · {(size or 0) / 1e6:,.1f} MB",
        lambda: client().ledger_file(key),
        filename(spec),
        mime=MEDIA,
        key=f"mtm:{label}",
    )


@st.fragment(run_every=LEDGER_POLL)
def ledger_wait(label: str, key: str, spec: RunSpec) -> None:
    try:
        status = client().ledger(key)
    except ApiError:
        st.caption("MTM ledger status unavailable; retrying.")
        return
    if status.state is LedgerState.BUILDING:
        st.caption("Building MTM ledger…")
    elif status.state is LedgerState.READY:
        ledger_button(label, key, spec, status.size)
    else:
        st.caption(f"MTM ledger failed: {status.detail}")


def strategy_view(label: str, key: str, spec: RunSpec) -> None:
    price_view(label, key, spec)
    trades = blotter_frame(key)
    orders = table(key, Table.ORDERS)
    st.html(title("Blotter"))
    blotter, order_tab = st.tabs([f"Trades {len(trades):,}", f"Orders {len(orders):,}"])
    with blotter:
        st.dataframe(
            trades,
            width="stretch",
            height=_height(len(trades)),
            row_height=CELL,
            hide_index=True,
            column_config=TRADE_COLUMNS,
        )
        csv, mtm = st.columns([1, 6])
        csv.download_button(
            "Trades CSV",
            trades_csv(key),
            f"trades_{label}_{key[:8]}.csv",
            key=f"dl:{label}",
        )
        with mtm:
            ledger_link(label, key, spec)
    with order_tab:
        st.dataframe(
            orders,
            width="stretch",
            height=_height(len(orders)),
            row_height=CELL,
            hide_index=True,
            column_config=ORDER_COLUMNS,
        )
    st.html(title("Monthly returns", "%") + monthly(table(key, Table.EQUITY)))


@st.fragment
def detail(results: dict[str, tuple[str, Metrics]], specs: dict[str, RunSpec], names: dict[str, str]) -> None:
    labels = list(results)
    label = labels[0]
    if len(labels) == 2:
        st.html(title("Strategy"))
        label = (
            st.segmented_control(
                "Strategy",
                labels,
                default=labels[0],
                format_func=lambda lb: f"{lb}  {names[lb]}",
                label_visibility="collapsed",
                key="detail",
            )
            or labels[0]
        )
    strategy_view(label, results[label][0], specs[label])


def show_runs(
    results: dict[str, tuple[str, Metrics]],
    specs: dict[str, RunSpec],
    cat: Catalog,
    strats: dict[str, StrategyInfo],
) -> None:
    names = {label: f"{strats[s.strategy].title} · {s.timeframe}" for label, s in specs.items()}
    tag = next(iter(results.values()))[0][:8]

    def top() -> None:
        markets = {label: market(key) for label, (key, _) in results.items()}
        st.html(header(specs, results, names, cat.data_version, markets))

    def panel() -> None:
        exits = frozenset(e for s in specs.values() for e in strats[s.strategy].exits)
        st.html(performance(results, worth(next(iter(results.values()))[0]), exits))

    guarded("summary", f"top:{tag}", top)
    left, right = st.columns([7, 5])
    with left:
        guarded("equity chart", f"equity:{tag}", lambda: equity_block(results))
    with right:
        guarded("performance", f"perf:{tag}", panel)
    guarded("run detail", f"detail:{tag}", lambda: detail(results, specs, names))


SWEEP_COLUMNS: Final = {
    "key": None,
    "net_pnl": st.column_config.NumberColumn("Net P&L $", format="%,.0f"),
    "total_return_pct": st.column_config.NumberColumn("Return %", format="%.2f"),
    "cagr_pct": st.column_config.NumberColumn("CAGR %", format="%.2f"),
    "volatility_pct": st.column_config.NumberColumn("Ann. vol %", format="%.2f"),
    "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
    "sortino": None,
    "max_drawdown_pct": st.column_config.NumberColumn("Max DD %", format="%.2f"),
    "calmar": None,
    "trades": st.column_config.NumberColumn("Trades", format="%,d"),
    "first_signal": st.column_config.DatetimeColumn("First signal", format=GRID_STAMP),
    "last_signal": st.column_config.DatetimeColumn("Last signal", format=GRID_STAMP),
    "win_rate_pct": st.column_config.NumberColumn("Win %", format="%.1f"),
    "profit_factor": None,
    "exposure_pct": st.column_config.NumberColumn("Exposure %", format="%.1f"),
    "buy_hold_return_pct": st.column_config.NumberColumn("Buy & hold %", format="%.1f"),
}


def start_sweep(spec: SweepSpec) -> str | None:
    try:
        return client().start_sweep(spec).key
    except ApiError as e:
        st.error(str(e))
        return None


@st.fragment(run_every=SWEEP_POLL)
def sweep_progress(key: str) -> None:
    try:
        status = client().sweep(key)
    except ApiError:
        st.caption(f"Sweep {key[:8]} status unavailable; retrying.")
        return
    if status.state != "running":
        st.rerun()
    total = max(1, status.total)
    st.progress(status.done / total, f"Sweep {key[:8]} · {status.done:,} of {total:,} runs")


SWEEP_STATS: Final[tuple[tuple[str, Callable[[pd.DataFrame], float], str], ...]] = (
    ("Runs", lambda f: float(len(f)), "{:,.0f}"),
    ("Profitable", lambda f: 100 * float((f["total_return_pct"] > 0).mean()), "{:.0f}%"),
    ("Median return", lambda f: float(f["total_return_pct"].median()), "{:.2f}%"),
    ("Best return", lambda f: float(f["total_return_pct"].max()), "{:.2f}%"),
    ("Median Sharpe", lambda f: float(f["sharpe"].median()), "{:.2f}"),
    ("Best Sharpe", lambda f: float(f["sharpe"].max()), "{:.2f}"),
    ("Worst DD", lambda f: float(f["max_drawdown_pct"].min()), "{:.2f}%"),
)


def sweep_stats(frame: pd.DataFrame) -> str:
    head = "".join(f"<th>{h}</th>" for h, _, _ in SWEEP_STATS)
    cells = "".join(f"<td>{_fmt(fn(frame), fmt)}</td>" for _, fn, fmt in SWEEP_STATS)
    return (
        f'<table class="t-cmp"><thead><tr>{head}</tr></thead>'
        f'<tbody><tr class="run">{cells}</tr></tbody></table>'
    )


def run_label(row: pd.Series, swept: list[str], multi: bool) -> str:
    values = "/".join(str(row[c]) for c in swept)
    return f"{row['ticker']} {values}".strip() if multi or not values else values


@st.cache_resource(max_entries=16)
def sweep_paths(key: str) -> pd.DataFrame:
    return client().sweep_paths(key)


@st.fragment
def show_sweep(key: str, spec: SweepSpec, cat: Catalog, strats: dict[str, StrategyInfo]) -> None:
    frame = client().sweep_summary(key)
    if frame.empty:
        notice("The sweep produced no runs.")
        return
    swept = [c for c in frame.columns if c in spec.grid]
    measures = [c for c in SWEEP_COLUMNS if c in frame.columns and c != "key"]
    fixed = [
        c for c in frame.columns if c not in {"ticker", "key", *swept, *measures} and frame[c].nunique() == 1
    ]
    exits = strats[spec.strategy].exits
    columns = {c: (cfg if applies(c, exits) else None) for c, cfg in SWEEP_COLUMNS.items()}
    shown = [c for c in frame.columns if c not in fixed and columns.get(c, True) is not None]
    strategy_title = strats[spec.strategy].title
    best = frame.sort_values("sharpe", ascending=False, na_position="last").reset_index(drop=True)
    count = len(spec.tickers)
    held_fixed = " · ".join(f"{forms.label(c)} {forms.label(frame[c].iloc[0])}" for c in fixed)
    runs = f"{count} ticker{'s' if count != 1 else ''} &middot; {len(frame):,} runs"
    fixed_line = (
        f'<div class="t-meta">held fixed &middot; {html.escape(held_fixed.lower())}</div>' if fixed else ""
    )
    st.html(
        '<div class="t-head"><div>'
        f'<div class="t-ticker">{html.escape(strategy_title)} sweep</div>'
        f'<div class="t-meta">{_day(spec.start)} &ndash; {_day(spec.end)} &middot; '
        f"{spec.timeframe} &middot; {runs}</div>"
        f"{fixed_line}"
        f'<div class="t-ids">sweep {key[:8]}</div></div>{sweep_stats(frame)}</div>'
    )
    multi = count > 1
    leaders = {str(r["key"]): run_label(r, swept, multi) for _, r in best.head(3).iterrows()}
    profitable = float((frame["total_return_pct"] > 0).mean())
    left, right = st.columns([7, 5])
    with left:
        keys = (
            key_line("Median", INK)
            + key_line("Top 3 by Sharpe", SECONDARY)
            + key_line(REFERENCE, MUTED, "dash")
            + key_line("Each run", FAINT)
        )
        st.html(title("Equity, all runs", "cumulative return", keys))
        st.plotly_chart(
            charts.paths(sweep_paths(key), [str(k) for k in frame["key"]], leaders, REFERENCE),
            width="stretch",
            key="sweep:paths",
            config=charts.CONFIG,
        )
    with right:
        st.html(title("Total return, all runs", f"{len(frame):,} runs &middot; {profitable:.0%} profitable"))
        st.plotly_chart(
            charts.distribution(frame["total_return_pct"], "%"),
            width="stretch",
            key="sweep:distribution",
            config=charts.CONFIG,
        )
    if count == 1 and len(swept) == 2:
        left, right = st.columns([7, 5])
        with left:
            st.html(
                title("Sharpe", f"{forms.label(swept[0]).lower()} &times; {forms.label(swept[1]).lower()}")
            )
            st.plotly_chart(
                charts.heatmap(frame, swept[0], swept[1], "sharpe"),
                width="stretch",
                key="sweep:heatmap",
                config=charts.CONFIG,
            )
        with right:
            top = [*swept, "sharpe", "cagr_pct", "max_drawdown_pct", "trades"]
            st.html(title("Best", "by Sharpe"))
            st.dataframe(
                best[top].head(TABLE_ROWS - 3),
                width="stretch",
                height=_height(TABLE_ROWS - 3),
                row_height=CELL,
                hide_index=True,
                column_config=columns,
            )
    st.html(title("Runs", f"{len(best):,} &middot; by Sharpe"))
    st.dataframe(
        best[shown],
        width="stretch",
        height=_height(len(best)),
        row_height=CELL,
        hide_index=True,
        column_config=columns,
    )
    st.download_button("Sweep CSV", best.drop(columns=["key"]).to_csv(index=False), f"sweep_{key[:8]}.csv")
    labels = [
        " · ".join(
            part
            for part in (
                str(r.ticker),
                " ".join(f"{c}={getattr(r, c)}" for c in swept),
                f"Sharpe {_plain(float(str(r.sharpe)), '{:.2f}')}",
            )
            if part
        )
        for r in best.itertuples()
    ]
    st.html(title("Open a run"))
    pick = st.selectbox(
        "Open a run",
        range(len(best)),
        format_func=lambda i: labels[i],
        index=None,
        placeholder="Choose a run to open",
        label_visibility="collapsed",
    )
    if pick is not None:
        run = client().detail(str(best.loc[pick, "key"]))
        show_runs({"A": (run.key, run.metrics)}, {"A": run.spec}, cat, strats)


def restore() -> tuple[dict[str, tuple[str, Metrics]], dict[str, RunSpec]] | None:
    keys = {label: st.query_params[name] for label, name in LINKS.items() if name in st.query_params}
    if LABELS[0] not in keys:
        return None
    try:
        found = {label: client().detail(key) for label, key in keys.items()}
        return (
            {label: (d.key, d.metrics) for label, d in found.items()},
            {label: d.spec for label, d in found.items()},
        )
    except GoneError:
        raise
    except ApiError:
        return None


def sweep_page(sweep_spec: SweepSpec | None, cat: Catalog, strats: dict[str, StrategyInfo]) -> None:
    if sweep_spec is not None and (key := start_sweep(sweep_spec)) is not None:
        st.session_state["sweep"] = (key, sweep_spec)
        st.query_params.from_dict({SWEEP_LINK: key})
    if "sweep" not in st.session_state and SWEEP_LINK in st.query_params:
        shared = str(st.query_params[SWEEP_LINK])
        try:
            st.session_state["sweep"] = (shared, client().sweep_spec(shared))
        except GoneError as e:
            st.query_params.clear()
            notice(f"This link's {e}.")
            return
        except ApiError:
            st.query_params.clear()
    current = st.session_state.get("sweep")
    if current is None:
        notice("Choose tickers, a strategy and the parameter values to sweep, then run the sweep.")
        return
    key, spec = current
    try:
        status = client().sweep(key)
    except ApiError:
        notice(f"Sweep {key[:8]} is no longer known to the server; run it again.")
        return
    if status.state == "running":
        sweep_progress(key)
        return
    if status.state == "stopped":
        notice(f"Sweep {key[:8]} was stopped after {status.done:,} of {status.total:,} runs.")
        return
    if status.state == "failed":
        st.error(f"Sweep failed: {status.error}")
        return
    for skipped in status.skipped:
        st.warning(f"{skipped.ticker} skipped: {skipped.reason}")
    guarded("sweep results", f"sweep:{key[:8]}", lambda: show_sweep(key, spec, cat, strats))


def backtest_page(specs: dict[str, RunSpec] | None, cat: Catalog, strats: dict[str, StrategyInfo]) -> None:
    if specs is not None:
        with st.spinner("Running"), ThreadPoolExecutor(len(specs)) as pool:
            futures = {label: pool.submit(client().run, s) for label, s in specs.items()}
            try:
                summaries = {label: f.result() for label, f in futures.items()}
                st.session_state["results"] = {label: (r.key, r.metrics) for label, r in summaries.items()}
                st.session_state["specs"] = specs
                st.query_params.from_dict(
                    {LINKS[label]: key for label, (key, _) in st.session_state["results"].items()}
                )
            except ApiError as e:
                st.error(str(e))
                return
    if "results" not in st.session_state:
        try:
            linked = restore()
        except GoneError as e:
            st.query_params.clear()
            notice(f"This link's {e}.")
            return
        if linked is not None:
            st.session_state["results"], st.session_state["specs"] = linked
    results: dict[str, tuple[str, Metrics]] | None = st.session_state.get("results")
    if not results:
        notice("Choose a ticker, a date range and a strategy in the sidebar, then run the backtest.")
        return
    show_runs(results, st.session_state["specs"], cat, strats)


def activity() -> tuple[list[SweepStatus], list[str]]:
    try:
        return client().running_sweeps(), client().building_ledgers()
    except ApiError:
        return [], []


def background() -> None:
    if any(activity()):
        background_live()


@st.fragment(run_every=BACKGROUND_POLL)
def background_live() -> None:
    sweeps, ledgers = activity()
    if not sweeps and not ledgers:
        return
    st.html(title("Background"))
    for s in sweeps:
        total = max(1, s.total)
        name = STRATEGIES[s.strategy].title if s.strategy in STRATEGIES else s.strategy
        st.progress(
            s.done / total,
            f"Sweep {s.key[:8]} · {name} {s.timeframe} · {s.tickers:,} tickers · {s.done:,}/{total:,}",
        )
        if st.button("Stop", key=f"stop:{s.key}", width="stretch"):
            try:
                client().stop_sweep(s.key)
            except ApiError as e:
                st.caption(f"Could not stop: {e}")
            st.rerun(scope="fragment")
    for key in ledgers:
        st.caption(f"MTM ledger · run {key[:8]} · building")


def main() -> None:
    started = time.perf_counter()
    mode = render()
    UI.info("render", mode=mode, ms=round(1000 * (time.perf_counter() - started)))


def render() -> str:
    st.set_page_config(page_title="tape", layout="wide", initial_sidebar_state=SIDEBAR)
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        cat, strats = catalog(), strategies()
    except (ApiError, OSError, httpx2.HTTPError) as e:
        st.error(f"API unavailable at {API_URL}: {e}")
        return "unavailable"
    with st.sidebar:
        st.html(
            '<div class="t-brand"><div class="word">tape</div>'
            '<div class="tag">US equity backtests</div></div>'
        )
        if MODE not in st.session_state:
            st.session_state[MODE] = "Sweep" if SWEEP_LINK in st.query_params else "Backtest"
        mode = st.segmented_control("Mode", ["Backtest", "Sweep"], key=MODE, label_visibility="collapsed")
        sweep = sweep_form(cat, strats) if mode == "Sweep" else None
        runs = backtest_form(cat, strats) if mode != "Sweep" else None
        background()
    if mode == "Sweep":
        sweep_page(sweep, cat, strats)
    else:
        backtest_page(runs, cat, strats)
    return str(mode)


main()
