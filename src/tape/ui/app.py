from __future__ import annotations

import html
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import TYPE_CHECKING, Any, Final

import pandas as pd
import streamlit as st

from tape.service.results import Table
from tape.ui import charts, forms
from tape.ui.client import ApiError, Client
from tape.ui.theme import CSS, SERIES

if TYPE_CHECKING:
    from tape.engine.reports import Metrics

API_URL: Final = os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000")
LABELS: Final = ("A", "B")
GROUPS: Final[list[list[tuple[str, str, str]]]] = [
    [
        ("net_pnl", "Net P&L ($)", "{:,.0f}"),
        ("total_return_pct", "Total return (%)", "{:.2f}"),
        ("buy_hold_return_pct", "Buy and hold (%)", "{:.2f}"),
        ("cagr_pct", "CAGR (%)", "{:.2f}"),
    ],
    [
        ("volatility_pct", "Volatility, ann. (%)", "{:.2f}"),
        ("sharpe", "Sharpe", "{:.2f}"),
        ("sortino", "Sortino", "{:.2f}"),
        ("max_drawdown_pct", "Max drawdown (%)", "{:.2f}"),
        ("max_drawdown_sessions", "Longest drawdown (sessions)", "{:,.0f}"),
        ("calmar", "Calmar", "{:.2f}"),
        ("exposure_pct", "Exposure (%)", "{:.1f}"),
    ],
    [
        ("trades", "Trades", "{:,.0f}"),
        ("win_rate_pct", "Win rate (%)", "{:.1f}"),
        ("profit_factor", "Profit factor", "{:.2f}"),
        ("payoff_ratio", "Payoff ratio", "{:.2f}"),
        ("expectancy", "Expectancy per trade ($)", "{:,.2f}"),
        ("avg_win", "Average win ($)", "{:,.2f}"),
        ("avg_loss", "Average loss ($)", "{:,.2f}"),
        ("avg_duration_hours", "Average holding (h)", "{:,.1f}"),
    ],
    [
        ("total_commission", "Commission ($)", "{:,.0f}"),
        ("final_equity", "Final equity ($)", "{:,.0f}"),
    ],
]


def badge(label: str) -> str:
    color = SERIES[LABELS.index(label)]
    return f'<span class="tape-line" style="background:{color}">{label}</span>'


@st.cache_resource
def client() -> Client:
    return Client(API_URL)


@st.cache_data(ttl=300)
def catalog() -> dict[str, Any]:
    return client().catalog()


@st.cache_data(ttl=300)
def strategies() -> dict[str, Any]:
    return client().strategies()


@st.cache_data(max_entries=64)
def table(key: str, name: Table) -> pd.DataFrame:
    return client().table(key, name)


def _fmt(value: float, pattern: str, signed: bool = False) -> str:
    if math.isnan(value):
        return "&ndash;"
    text = pattern.format(value)
    return f"+{text}" if signed and value > 0 else text.replace("-", "&minus;")


def comparison(results: dict[str, tuple[str, Metrics]], names: dict[str, str]) -> str:
    labels = list(results)
    head = "".join(f"<th>{badge(label)}<br>{html.escape(names[label])}</th>" for label in labels)
    head += "<th>B &minus; A</th>" if len(labels) == 2 else ""
    body = []
    for group in GROUPS:
        for i, (field, title, fmt) in enumerate(group):
            values = [float(getattr(results[label][1], field)) for label in labels]
            cells = "".join(f"<td>{_fmt(v, fmt)}</td>" for v in values)
            if len(values) == 2:
                cells += f'<td class="diff">{_fmt(values[1] - values[0], fmt, signed=True)}</td>'
            rule = ' class="group"' if i == len(group) - 1 else ""
            body.append(f"<tr{rule}><td>{html.escape(title)}</td>{cells}</tr>")
    return (
        f'<table class="tape"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
    )


def board(
    spec: dict[str, Any], results: dict[str, tuple[str, Metrics]], names: dict[str, str], version: str
) -> str:
    start = date.fromisoformat(spec["start"]).strftime("%d.%m.%Y")
    end = date.fromisoformat(spec["end"]).strftime("%d.%m.%Y")
    legs = "".join(
        f'<span class="leg">{badge(label)} {html.escape(names[label])} '
        f'<span class="meta">{key[:10]}</span></span>'
        for label, (key, _) in results.items()
    )
    return (
        f'<div class="tape-board"><span class="ticker">{html.escape(spec["ticker"])}</span>'
        f'<span class="span">{start} &ndash; {end}</span>{legs}'
        f'<span class="meta">data {version[:10]}</span></div>'
    )


def monthly(eq: pd.DataFrame) -> pd.DataFrame:
    s = eq.set_index("date")["equity"]
    m = s.resample("ME").last().pct_change().fillna(s.resample("ME").last().iloc[0] / s.iloc[0] - 1) * 100
    months = pd.DatetimeIndex(m.index)
    grid = pd.DataFrame({"year": months.year, "month": months.strftime("%b"), "ret": m.to_numpy()})
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    pivot = grid.pivot_table(index="year", columns="month", values="ret").reindex(columns=order)
    pivot["Year"] = (
        s.resample("YE").last().pct_change().fillna(s.resample("YE").last().iloc[0] / s.iloc[0] - 1) * 100
    ).to_numpy()
    return pivot.round(2)


def sidebar(cat: dict[str, Any], strats: dict[str, Any]) -> dict[str, Any] | None:
    with st.sidebar:
        st.header("tape")
        st.caption("Bar-level backtests, regular hours, XNYS sessions")
        tickers = {t["ticker"]: t for t in cat["tickers"]}
        ticker = st.selectbox(
            "Ticker", list(tickers), index=list(tickers).index("AAPL") if "AAPL" in tickers else 0
        )
        first = date.fromisoformat(tickers[ticker]["first_session"])
        last = date.fromisoformat(tickers[ticker]["last_session"])
        span = st.date_input("Dates", (first, last), min_value=first, max_value=last, format="DD/MM/YYYY")
        with st.expander("Execution", expanded=False):
            cash = st.number_input("Initial cash ($)", min_value=1_000.0, value=100_000.0, step=10_000.0)
            commission = st.number_input(
                "Commission ($/share)", min_value=0.0, value=0.005, step=0.001, format="%.4f"
            )
            slippage = st.number_input("Slippage (bps)", min_value=0.0, value=1.0, step=0.5)
            prob_limit = st.slider("Limit fill probability on touch", 0.0, 1.0, 1.0, 0.05)
            intraday = st.toggle("Intraday (flat at every close)")
            delay = st.number_input(
                "Entry delay after open (min)", min_value=0, value=0, step=5, disabled=not intraday
            )
            squareoff = st.number_input(
                "Square-off before close (min)", min_value=1, value=5, step=1, disabled=not intraday
            )
        compare = st.toggle("Compare two strategies", value=False)
        runs = {}
        for label in LABELS if compare else LABELS[:1]:
            st.subheader(f"Strategy {label}" if compare else "Strategy")
            names = list(strats)
            name = st.selectbox(
                "Strategy", names, index=LABELS.index(label) % len(names), key=f"strategy:{label}"
            )
            runs[label] = (name, forms.params(strats[name]["config_schema"], key=f"{label}:{name}"))
        submit = st.button("Run comparison" if compare else "Run backtest", type="primary", width="stretch")
    if not submit or not isinstance(span, tuple) or len(span) != 2:
        return None
    execution = {
        "initial_cash": cash,
        "fill": {
            "commission_per_share": commission,
            "slippage_bps": slippage,
            "prob_fill_on_limit": prob_limit,
        },
        "intraday": intraday,
        "entry_delay_minutes": int(delay),
        "squareoff_minutes_before_close": int(squareoff),
    }
    return {
        label: {
            "ticker": ticker,
            "start": span[0].isoformat(),
            "end": span[1].isoformat(),
            "strategy": name,
            "params": params,
            "execution": execution,
        }
        for label, (name, params) in runs.items()
    }


def strategy_tab(label: str, key: str, spec: dict[str, Any]) -> None:
    bars = table(key, Table.BARS)
    fills = table(key, Table.FILLS)
    trades = table(key, Table.TRADES)
    orders = table(key, Table.ORDERS)
    eq = table(key, Table.EQUITY)
    intraday = spec["params"].get("timeframe", "1d") != "1d"
    days = pd.Index(bars["time"].dt.normalize().unique())
    lo = days[max(0, len(days) - (90 if intraday else len(days)))]
    view = st.slider(
        "Chart window",
        min_value=days[0].date(),
        max_value=days[-1].date(),
        value=(lo.date(), days[-1].date()),
        format="DD/MM/YYYY",
        key=f"window:{label}",
    )
    a, b = pd.Timestamp(view[0]), pd.Timestamp(view[1]) + pd.Timedelta(days=1)
    shown = bars[(bars["time"] >= a) & (bars["time"] < b)]
    shown_fills = fills[(fills["time"] >= a) & (fills["time"] < b)] if len(fills) else fills
    indicators = [
        c for c in bars.columns if c not in {"time", "open", "high", "low", "close", "volume", "equity"}
    ]
    st.plotly_chart(
        charts.price(shown, shown_fills, indicators, intraday), width="stretch", key=f"price:{label}"
    )
    blotter, order_tab, month_tab = st.tabs(["Trades", "Orders", "Monthly returns"])
    with blotter:
        st.dataframe(trades, width="stretch", height=420, hide_index=True)
        st.download_button(
            "Trades CSV", trades.to_csv(index=False), f"trades_{label}_{key[:8]}.csv", key=f"dl:{label}"
        )
    with order_tab:
        st.dataframe(orders, width="stretch", height=420, hide_index=True)
    with month_tab:
        st.dataframe(monthly(eq), width="stretch")


def main() -> None:
    st.set_page_config(page_title="tape", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        cat, strats = catalog(), strategies()
    except (ApiError, OSError) as e:
        st.error(f"API unavailable at {API_URL}: {e}")
        return
    specs = sidebar(cat, strats)
    if specs is not None:
        with st.spinner("Running"), ThreadPoolExecutor(len(specs)) as pool:
            futures = {label: pool.submit(client().run, s) for label, s in specs.items()}
            try:
                st.session_state["results"] = {label: f.result() for label, f in futures.items()}
                st.session_state["specs"] = specs
            except ApiError as e:
                st.error(str(e))
                return
    results: dict[str, tuple[str, Metrics]] | None = st.session_state.get("results")
    if not results:
        st.info("Choose a ticker, a date range and a strategy in the sidebar, then run the backtest.")
        return
    specs = st.session_state["specs"]
    names = {label: f"{s['strategy']} · {s['params'].get('timeframe', '1d')}" for label, s in specs.items()}
    first = next(iter(specs.values()))
    st.html(board(first, results, names, cat["data_version"]))
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.html(comparison(results, names))
    with right:
        curves = {f"{label} {names[label]}": table(key, Table.EQUITY) for label, (key, _) in results.items()}
        st.plotly_chart(charts.equity(curves), width="stretch", key="equity")
    if len(results) == 1:
        label, (key, _) = next(iter(results.items()))
        strategy_tab(label, key, specs[label])
        return
    tabs = st.tabs([f"{label} · {names[label]}" for label in results])
    for tab, (label, (key, _)) in zip(tabs, results.items(), strict=True):
        with tab:
            strategy_tab(label, key, specs[label])


main()
