from __future__ import annotations

from pathlib import Path
from typing import Final

import anywidget
import traitlets

HERE: Final = Path(__file__).parent


class Ledger(anywidget.AnyWidget):
    _esm = HERE / "ledger.js"
    _css = HERE / "widgets.css"

    api = traitlets.Unicode("").tag(sync=True)
    key = traitlets.Unicode("").tag(sync=True)
