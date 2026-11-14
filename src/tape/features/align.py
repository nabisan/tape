from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict

from tape.core.hashing import digest
from tape.core.types import Timeframe
from tape.features.kinds import FeatureId, FeatureSpec

if TYPE_CHECKING:
    from tape.core.types import F64, I64


class Aligned(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feature: FeatureSpec
    timeframe: Timeframe

    @property
    def id(self) -> FeatureId:
        return FeatureId(digest({"aligned": self.model_dump(mode="json"), "version": 1}))

    @property
    def name(self) -> str:
        return f"{self.feature.name}_{self.timeframe}"


def as_of(values: F64, source_ends: I64, target_ends: I64) -> F64:
    at = np.searchsorted(source_ends, target_ends, side="right") - 1
    out = np.full(len(target_ends), np.nan)
    known = at >= 0
    out[known] = values[at[known]]
    return out
