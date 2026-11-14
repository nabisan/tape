from __future__ import annotations

from typing import TYPE_CHECKING, Final

import marimo as mo

from tape.lab.mount import url

if TYPE_CHECKING:
    from tape.lab.form.model import Form

NAMES: Final = ("run", "simulation")
WIDTH: Final = "22rem"


def link(page: str, **query: str) -> str:
    tail = "&".join(f"{k}={v}" for k, v in query.items())
    return url(page) + (f"?{tail}" if tail else "")


PAGES: Final = {name: link(name) for name in NAMES}


def sidebar(form: Form) -> object:
    return mo.sidebar(form, width=WIDTH)
