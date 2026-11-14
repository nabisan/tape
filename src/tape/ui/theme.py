from __future__ import annotations

from typing import Final

MONO: Final = "JetBrains Mono"
MONO_URL: Final = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap"

GRID_STAMP: Final = "DD MMM YYYY HH:mm:ss"
SLIDER_DATE: Final = "DD MMM YYYY"

SIZE: Final = 14
LINE: Final = 20
ROW: Final = 24
CELL: Final = 28
GAP: Final = 24
MARGIN: Final = 24
SIDEBAR: Final = 352

WHITE: Final = "#ffffff"
INK: Final = "#111111"
SECONDARY: Final = "#555555"
MUTED: Final = "#8c8c8c"
FAINT: Final = "#bdbdbd"
HAIRLINE: Final = "#e3e3e3"
GRID: Final = "#f0f0f0"
FIELD: Final = "#f5f5f5"

UP: Final = "#8cc5a0"
DOWN: Final = "#e7a3a3"
DOWN_FILL: Final = "rgba(231,163,163,0.35)"
HEAT_UP: Final = (205, 239, 215)
HEAT_DOWN: Final = (248, 200, 208)

SERIES: Final = ((INK, "solid"), (MUTED, "dash"))
INDICATORS: Final = ((INK, "solid"), (MUTED, "solid"), (INK, "dot"), (MUTED, "dot"))


VARIABLES: Final = {
    "mono": f"'{MONO}', ui-monospace, monospace",
    "size": f"{SIZE}px",
    "line": f"{LINE}px",
    "row": f"{ROW}px",
    "gap": f"{GAP}px",
    "margin": f"{MARGIN}px",
    "white": WHITE,
    "ink": INK,
    "secondary": SECONDARY,
    "muted": MUTED,
    "faint": FAINT,
    "hairline": HAIRLINE,
    "field": FIELD,
}
