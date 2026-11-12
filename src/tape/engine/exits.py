from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Collection


class Exit(StrEnum):
    SIGNAL = "signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


REQUIRES: Final[dict[str, Exit]] = {"win_rate_pct": Exit.TAKE_PROFIT}


def applies(metric: str, exits: Collection[Exit]) -> bool:
    need = REQUIRES.get(metric)
    return need is None or need in exits
