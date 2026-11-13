from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Collection


class Cost(StrEnum):
    COMMISSION = "commission"


REQUIRES: Final[dict[str, Cost]] = {"commission": Cost.COMMISSION, "total_commission": Cost.COMMISSION}


def charged(column: str, costs: Collection[Cost]) -> bool:
    need = REQUIRES.get(column)
    return need is None or need in costs
