from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd

from tape.lab.markup import MINUS, fmt
from tape.lab.nav import link
from tape.lab.views import Column, cell, table
from tape.portfolio_index import SCHEMA

if TYPE_CHECKING:
    from collections.abc import Callable, Collection


class Group(StrEnum):
    IDENTITY = "Identity"
    BOOK = "Book"
    AGAINST = "Against"
    CAPITAL = "Capital"
    EXECUTION = "Execution"
    SOURCE = "Source"


DEFAULT_GROUPS: Final = (Group.IDENTITY, Group.BOOK, Group.AGAINST, Group.CAPITAL)
RHO: Final = "\u03c1"

COLUMNS: Final[dict[str, tuple[Group, Column]]] = {
    "strategy_title": (Group.IDENTITY, Column("Strategy", "")),
    "market": (Group.IDENTITY, Column("Market", "")),
    "timeframe": (Group.IDENTITY, Column("Timeframe", "")),
    "tickers": (Group.IDENTITY, Column("Tickers", "{:,.0f}")),
    "start": (Group.IDENTITY, Column("Start", "")),
    "end": (Group.IDENTITY, Column("End", "")),
    "years": (Group.IDENTITY, Column("Years", "{:.2f}")),
    "parameters": (Group.IDENTITY, Column("Parameters", "")),
    "stop_loss_pct": (Group.IDENTITY, Column("Stop loss %", "{:.2f}")),
    "net_pnl": (Group.BOOK, Column("Net P&L", "${:,.0f}")),
    "total_return_pct": (Group.BOOK, Column("Return %", "{:.2f}")),
    "cagr_pct": (Group.BOOK, Column("CAGR %", "{:.2f}")),
    "volatility_pct": (Group.BOOK, Column("Ann. vol %", "{:.2f}")),
    "sharpe": (Group.BOOK, Column("Sharpe", "{:.2f}")),
    "max_drawdown_pct": (Group.BOOK, Column("Max DD %", "{:.2f}")),
    "beta": (Group.BOOK, Column("β QQQ", "{:.2f}")),
    "correlation": (Group.BOOK, Column(f"{RHO} QQQ", "{:.2f}")),
    "trades": (Group.BOOK, Column("Trades", "{:,.0f}")),
    "hold_return_pct": (Group.AGAINST, Column("Hold %", "{:.2f}")),
    "vs_hold_pct": (Group.AGAINST, Column(f"Book {MINUS} Hold", "{:.2f}")),
    "qqq_return_pct": (Group.AGAINST, Column("QQQ %", "{:.2f}")),
    "vs_qqq_pct": (Group.AGAINST, Column(f"Book {MINUS} QQQ", "{:.2f}")),
    "capital": (Group.CAPITAL, Column("Initial capital", "${:,.0f}")),
    "final_equity": (Group.CAPITAL, Column("Final capital", "${:,.0f}")),
    "max_capital_loss_pct": (Group.CAPITAL, Column("Max DD from capital %", "{:.2f}")),
    "avg_capital_used": (Group.CAPITAL, Column("Capital used avg", "${:,.0f}")),
    "max_capital_used": (Group.CAPITAL, Column("Capital used max", "${:,.0f}")),
    "avg_overnight_capital": (Group.CAPITAL, Column("Overnight avg", "${:,.0f}")),
    "fills_on": (Group.EXECUTION, Column("Fills on", "")),
    "intraday": (Group.EXECUTION, Column("Intraday", "")),
    "margin": (Group.EXECUTION, Column("Margin", "")),
    "commission_per_share": (Group.EXECUTION, Column("Commission / share", "{:.4f}")),
    "slippage_bps": (Group.EXECUTION, Column("Slippage bps", "{:.1f}")),
    "sweep_short": (Group.SOURCE, Column("Simulation", "")),
    "engine": (Group.SOURCE, Column("Engine", "{:.0f}")),
    "benchmark_ends": (Group.SOURCE, Column("QQQ data ends", "")),
}
PARAMETER_PATTERN: Final = ""


@dataclass(frozen=True, slots=True)
class Criteria:
    markets: Collection[str] = ()
    strategies: Collection[str] = ()
    timeframes: Collection[str] = ()
    stops: Collection[str] = ()
    fills: Collection[str] = ()
    margins: Collection[str] = ()
    search: str = ""
    outdated: bool = False
    groups: Collection[Group] = field(default_factory=lambda: DEFAULT_GROUPS)


def prepare(index: pd.DataFrame) -> pd.DataFrame:
    frame = index.copy()
    frame["vs_hold_pct"] = frame["total_return_pct"] - frame["hold_return_pct"]
    frame["vs_qqq_pct"] = frame["total_return_pct"] - frame["qqq_return_pct"]
    frame["sweep_short"] = frame["sweep"].str[:8]
    frame["stop_label"] = frame["stop_loss_pct"].map(stop_label)
    return frame.sort_values("sharpe", ascending=False, na_position="last").reset_index(drop=True)


def empty() -> pd.DataFrame:
    return prepare(SCHEMA.empty_table().to_pandas())


def stop_label(value: object) -> str:
    if not isinstance(value, int | float) or np.isnan(value):
        return "none"
    return f"{float(value):g}%"


def choices(frame: pd.DataFrame, column: str) -> list[str]:
    return sorted({str(v) for v in frame[column].dropna()})


def narrow(frame: pd.DataFrame, c: Criteria) -> pd.DataFrame:
    keep = np.ones(len(frame), dtype=bool)
    pairs = (("market", c.markets), ("strategy_title", c.strategies), ("timeframe", c.timeframes),
             ("stop_label", c.stops), ("fills_on", c.fills), ("margin", c.margins))
    for column, wanted in pairs:
        if wanted:
            keep &= frame[column].astype(str).isin(list(wanted)).to_numpy()
    if not c.outdated:
        keep &= ~frame["outdated"].to_numpy(dtype=bool)
    if c.search.strip():
        keep &= frame["parameters"].str.contains(c.search.strip(), case=False, regex=False).to_numpy()
    return frame[keep].reset_index(drop=True)


def parameter_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame["strategy"].nunique() != 1:
        return pd.DataFrame(index=frame.index)
    values = [json.loads(s) for s in frame["parameters_json"]]
    names = list(dict.fromkeys(k for v in values for k in v))
    return pd.DataFrame({name.replace("_", " ").title(): [v.get(name) for v in values] for name in names},
                        index=frame.index)


type Formats = dict[str, str | Callable[..., Any]]


def shown(frame: pd.DataFrame, groups: Collection[Group]) -> tuple[pd.DataFrame, Formats]:
    picked = [k for k, (g, _) in COLUMNS.items() if g in groups]
    out = frame[picked].rename(columns={k: COLUMNS[k][1].label for k in picked})
    extra = parameter_columns(frame)
    if not extra.empty and len(extra.columns):
        at = list(out.columns).index("Parameters") + 1 if "Parameters" in out.columns else len(out.columns)
        out = pd.concat([out.iloc[:, :at], extra, out.iloc[:, at:]], axis=1)
    out.insert(0, "#", range(1, len(out) + 1))
    specs = [COLUMNS[k][1] for k in picked]
    formats: Formats = {c.label: cell(c.pattern) for c in specs if c.pattern}
    return out, formats


def open_link(row: pd.Series) -> str:
    params: dict[str, object] = json.loads(str(row["parameters_json"]))
    query = {k: str(v) for k, v in params.items()}
    return link("simulation", sweep=str(row["sweep"]), **query)


SUMMARY: Final[tuple[tuple[str, Callable[[pd.DataFrame], float], str], ...]] = (
    ("Books", lambda f: float(len(f)), "{:,.0f}"),
    ("Beat Hold", lambda f: 100 * float((f["vs_hold_pct"] > 0).mean()), "{:.0f}%"),
    ("Beat QQQ", lambda f: 100 * float((f["vs_qqq_pct"] > 0).mean()), "{:.0f}%"),
    ("Median return", lambda f: float(f["total_return_pct"].median()), "{:.2f}%"),
    ("Best Sharpe", lambda f: float(f["sharpe"].max()), "{:.2f}"),
    ("Median Sharpe", lambda f: float(f["sharpe"].median()), "{:.2f}"),
    ("Worst DD", lambda f: float(f["max_drawdown_pct"].min()), "{:.2f}%"),
)


def summary(frame: pd.DataFrame) -> str:
    head = "".join(f"<th>{h}</th>" for h, _, _ in SUMMARY)
    if frame.empty:
        return table(head, "".join("<td>&ndash;</td>" for _ in SUMMARY))
    return table(head, "".join(f"<td>{fmt(fn(frame), pattern)}</td>" for _, fn, pattern in SUMMARY))
