from __future__ import annotations

import marimo as mo


def split(left: object, right: object) -> mo.Html:
    return mo.Html(f'<div class="t-split"><div>{mo.as_html(left)}</div><div>{mo.as_html(right)}</div></div>')
