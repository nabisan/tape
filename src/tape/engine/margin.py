from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class MarginSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    leverage: Annotated[float, Field(gt=1, title="Leverage")] = 30.0
    maintenance: Annotated[float, Field(gt=0, le=1, title="Maintenance, share of initial")] = 0.5

    def initial(self, notional: float) -> float:
        return abs(notional) / self.leverage

    def maintenance_margin(self, notional: float) -> float:
        return self.initial(notional) * self.maintenance
