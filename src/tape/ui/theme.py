from __future__ import annotations

from pathlib import Path
from typing import Final

SANS: Final = "Inter"
MONO: Final = "JetBrains Mono"
SANS_URL: Final = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
MONO_URL: Final = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap"

PAGE: Final = "#f4f4f5"
SURFACE: Final = "#ffffff"
SUBTLE: Final = "#fafafa"
INK: Final = "#18181b"
SECONDARY: Final = "#52525b"
MUTED: Final = "#71717a"
AXIS: Final = "#a1a1aa"
BORDER: Final = "#e4e4e7"
HAIRLINE: Final = "#f4f4f5"
GRID: Final = "#f4f4f5"
UP: Final = "#047857"
DOWN: Final = "#b91c1c"
UP_TINT: Final = "#ecfdf5"
DOWN_TINT: Final = "#fef2f2"

SERIES: Final = (INK, "#a1a1aa")
INDICATORS: Final = ("#7c3aed", "#0891b2", "#db2777", "#ca8a04")
BUY: Final = "#2563eb"
SELL: Final = "#f59e0b"
DRAWDOWN: Final = "#fb2c36"

STREAMLIT: Final[dict[str, str]] = {
    "theme.base": "light",
    "theme.primaryColor": INK,
    "theme.backgroundColor": PAGE,
    "theme.secondaryBackgroundColor": SUBTLE,
    "theme.textColor": INK,
    "theme.borderColor": BORDER,
    "theme.dataframeBorderColor": BORDER,
    "theme.dataframeHeaderBackgroundColor": SUBTLE,
    "theme.linkColor": INK,
    "theme.greenColor": UP,
    "theme.redColor": DOWN,
    "theme.font": f"{SANS}:{SANS_URL}, sans-serif",
    "theme.headingFont": f"{SANS}:{SANS_URL}, sans-serif",
    "theme.codeFont": f"{MONO}:{MONO_URL}, monospace",
    "theme.baseFontSize": "14",
    "theme.baseRadius": "10px",
    "theme.buttonRadius": "full",
    "theme.showWidgetBorder": "true",
    "theme.showSidebarBorder": "true",
    "theme.sidebar.backgroundColor": SURFACE,
    "theme.sidebar.secondaryBackgroundColor": SUBTLE,
    "client.toolbarMode": "viewer",
}

VARIABLES: Final = {
    "sans": f"'{SANS}', sans-serif",
    "mono": f"'{MONO}', monospace",
    "ink": INK,
    "secondary": SECONDARY,
    "muted": MUTED,
    "border": BORDER,
    "hairline": HAIRLINE,
    "surface": SURFACE,
    "subtle": SUBTLE,
    "up": UP,
    "down": DOWN,
    "up-tint": UP_TINT,
    "down-tint": DOWN_TINT,
}

_ROOT: Final = ":root { " + " ".join(f"--{k}: {v};" for k, v in VARIABLES.items()) + " }"
CSS: Final = f"<style>{_ROOT}\n{Path(__file__).with_name('tape.css').read_text()}</style>"
