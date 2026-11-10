from __future__ import annotations

import html
import math
import os
import time
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


def notice(message: str) -> None:
    st.html(f'<div class="tape-notice">{html.escape(message)}</div>')


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


FIGURES: Final[list[tuple[str, str, str]]] = [
    ("net_pnl", "Net P&L", "${:,.0f}"),
    ("cagr_pct", "CAGR", "{:.2f}%"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("max_drawdown_pct", "Max drawdown", "{:.2f}%"),
    ("trades", "Trades", "{:,.0f}"),
]


def figure(title: str, value: str, muted: bool = False) -> str:
    tone = "v muted" if muted else "v"
    return f'<span class="fig"><span class="k">{title}</span><span class="{tone}">{value}</span></span>'


def board(
    spec: dict[str, Any], results: dict[str, tuple[str, Metrics]], names: dict[str, str], version: str
) -> str:
    start = date.fromisoformat(spec["start"]).strftime("%d.%m.%Y")
    end = date.fromisoformat(spec["end"]).strftime("%d.%m.%Y")
    rows = []
    for label, (key, m) in results.items():
        figures = "".join(figure(title, _fmt(float(getattr(m, f)), fmt)) for f, title, fmt in FIGURES)
        bench = figure("Buy and hold", _fmt(m.buy_hold_return_pct, "{:.1f}%"), muted=True)
        rows.append(
            f'<div class="tape-leg">{badge(label)}<span class="name">{html.escape(names[label])}</span>'
            f'{figures}{bench}<span class="meta">run {key[:10]}</span></div>'
        )
    return (
        f'<div class="tape-board"><div class="line"><span class="ticker">{html.escape(spec["ticker"])}</span>'
        f'<span class="span">{start} &ndash; {end}</span>'
        f'<span class="meta">data {version[:10]}</span></div>{"".join(rows)}</div>'
    )


def monthly(eq: pd.DataFrame) -> str:
    s = eq.set_index("date")["equity"]
    month_end = s.resample("ME").last()
    m = month_end.pct_change().fillna(month_end.iloc[0] / s.iloc[0] - 1) * 100
    year_end = s.resample("YE").last()
    y = year_end.pct_change().fillna(year_end.iloc[0] / s.iloc[0] - 1) * 100
    months = pd.DatetimeIndex(m.index)
    grid = pd.DataFrame({"year": months.year, "month": months.month, "ret": m.to_numpy()})
    pivot = grid.pivot_table(index="year", columns="month", values="ret").reindex(columns=range(1, 13))
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def cell(v: float) -> str:
        if math.isnan(v):
            return "<td></td>"
        tone = "up" if v > 0 else "down" if v < 0 else ""
        return f'<td class="{tone}">{_fmt(v, "{:.1f}", signed=True)}</td>'

    head = "".join(f"<th>{n}</th>" for n in names) + "<th>Year</th>"
    body = "".join(
        f"<tr><td>{year}</td>{''.join(cell(float(v)) for v in row)}{cell(float(y.iloc[i]))}</tr>"
        for i, (year, row) in enumerate(pivot.iterrows())
    )
    return f'<table class="tape months"><thead><tr><th>%</th>{head}</tr></thead><tbody>{body}</tbody></table>'


TRADE_COLUMNS: Final = {
    "trade_id": st.column_config.NumberColumn("#", format="%d", width="small"),
    "side": st.column_config.TextColumn("Side", width="small"),
    "quantity": st.column_config.NumberColumn("Qty", format="%,d"),
    "entry_time": st.column_config.DatetimeColumn("Entry", format="YYYY-MM-DD HH:mm"),
    "entry_price": st.column_config.NumberColumn("Entry px", format="%.2f"),
    "exit_time": st.column_config.DatetimeColumn("Exit", format="YYYY-MM-DD HH:mm"),
    "exit_price": st.column_config.NumberColumn("Exit px", format="%.2f"),
    "exit_tag": st.column_config.TextColumn("Exit on"),
    "duration": st.column_config.TextColumn("Held"),
    "gross_pnl": st.column_config.NumberColumn("Gross $", format="%,.2f"),
    "commission": st.column_config.NumberColumn("Comm $", format="%,.2f"),
    "net_pnl": st.column_config.NumberColumn("Net $", format="%,.2f"),
    "return_pct": st.column_config.NumberColumn("Return %", format="%.2f"),
    "mae_pct": st.column_config.NumberColumn("MAE %", format="%.2f"),
    "mfe_pct": st.column_config.NumberColumn("MFE %", format="%.2f"),
    "open": st.column_config.CheckboxColumn("Open"),
}
ORDER_COLUMNS: Final = {
    "order_id": st.column_config.NumberColumn("#", format="%d", width="small"),
    "submitted": st.column_config.DatetimeColumn("Submitted", format="YYYY-MM-DD HH:mm"),
    "closed": st.column_config.DatetimeColumn("Closed", format="YYYY-MM-DD HH:mm"),
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
def space(strategy: str, ticker: str) -> dict[str, Any]:
    return client().space(strategy, ticker)


def execution_form() -> dict[str, Any]:
    with st.expander("Execution", expanded=False):
        cash = st.number_input(
            "Initial cash ($)", min_value=1_000.0, value=100_000.0, step=10_000.0, format="%.0f"
        )
        commission = st.number_input("Commission ($/share)", 0.0, 1.0, 0.005, 0.001, format="%.4f")
        slippage = st.number_input("Slippage (bps)", 0.0, 1_000.0, 1.0, 0.5)
        prob_limit = st.slider("Limit fill probability on touch", 0.0, 1.0, 1.0, 0.05)
        intraday = st.toggle("Intraday (flat at every close)")
        delay = st.number_input("Entry delay after open (min)", 0, 390, 0, 5, disabled=not intraday)
        squareoff = st.number_input("Square-off before close (min)", 1, 390, 5, 1, disabled=not intraday)
    return {
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


def _span(first: date, last: date, key: str) -> tuple[date, date] | None:
    span = st.date_input(
        "Dates", (first, last), min_value=first, max_value=last, format="DD/MM/YYYY", key=key
    )
    return (span[0], span[1]) if isinstance(span, tuple) and len(span) == 2 else None


def backtest_form(cat: dict[str, Any], strats: dict[str, Any]) -> dict[str, Any] | None:
    tickers = [t["ticker"] for t in cat["tickers"]]
    info = {t["ticker"]: t for t in cat["tickers"]}
    ticker = st.selectbox("Ticker", tickers, index=tickers.index("AAPL") if "AAPL" in tickers else 0)
    span = _span(
        date.fromisoformat(info[ticker]["first_session"]),
        date.fromisoformat(info[ticker]["last_session"]),
        "dates:backtest",
    )
    compare = st.toggle("Compare two strategies", value=False)
    runs: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for label in LABELS if compare else LABELS[:1]:
        st.subheader(f"Strategy {label}" if compare else "Strategy")
        names = list(strats)
        name = st.selectbox(
            "Strategy",
            names,
            index=LABELS.index(label) % len(names),
            format_func=lambda n: strats[n]["title"],
            key=f"strategy:{label}",
        )
        resolved = space(name, ticker)
        runs[label] = (name, forms.strategy(resolved["config_schema"], resolved["space"], f"{label}:{name}"))
    execution = execution_form()
    ready = span is not None and all(params is not None for _, params in runs.values())
    label_text = "Run comparison" if compare else "Run backtest"
    if not st.button(label_text, type="primary", width="stretch", disabled=not ready) or span is None:
        return None
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


def sweep_form(cat: dict[str, Any], strats: dict[str, Any]) -> dict[str, Any] | None:
    tickers = [t["ticker"] for t in cat["tickers"]]
    chosen = st.multiselect("Tickers", tickers, default=["AAPL"] if "AAPL" in tickers else tickers[:1])
    if st.toggle("All tickers", value=False):
        chosen = tickers
    if not chosen:
        notice("Choose at least one ticker.")
        return None
    span = _span(
        date.fromisoformat(cat["first_session"]), date.fromisoformat(cat["last_session"]), "dates:sweep"
    )
    name = st.selectbox(
        "Strategy", list(strats), format_func=lambda n: strats[n]["title"], key="sweep:strategy"
    )
    resolved = space(name, chosen[0])
    timeframes: list[str] = resolved["space"]["timeframes"]
    if not timeframes:
        st.warning(f"No precomputed features cover {strats[name]['title']} for {chosen[0]}.")
        return None
    tf = st.selectbox("Timeframe", timeframes, index=len(timeframes) - 1, key="sweep:timeframe")
    grid = {
        param: st.multiselect(forms.label(param), domain, default=domain, key=f"sweep:{name}:{tf}:{param}")
        for param, domain in resolved["space"]["choices"][tf].items()
    }
    schema = resolved["config_schema"]
    fixed = forms.fields(
        schema["properties"], schema.get("$defs", {}), f"sweep:{name}", skip={"timeframe", *grid}
    )
    execution = execution_form()
    ready = span is not None and all(grid.values())
    if not st.button("Run sweep", type="primary", width="stretch", disabled=not ready) or span is None:
        return None
    return {
        "tickers": chosen,
        "start": span[0].isoformat(),
        "end": span[1].isoformat(),
        "strategy": name,
        "timeframe": tf,
        "grid": grid,
        "params": fixed,
        "execution": execution,
    }


def strategy_tab(label: str, key: str, spec: dict[str, Any]) -> None:
    bars = table(key, Table.BARS)
    fills = table(key, Table.FILLS)
    trades = table(key, Table.TRADES)
    orders = table(key, Table.ORDERS)
    eq = table(key, Table.EQUITY)
    intraday = spec["params"].get("timeframe", "1d") != "1d"
    days = pd.Index(bars["time"].dt.normalize().unique())
    lo = days[max(0, len(days) - (60 if intraday else len(days)))]
    view = st.slider(
        "Chart window",
        min_value=days[0].date(),
        max_value=days[-1].date(),
        value=(lo.date(), days[-1].date()),
        format="DD/MM/YYYY",
        key=f"window:{label}",
        label_visibility="collapsed",
    )
    a, b = pd.Timestamp(view[0]), pd.Timestamp(view[1]) + pd.Timedelta(days=1)
    shown = bars[(bars["time"] >= a) & (bars["time"] < b)]
    shown_fills = fills[(fills["time"] >= a) & (fills["time"] < b)] if len(fills) else fills
    indicators = [
        c for c in bars.columns if c not in {"time", "open", "high", "low", "close", "volume", "equity"}
    ]
    st.plotly_chart(
        charts.price(shown, shown_fills, indicators, intraday),
        width="stretch",
        key=f"price:{label}",
        config=charts.CONFIG,
    )
    blotter, order_tab, month_tab = st.tabs(
        [f"Trades ({len(trades):,})", f"Orders ({len(orders):,})", "Monthly returns"]
    )
    with blotter:
        view_trades = trades.assign(duration=trades["duration"].map(held)) if len(trades) else trades
        st.dataframe(view_trades, width="stretch", height=440, hide_index=True, column_config=TRADE_COLUMNS)
        st.download_button(
            "Download trades (CSV)",
            trades.to_csv(index=False),
            f"trades_{label}_{key[:8]}.csv",
            key=f"dl:{label}",
        )
    with order_tab:
        st.dataframe(orders, width="stretch", height=440, hide_index=True, column_config=ORDER_COLUMNS)
    with month_tab:
        st.html(monthly(eq))


def show_runs(
    results: dict[str, tuple[str, Metrics]],
    specs: dict[str, Any],
    cat: dict[str, Any],
    strats: dict[str, Any],
) -> None:
    names = {
        label: f"{strats[s['strategy']]['title']} · {s['params'].get('timeframe', '1d')}"
        for label, s in specs.items()
    }
    st.html(board(next(iter(specs.values())), results, names, cat["data_version"]))
    left, right = st.columns([3, 2], gap="large")
    with left:
        curves = {f"{label} {names[label]}": table(key, Table.EQUITY) for label, (key, _) in results.items()}
        st.plotly_chart(charts.equity(curves), width="stretch", key="equity", config=charts.CONFIG)
    with right:
        st.html(comparison(results, names))
    if len(results) == 1:
        label, (key, _) = next(iter(results.items()))
        strategy_tab(label, key, specs[label])
        return
    tabs = st.tabs([f"{label} · {names[label]}" for label in results])
    for tab, (label, (key, _)) in zip(tabs, results.items(), strict=True):
        with tab:
            strategy_tab(label, key, specs[label])


SWEEP_COLUMNS: Final = {
    "key": None,
    "net_pnl": st.column_config.NumberColumn("Net P&L $", format="%,.0f"),
    "total_return_pct": st.column_config.NumberColumn("Return %", format="%.2f"),
    "cagr_pct": st.column_config.NumberColumn("CAGR %", format="%.2f"),
    "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
    "sortino": st.column_config.NumberColumn("Sortino", format="%.2f"),
    "max_drawdown_pct": st.column_config.NumberColumn("Max DD %", format="%.2f"),
    "calmar": st.column_config.NumberColumn("Calmar", format="%.2f"),
    "trades": st.column_config.NumberColumn("Trades", format="%,d"),
    "win_rate_pct": st.column_config.NumberColumn("Win %", format="%.1f"),
    "profit_factor": st.column_config.NumberColumn("PF", format="%.2f"),
    "exposure_pct": st.column_config.NumberColumn("Exposure %", format="%.1f"),
    "buy_hold_return_pct": st.column_config.NumberColumn("Buy & hold %", format="%.1f"),
}


def run_sweep(spec: dict[str, Any]) -> str | None:
    try:
        started = client().start_sweep(spec)
    except ApiError as e:
        st.error(str(e))
        return None
    key = str(started["key"])
    bar = st.progress(0.0, "Starting sweep")
    status = started
    while status["state"] == "running":
        total = max(1, int(status["total"]))
        bar.progress(int(status["done"]) / total, f"{status['done']:,} of {total:,} runs")
        time.sleep(1.0)
        status = client().sweep(key)
    bar.empty()
    if status["state"] == "failed":
        st.error(f"Sweep failed: {status['error']}")
        return None
    for s in status["skipped"]:
        st.warning(f"{s['ticker']} skipped: {s['reason']}")
    return key


def show_sweep(key: str, spec: dict[str, Any], cat: dict[str, Any], strats: dict[str, Any]) -> None:
    frame = client().sweep_summary(key)
    if frame.empty:
        notice("The sweep produced no runs.")
        return
    swept = [c for c in frame.columns if c in spec["grid"]]
    measures = [c for c in SWEEP_COLUMNS if c in frame.columns and c != "key"]
    fixed = [
        c for c in frame.columns if c not in {"ticker", "key", *swept, *measures} and frame[c].nunique() == 1
    ]
    shown = [c for c in frame.columns if c not in fixed]
    title = strats[spec["strategy"]]["title"]
    best = frame.sort_values("sharpe", ascending=False, na_position="last").reset_index(drop=True)
    count = len(spec["tickers"])
    held_fixed = " · ".join(f"{forms.label(c)} {forms.label(frame[c].iloc[0])}" for c in fixed)
    st.html(
        f'<div class="tape-board"><div class="line"><span class="ticker">{html.escape(title)} sweep</span>'
        f'<span class="span">{spec["timeframe"]} · {count} ticker{"s" if count != 1 else ""} · '
        f"{len(frame):,} runs</span>"
        f'<span class="meta">sweep {key[:10]}</span></div>'
        f'<div class="tape-leg"><span class="k">Held fixed</span>'
        f"<span>{html.escape(held_fixed)}</span></div></div>"
    )
    if count == 1 and len(swept) == 2:
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.plotly_chart(
                charts.heatmap(frame, swept[0], swept[1], "sharpe"),
                width="stretch",
                key="sweep:heatmap",
                config=charts.CONFIG,
            )
        with right:
            top = [*swept, "sharpe", "cagr_pct", "max_drawdown_pct", "trades"]
            st.dataframe(
                best[top].head(12), width="stretch", height=420, hide_index=True, column_config=SWEEP_COLUMNS
            )
    st.dataframe(best[shown], width="stretch", height=480, hide_index=True, column_config=SWEEP_COLUMNS)
    st.download_button(
        "Download sweep (CSV)", best.drop(columns=["key"]).to_csv(index=False), f"sweep_{key[:8]}.csv"
    )
    labels = [
        f"{r.ticker} · "
        + " ".join(f"{c}={getattr(r, c)}" for c in swept)
        + f" · Sharpe {_fmt(float(str(r.sharpe)), '{:.2f}')}"
        for r in best.itertuples()
    ]
    pick = st.selectbox(
        "Open a run",
        range(len(best)),
        format_func=lambda i: labels[i],
        index=None,
        placeholder="Choose a run to open",
    )
    if pick is not None:
        run_key = str(best.loc[pick, "key"])
        run_spec = client().spec(run_key)
        show_runs({"A": (run_key, client().metrics(run_key))}, {"A": run_spec}, cat, strats)


def main() -> None:
    st.set_page_config(page_title="tape", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        cat, strats = catalog(), strategies()
    except (ApiError, OSError) as e:
        st.error(f"API unavailable at {API_URL}: {e}")
        return
    with st.sidebar:
        st.header("tape")
        st.caption("Bar-level backtests · XNYS regular hours")
        mode = st.segmented_control(
            "Mode", ["Backtest", "Sweep"], default="Backtest", label_visibility="collapsed"
        )
        if mode == "Sweep":
            sweep_spec = sweep_form(cat, strats)
            specs = None
        else:
            specs = backtest_form(cat, strats)
            sweep_spec = None
    if mode == "Sweep":
        if sweep_spec is not None and (key := run_sweep(sweep_spec)) is not None:
            st.session_state["sweep"] = (key, sweep_spec)
        current = st.session_state.get("sweep")
        if current is None:
            notice("Choose tickers, a strategy and the parameter values to sweep, then run the sweep.")
            return
        show_sweep(current[0], current[1], cat, strats)
        return
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
        notice("Choose a ticker, a date range and a strategy in the sidebar, then run the backtest.")
        return
    show_runs(results, st.session_state["specs"], cat, strats)


main()
