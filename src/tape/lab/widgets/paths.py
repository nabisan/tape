from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import anywidget
import pandas as pd
import traitlets

from tape.lab.mount import PLOTLY
from tape.ui import charts

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

HERE: Final = Path(__file__).parent
STUB: Final = pd.to_datetime(["2000-01-03", "2000-01-04"])
RUNS: Final = "runs"
MEDIAN: Final = "median"
REFERENCE: Final = "reference"
BEST: Final = "best:"


def roles(runs: Sequence[str], best: Mapping[str, str], reference: str | None) -> list[str]:
    out = [RUNS] if runs else []
    if reference is not None:
        out.append(REFERENCE)
    out += [f"{BEST}{key}" for key in best]
    return [*out, MEDIAN] if runs else out


def spec(runs: Sequence[str], best: Mapping[str, str], reference: str | None) -> dict[str, Any]:
    names = list(dict.fromkeys([*runs, *best, *([reference] if reference else [])]))
    stub = pd.DataFrame({"date": STUB, **{n: [0.0, 0.0] for n in names}})
    fig = charts.paths(stub, list(runs), dict(best), reference)
    traces = [{k: v for k, v in t.to_plotly_json().items() if k not in {"x", "y"}} for t in fig.data]
    return {
        "layout": fig.layout.to_plotly_json(),
        "config": charts.CONFIG,
        "traces": [{**t, "meta": role} for t, role in zip(traces, roles(runs, best, reference), strict=True)],
    }


class Paths(anywidget.AnyWidget):
    _esm = HERE / "paths.js"

    api = traitlets.Unicode("").tag(sync=True)
    key = traitlets.Unicode("").tag(sync=True)
    plotly = traitlets.Unicode(PLOTLY).tag(sync=True)
    runs = traitlets.List(traitlets.Unicode()).tag(sync=True)
    figure = traitlets.Dict().tag(sync=True)

    @classmethod
    def of(cls, key: str, runs: Sequence[str], best: Mapping[str, str], reference: str | None) -> Paths:
        return cls(key=key, runs=list(runs), figure=spec(runs, best, reference))
