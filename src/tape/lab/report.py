from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from tape.core.types import DATE
from tape.engine.costs import charged
from tape.engine.exits import applies
from tape.lab.markup import day, fmt, mark, title, value_text
from tape.lab.schema import label
from tape.ui.theme import HEAT_DOWN, HEAT_UP

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from tape.benchmarks import Comparison
    from tape.engine.backtest import ExecutionSpec
    from tape.engine.costs import Cost
    from tape.engine.exits import Exit
    from tape.engine.reports import Metrics
    from tape.fundamentals import Snapshot, Valuation
    from tape.runner import RunSpec

type Row = tuple[str, Callable[[Metrics], float], str]


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    get: Callable[[Metrics], float]
    fmt: str
    metric: str = ""


type Moment = tuple[str, Callable[[Metrics], datetime | None]]

GROUPS: Final[list[list[Line]]] = [
    [
        Line("Net P&L $", lambda m: m.net_pnl, "{:,.0f}"),
        Line("Total return %", lambda m: m.total_return_pct, "{:.2f}"),
        Line("CAGR %", lambda m: m.cagr_pct, "{:.2f}"),
        Line("Max loss on capital %", lambda m: m.max_capital_loss_pct, "{:.2f}"),
    ],
    [
        Line("Volatility, ann. %", lambda m: m.volatility_pct, "{:.2f}"),
        Line("Sharpe, ann.", lambda m: m.sharpe, "{:.2f}"),
        Line("Sortino, ann.", lambda m: m.sortino, "{:.2f}"),
        Line("Max drawdown %", lambda m: m.max_drawdown_pct, "{:.2f}"),
        Line("Longest DD, sessions", lambda m: m.max_drawdown_sessions, "{:,.0f}"),
        Line("Calmar", lambda m: m.calmar, "{:.2f}"),
        Line("Time in market %", lambda m: m.time_in_market_pct, "{:.1f}"),
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
        Line("Commission $", lambda m: m.total_commission, "{:,.0f}", "total_commission"),
        Line("Final equity $", lambda m: m.final_equity, "{:,.0f}"),
    ],
]

CLIP_DAYS: Final = 7
MOMENTS: Final[dict[str, tuple[Moment, ...]]] = {
    "Trades": (("First signal", lambda m: m.first_signal), ("Last signal", lambda m: m.last_signal))
}


GROUP_TITLES: Final = ("Returns", "Risk", "Trades", "Costs")
COMPARE: Final[tuple[Row, ...]] = (
    ("Net P&L", lambda m: m.net_pnl, "${:,.0f}"),
    ("Total return", lambda m: m.total_return_pct, "{:.2f}%"),
    ("CAGR", lambda m: m.cagr_pct, "{:.2f}%"),
    ("Ann. vol", lambda m: m.volatility_pct, "{:.2f}%"),
    ("Ann. Sharpe", lambda m: m.sharpe, "{:.2f}"),
    ("Max DD", lambda m: m.max_drawdown_pct, "{:.2f}%"),
)
MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


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
    for leg, s in specs.items():
        params = " &middot; ".join(
            f"{html.escape(label(k).lower())} {html.escape(value_text(v))}"
            for k, v in s.params.items()
            if k != "timeframe"
        )
        legs.append(
            f'<div class="t-meta">{mark(leg, compare)}<b>{html.escape(names[leg])}</b>'
            f" &middot; {params}</div>"
        )
    keys = " &middot; ".join(
        f"run {leg.lower() + ' ' if compare else ''}{key[:8]}" for leg, (key, _) in results.items()
    )
    traded = next(iter(results.values()))[1]
    start, end = traded.start or spec.start, traded.end or spec.end
    clipped = (start - spec.start).days > CLIP_DAYS or (spec.end - end).days > CLIP_DAYS
    requested = f" &middot; requested {day(spec.start)} &ndash; {day(spec.end)}" if clipped else ""
    left = (
        f'<div><div class="t-ticker">{html.escape(spec.ticker)}</div>'
        f'<div class="t-meta">{day(start)} &ndash; {day(end)}'
        f" &middot; {sessions:,} sessions{requested}</div>"
        f"{''.join(legs)}"
        f'<div class="t-meta">{execution_line(spec.execution)}</div>'
        f'<div class="t-ids">{keys} &middot; data {version[:8]}</div></div>'
    )
    return f'<div class="t-head">{left}{comparison(results, names, markets, spec)}</div>'


def _cells(values: list[tuple[float, str]]) -> str:
    return "".join(f"<td>{fmt(v, pattern)}</td>" for v, pattern in values)


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
    for leg, (_, m) in results.items():
        e = markets[leg].run
        values = [(float(get(m)), pattern) for _, get, pattern in COMPARE]
        values += [(e.beta, "{:.2f}"), (e.correlation, "{:.2f}")]
        name = f"{mark(leg, compare)}{html.escape(names[leg])}"
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
    heading: str,
    group: list[Line],
    results: dict[str, tuple[str, Metrics]],
    exits: frozenset[Exit],
    costs: frozenset[Cost],
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
        if not (applies(line.metric, exits) and charged(line.metric, costs)):
            continue
        text, get, pattern = line.label, line.get, line.fmt
        values = [float(get(results[lb][1])) for lb in labels]
        cells = "".join(f"<td>{fmt(v, pattern)}</td>" for v in values)
        if compare:
            cells += f'<td class="diff">{fmt(values[1] - values[0], pattern, signed=True)}</td>'
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


def performance(
    results: dict[str, tuple[str, Metrics]], value: Valuation, exits: frozenset[Exit], costs: frozenset[Cost]
) -> str:
    groups = [_group(h, g, results, exits, costs) for h, g in zip(GROUP_TITLES, GROUPS, strict=True)]
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
        return f'<td class="{tone}{down}"{fill}>{fmt(v, "{:.1f}", signed=True)}</td>'

    head = "".join(f"<th>{n}</th>" for n in MONTHS) + "<th>Year</th>"
    body = "".join(
        f"<tr><td>{year}</td>{''.join(cell(float(v)) for v in r)}{cell(float(y.iloc[i]), 'year')}</tr>"
        for i, (year, r) in enumerate(pivot.iterrows())
    )
    return f'<table class="t-months"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>'


def held(d: pd.Timedelta) -> str:
    minutes = int(d.total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return f"{days}d {hours:02d}h" if days else f"{hours}h {mins:02d}m"
