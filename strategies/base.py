"""Strategy contract.

A strategy declares its tunable parameters in `params` (the UI builds inputs
from it) and implements `generate()`, which returns:

  signal      : +1 = go long, -1 = go short, 0 = no new instruction, on the bar
                where the decision is made (at that bar's close). The engine
                fills on the NEXT bar's open, so there is no look-ahead.
  indicators  : dict of name -> Series to draw on the price chart.

To add a strategy: create a file in this folder with a Strategy subclass and
add it to REGISTRY in strategies/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Param:
    label: str
    default: float
    min: float
    max: float
    step: float = 1
    kind: str = "int"  # "int" or "float"


ALL_DIRECTIONS = ["Long & Short", "Long only", "Short only"]


class Strategy:
    name: str = "Base"
    params: dict[str, Param] = {}
    # Trade directions this strategy supports; the UI offers only these.
    # e.g. a long-only strategy sets  directions = ["Long only"]
    directions: list[str] = ALL_DIRECTIONS
    # Whether the strategy can be run in intraday (square-off daily) mode.
    supports_intraday: bool = True

    def validate(self, p: dict) -> str | None:
        """Return an error message if the parameter combination is invalid."""
        return None

    def generate(self, df: pd.DataFrame, **p) -> tuple[pd.Series, dict[str, pd.Series]]:
        raise NotImplementedError
