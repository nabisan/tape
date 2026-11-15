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
SPACE: Final = (4, 8, 12, 16, 24, 32)
TEXT: Final = (12, 13, 20, 28)
HAIR: Final = 1

SPRING: Final = (
    "linear(0, 0.024 2.5%, 0.084 5%, 0.167 7.5%, 0.559 17.5%, 0.645 20%, 0.722 22.5%, 0.789 25%,"
    " 0.846 27.5%, 0.893 30%, 0.931 32.5%, 0.984 37.5%, 1.013 42.5%, 1.027 55%, 1)"
)
DURATION: Final = 400

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
    "hair": f"{HAIR}px",
    **{f"space-{step}": f"{value}px" for step, value in enumerate(SPACE, 1)},
    **{f"text-{step}": f"{value}px" for step, value in enumerate(TEXT, 1)},
    "ease": SPRING,
    "dur": f"{DURATION}ms",
}
