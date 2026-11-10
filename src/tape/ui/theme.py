from __future__ import annotations

from typing import Final

FONT_FAMILY: Final = "JetBrains Mono"
FONT_URL: Final = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap"

SURFACE: Final = "#ffffff"
PANEL: Final = "#f6f6f4"
INK: Final = "#1a1a1a"
MUTED: Final = "#6b6b66"
HAIRLINE: Final = "#dcdcd7"
GRID: Final = "#ecece8"
SIGNAL: Final = "#c60018"
SIGNAL_TINT: Final = "#f9dde1"

SERIES: Final = ("#2a78d6", "#eb6834")
INDICATORS: Final = ("#1baf7a", "#eda100", "#6250d6", "#e87ba4")
BUY: Final = "#1baf7a"
SELL: Final = "#c60018"

STREAMLIT: Final[dict[str, str]] = {
    "theme.base": "light",
    "theme.primaryColor": SIGNAL,
    "theme.backgroundColor": SURFACE,
    "theme.secondaryBackgroundColor": PANEL,
    "theme.textColor": INK,
    "theme.borderColor": HAIRLINE,
    "theme.dataframeBorderColor": HAIRLINE,
    "theme.dataframeHeaderBackgroundColor": PANEL,
    "theme.linkColor": SIGNAL,
    "theme.font": f"{FONT_FAMILY}:{FONT_URL}, monospace",
    "theme.headingFont": f"{FONT_FAMILY}:{FONT_URL}, monospace",
    "theme.codeFont": f"{FONT_FAMILY}:{FONT_URL}, monospace",
    "theme.baseFontSize": "14",
    "theme.baseRadius": "2px",
    "theme.buttonRadius": "2px",
    "theme.showWidgetBorder": "true",
    "theme.showSidebarBorder": "true",
    "client.toolbarMode": "viewer",
}

CSS: Final = f"""
<style>
html, body, [class*="st-"] {{ font-variant-numeric: tabular-nums; }}
::selection {{ background: {SIGNAL_TINT}; color: {INK}; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
@media (min-width: 1100px) {{
  section[data-testid="stSidebar"][data-collapsed="false"] {{ min-width: 350px; }}
}}
section[data-testid="stSidebar"] h2 {{ font-size: 1.05rem; font-weight: 700; margin: 0 0 .25rem; }}
section[data-testid="stSidebar"] h3 {{ font-size: .9rem; font-weight: 600; margin: 1rem 0 .25rem; }}
.tape-line {{ display: inline-block; min-width: 1.6em; padding: 0 .45em; color: #fff;
  font-weight: 700; text-align: center; border-radius: 2px; line-height: 1.55; }}
.tape-board {{ display: flex; flex-wrap: wrap; gap: .4rem 1.6rem; align-items: baseline;
  border-bottom: 2px solid {INK}; padding: 0 0 .5rem; margin: 0 0 .9rem; }}
.tape-board .ticker {{ font-size: 1.45rem; font-weight: 700; color: {INK}; }}
.tape-board .span {{ color: {INK}; font-weight: 500; }}
.tape-board .meta {{ color: {MUTED}; }}
.tape-board .leg {{ display: inline-flex; gap: .45rem; align-items: baseline; color: {INK}; }}
table.tape {{ border-collapse: collapse; width: 100%; font-size: .95rem; }}
table.tape th, table.tape td {{ padding: .3rem .6rem; border-bottom: 1px solid {HAIRLINE};
  white-space: nowrap; }}
table.tape thead th {{ border-bottom: 2px solid {INK}; text-align: right; font-weight: 600;
  vertical-align: bottom; }}
table.tape thead th:first-child, table.tape td:first-child {{ text-align: left; color: {MUTED};
  font-weight: 400; }}
table.tape td {{ text-align: right; color: {INK}; }}
table.tape td.diff {{ color: {MUTED}; }}
table.tape tr.group td {{ border-bottom: 1px solid {INK}; }}
</style>
"""
