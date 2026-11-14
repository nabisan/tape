from __future__ import annotations

import html
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tape.core.types import DATE
from tape.lab.schema import label
from tape.ui.theme import MONO_URL, VARIABLES

if TYPE_CHECKING:
    from datetime import date

MINUS: Final = "\u2212"
TIMES: Final = "\u00d7"
EN_DASH: Final = "\u2013"
STROKES: Final = {"solid": "solid", "dash": "dashed", "dot": "dotted"}
DESIGN: Final = Path(__file__).with_name("design.css")
ROOT: Final = ":root { " + " ".join(f"--{k}: {v}; --t-{k}: {v};" for k, v in VARIABLES.items()) + " }"
STYLE: Final = f"<style>@import url('{MONO_URL}');\n{ROOT}\n{DESIGN.read_text()}</style>"


def fmt(value: float, pattern: str, signed: bool = False) -> str:
    if math.isnan(value):
        return "&ndash;"
    text = pattern.format(value)
    return f"+{text}" if signed and value > 0 else text.replace("-", "&minus;")


def plain(value: float, pattern: str) -> str:
    return EN_DASH if math.isnan(value) else pattern.format(value).replace("-", MINUS)


def value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, int | float):
        return f"{value:,g}"
    return label(value).lower()


def day(day: date) -> str:
    return day.strftime(DATE)


def mark(label: str, compare: bool) -> str:
    return f'<span class="mark">{label}</span>' if compare else ""


def title(name: str, caption: str = "", keys: str = "") -> str:
    cap = f'<span class="cap">{caption}</span>' if caption else ""
    legend = f'<span class="keys">{keys}</span>' if keys else ""
    return f'<div class="t-title"><span class="name">{html.escape(name)}</span>{cap}{legend}</div>'


def key_line(name: str, color: str, dash: str = "solid") -> str:
    style = f"border-top: 2px {STROKES[dash]} {color}"
    return f'<span class="t-key"><i style="{style}"></i>{html.escape(name)}</span>'


def key_marker(name: str, glyph: str) -> str:
    return f'<span class="t-key"><b>{glyph}</b>{html.escape(name)}</span>'


def notice(message: str) -> str:
    return f'<div class="t-notice">{html.escape(message)}</div>'
