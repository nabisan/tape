from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import anywidget
import pandas as pd
import plotly.graph_objects as go
import traitlets

from tape.lab.mount import PLOTLY
from tape.ui import charts
from tape.ui.theme import INDICATORS

if TYPE_CHECKING:
    from collections.abc import Mapping

HERE: Final = Path(__file__).parent
STUB: Final = pd.DataFrame(
    {
        "time": pd.to_datetime(["2000-01-03", "2000-01-04"]),
        "open": [1.0, 1.0],
        "high": [1.0, 1.0],
        "low": [1.0, 1.0],
        "close": [1.0, 1.0],
    }
)
NO_FILLS: Final = pd.DataFrame(
    {"time": pd.to_datetime([]), "side": [], "price": [], "quantity": [], "tag": []}
)
ROLES: Final = ("candles", "buy", "sell")
DATA: Final = frozenset({"x", "y", "open", "high", "low", "close", "text", "customdata"})


def spec(intraday: bool) -> dict[str, Any]:
    fig = charts.price(STUB, NO_FILLS, [], intraday)
    base = [{k: v for k, v in t.to_plotly_json().items() if k not in DATA} for t in fig.data]
    lines = [
        go.Scattergl(mode="lines", line={"color": color, "width": 1.2, "dash": dash}).to_plotly_json()
        for color, dash in INDICATORS
    ]
    layout = fig.layout.to_plotly_json()
    layout["dragmode"] = "zoom"
    layout["xaxis"] = {**layout.get("xaxis", {}), "tickmode": "array", "fixedrange": False}
    layout["yaxis"] = {**layout.get("yaxis", {}), "fixedrange": False, "autorange": True}
    return {
        "layout": layout,
        "config": {**charts.CONFIG, "doubleClick": "reset"},
        "traces": [
            *({**t, "meta": role} for t, role in zip(base, ROLES, strict=True)),
            *({**t, "meta": "indicator"} for t in lines),
        ],
    }


class PriceChart(anywidget.AnyWidget):
    _esm = HERE / "price.js"
    _css = HERE / "widgets.css"

    api = traitlets.Unicode("").tag(sync=True)
    key = traitlets.Unicode("").tag(sync=True)
    plotly = traitlets.Unicode(PLOTLY).tag(sync=True)
    intraday = traitlets.Bool(True).tag(sync=True)
    labels = traitlets.Dict(value_trait=traitlets.Unicode()).tag(sync=True)
    figure = traitlets.Dict().tag(sync=True)

    @classmethod
    def of(cls, key: str, intraday: bool, labels: Mapping[str, str] | None = None) -> PriceChart:
        return cls(key=key, intraday=intraday, labels=dict(labels or {}), figure=spec(intraday))
