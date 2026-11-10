from __future__ import annotations

from typing import TYPE_CHECKING, Final

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tape.ui.theme import BUY, FONT_FAMILY, GRID, HAIRLINE, INDICATORS, INK, MUTED, SELL, SERIES, SURFACE

if TYPE_CHECKING:
    import pandas as pd


CONFIG: Final = {"displayModeBar": False, "scrollZoom": False}


def _style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font={"color": INK, "size": 12, "family": f"{FONT_FAMILY}, monospace"},
        margin={"t": 24, "b": 8, "l": 8, "r": 8},
        hovermode="x unified",
        hoverlabel={"font": {"family": f"{FONT_FAMILY}, monospace", "size": 12}, "bgcolor": SURFACE},
        legend={"orientation": "h", "y": 1.02, "x": 0, "yanchor": "bottom", "font": {"size": 12}},
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=HAIRLINE, zeroline=False, tickfont={"color": MUTED})
    fig.update_yaxes(gridcolor=GRID, linecolor=HAIRLINE, zeroline=False, tickfont={"color": MUTED})
    return fig


def equity(curves: dict[str, pd.DataFrame]) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04)
    for (label, eq), color in zip(curves.items(), SERIES, strict=False):
        fig.add_trace(
            go.Scatter(x=eq["date"], y=eq["equity"], name=label, line={"color": color, "width": 2}), 1, 1
        )
        fig.add_trace(
            go.Scatter(
                x=eq["date"],
                y=eq["drawdown_pct"],
                name=f"{label} drawdown",
                line={"color": color, "width": 1.5},
                showlegend=False,
            ),
            2,
            1,
        )
    fig.update_yaxes(title_text="Equity", tickprefix="$", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown", ticksuffix="%", tickformat=".0f", row=2, col=1)
    return _style(fig, 520)


def price(bars: pd.DataFrame, fills: pd.DataFrame, indicators: list[str], intraday: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=bars["time"],
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            name="Price",
            increasing={"line": {"color": MUTED, "width": 1}, "fillcolor": SURFACE},
            decreasing={"line": {"color": INK, "width": 1}, "fillcolor": INK},
        )
    )
    for name, color in zip(indicators, INDICATORS, strict=False):
        fig.add_trace(go.Scatter(x=bars["time"], y=bars[name], name=name, line={"color": color, "width": 2}))
    for side, symbol, color in (("buy", "triangle-up", BUY), ("sell", "triangle-down", SELL)):
        f = fills[fills["side"] == side]
        fig.add_trace(
            go.Scatter(
                x=f["time"],
                y=f["price"],
                mode="markers",
                name=side.capitalize(),
                marker={"symbol": symbol, "size": 11, "color": color, "line": {"width": 1, "color": SURFACE}},
                customdata=f[["quantity", "tag"]],
                hovertemplate="%{y:.2f}  qty %{customdata[0]}  %{customdata[1]}<extra></extra>",
            )
        )
    breaks: list[dict[str, object]] = [{"bounds": ["sat", "mon"]}]
    if intraday:
        breaks.append({"bounds": [16, 9.5], "pattern": "hour"})
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False)
    return _style(fig, 560)
