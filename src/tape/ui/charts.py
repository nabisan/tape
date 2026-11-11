from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tape.core.types import DATE, STAMP
from tape.ui.theme import (
    DOWN,
    DOWN_FILL,
    FAINT,
    GRID,
    HAIRLINE,
    INDICATORS,
    INK,
    MONO,
    MUTED,
    SECONDARY,
    SERIES,
    UP,
    WHITE,
)

if TYPE_CHECKING:
    import pandas as pd


CONFIG: Final = {"displayModeBar": False, "scrollZoom": False}
EQUITY_HEIGHT: Final = 380
PRICE_HEIGHT: Final = 440
HEATMAP_HEIGHT: Final = 340
FONT: Final = f"{MONO}, ui-monospace, monospace"
TICK: Final = {"color": MUTED, "size": 11, "family": FONT}


def _style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        font={"color": INK, "size": 12, "family": FONT},
        margin={"t": 4, "b": 0, "l": 0, "r": 0},
        showlegend=False,
        hovermode="x unified",
        hoverlabel={
            "font": {"family": FONT, "size": 12, "color": INK},
            "bgcolor": WHITE,
            "bordercolor": HAIRLINE,
        },
    )
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linecolor=HAIRLINE,
        zeroline=False,
        ticks="",
        tickfont=TICK,
        automargin=True,
        showspikes=True,
        spikecolor=MUTED,
        spikethickness=1,
        spikedash="dot",
        spikemode="across",
    )
    fig.update_yaxes(
        gridcolor=GRID,
        showline=False,
        zeroline=False,
        ticks="",
        tickfont=TICK,
        automargin=True,
        side="right",
        nticks=6,
    )
    return fig


def equity(curves: dict[str, pd.DataFrame]) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    for i, ((label, eq), (color, dash)) in enumerate(zip(curves.items(), SERIES, strict=False)):
        fig.add_trace(
            go.Scattergl(
                x=eq["date"], y=eq["equity"], name=label, line={"color": color, "width": 1.5, "dash": dash}
            ),
            1,
            1,
        )
        fig.add_trace(
            go.Scattergl(
                x=eq["date"],
                y=eq["drawdown_pct"],
                name=f"{label} drawdown",
                line={"color": DOWN if i == 0 else color, "width": 1, "dash": dash},
                fill="tozeroy" if i == 0 else None,
                fillcolor=DOWN_FILL,
            ),
            2,
            1,
        )
    fig.update_xaxes(hoverformat=DATE)
    fig.update_yaxes(tickprefix="$", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(ticksuffix="%", tickformat=".0f", nticks=3, row=2, col=1)
    return _style(fig, EQUITY_HEIGHT)


def price(bars: pd.DataFrame, fills: pd.DataFrame, indicators: list[str], intraday: bool) -> go.Figure:
    n = len(bars)
    x = np.arange(n)
    stamps = bars["time"].dt.strftime(STAMP if intraday else DATE).to_numpy()
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=x,
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            text=stamps,
            hoverinfo="text+y",
            name="Price",
            increasing={"line": {"color": UP, "width": 1}, "fillcolor": UP},
            decreasing={"line": {"color": DOWN, "width": 1}, "fillcolor": DOWN},
        )
    )
    for name, (color, dash) in zip(indicators, INDICATORS, strict=False):
        fig.add_trace(
            go.Scattergl(
                x=x, y=bars[name], name=name, mode="lines", line={"color": color, "width": 1.2, "dash": dash}
            )
        )
    times = bars["time"].to_numpy()
    for side, symbol in (("buy", "triangle-up"), ("sell", "triangle-down")):
        f = fills[fills["side"] == side]
        at = np.searchsorted(times, f["time"].to_numpy(), side="right") - 1
        fig.add_trace(
            go.Scattergl(
                x=at,
                y=f["price"],
                mode="markers",
                name=side.capitalize(),
                marker={
                    "symbol": symbol,
                    "size": 10,
                    "color": INK if side == "buy" else WHITE,
                    "line": {"width": 1, "color": INK},
                },
                customdata=np.column_stack([f["quantity"], f["tag"]]) if len(f) else None,
                hovertemplate="%{y:.2f}  qty %{customdata[0]}  %{customdata[1]}<extra></extra>",
            )
        )
    ticks = np.unique(np.linspace(0, max(n - 1, 0), num=min(10, max(n, 1))).astype(int))
    fig.update_xaxes(
        tickmode="array",
        tickvals=ticks,
        ticktext=[stamps[i][: -len(" 00:00:00")] if intraday else stamps[i] for i in ticks],
        rangeslider_visible=False,
        range=[-1, n],
    )
    fig.update_layout(hovermode="closest")
    return _style(fig, PRICE_HEIGHT)


def heatmap(frame: pd.DataFrame, x: str, y: str, value: str) -> go.Figure:
    grid = frame.pivot_table(index=y, columns=x, values=value).sort_index()
    fig = go.Figure(
        go.Heatmap(
            z=grid.to_numpy(),
            x=[str(c) for c in grid.columns],
            y=[str(i) for i in grid.index],
            colorscale=[[0.0, DOWN], [0.5, WHITE], [1.0, UP]],
            zmid=0,
            text=[["" if math.isnan(v) else f"{v:.2f}" for v in row] for row in grid.to_numpy()],
            texttemplate="%{text}",
            hovertemplate=f"{x} %{{x}}<br>{y} %{{y}}<br>{value} %{{z:.2f}}<extra></extra>",
            showscale=False,
        )
    )
    _style(fig, HEATMAP_HEIGHT)
    fig.update_xaxes(title_text=x, type="category", showspikes=False)
    fig.update_yaxes(title_text=y, type="category", side="left")
    fig.update_layout(hovermode="closest")
    return fig


def paths(frame: pd.DataFrame, runs: list[str], best: dict[str, str], reference: str | None) -> go.Figure:
    x = frame["date"].to_numpy()
    fig = go.Figure()
    if runs:
        gap = np.array([np.datetime64("NaT")])
        xs = np.concatenate([np.concatenate([x, gap]) for _ in runs])
        ys = np.concatenate([np.r_[frame[k].to_numpy(dtype=float), np.nan] for k in runs])
        fig.add_trace(
            go.Scattergl(
                x=xs, y=ys, mode="lines", line={"color": FAINT, "width": 0.8}, opacity=0.6, hoverinfo="skip"
            )
        )
    if reference is not None and reference in frame:
        fig.add_trace(
            go.Scattergl(
                x=x,
                y=frame[reference],
                name=reference,
                mode="lines",
                line={"color": MUTED, "width": 1.2, "dash": "dash"},
            )
        )
    for key, label in best.items():
        fig.add_trace(
            go.Scattergl(x=x, y=frame[key], name=label, mode="lines", line={"color": SECONDARY, "width": 1.2})
        )
    if runs:
        fig.add_trace(
            go.Scattergl(
                x=x,
                y=frame[runs].median(axis=1),
                name="Median",
                mode="lines",
                line={"color": INK, "width": 2},
            )
        )
    fig.update_xaxes(hoverformat=DATE)
    fig.update_yaxes(ticksuffix="%", tickformat=",.0f")
    fig.update_layout(hovermode="x unified")
    return _style(fig, EQUITY_HEIGHT)


def distribution(values: pd.Series, unit: str) -> go.Figure:
    v = values.dropna().to_numpy(dtype=float)
    lo, hi = (float(v.min()), float(v.max())) if len(v) else (0.0, 1.0)
    size = (hi - lo) / 30 if hi > lo else 1.0
    bins = {"start": lo, "end": hi + size, "size": size}
    fig = go.Figure()
    for mask, color in ((v <= 0, DOWN), (v > 0, UP)):
        fig.add_trace(
            go.Histogram(
                x=v[mask],
                xbins=bins,
                marker={"color": color, "line": {"color": WHITE, "width": 1}},
                hovertemplate=f"%{{x}}{unit}<br>%{{y}} runs<extra></extra>",
            )
        )
    fig.update_layout(barmode="overlay", bargap=0)
    fig.update_xaxes(ticksuffix=unit, showspikes=False)
    _style(fig, EQUITY_HEIGHT)
    fig.update_layout(hovermode="closest")
    return fig
