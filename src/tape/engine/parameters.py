from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, model_validator

from tape.core.types import Timeframe
from tape.features.kinds import FeatureKind, spec_for

if TYPE_CHECKING:
    from typing import Self

    from pydantic import BaseModel as Config

    from tape.core.types import Ticker
    from tape.features.catalog import FeatureCatalog
    from tape.features.kinds import FeatureSpec


class WindowParameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kinds: tuple[FeatureKind, ...]


class Ordered(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lower: str
    upper: str
    strict: bool = True

    def holds(self, lower: int, upper: int) -> bool:
        return lower < upper if self.strict else lower <= upper


class ParameterSpace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    windows: dict[str, WindowParameter] = {}
    ordered: tuple[Ordered, ...] = ()

    @model_validator(mode="after")
    def _known(self) -> Self:
        for o in self.ordered:
            if {o.lower, o.upper} - self.windows.keys():
                raise ValueError(f"ordering {o.lower} < {o.upper} names a parameter that is not a window")
        return self

    def requires(self, config: Config) -> list[FeatureSpec]:
        return [spec_for(kind, getattr(config, name)) for name, p in self.windows.items() for kind in p.kinds]

    def resolve(self, catalog: FeatureCatalog, ticker: Ticker) -> ResolvedSpace:
        choices: dict[Timeframe, dict[str, list[int]]] = {}
        for tf in catalog.timeframes(ticker):
            present = catalog.ids(ticker, tf)
            windows = sorted({s.window for s in catalog.specs(ticker, tf)})
            domain = {
                name: [w for w in windows if all(spec_for(k, w).id in present for k in p.kinds)]
                for name, p in self.windows.items()
            }
            feasible = all(domain.values()) and all(
                any(o.holds(lo, hi) for lo in domain[o.lower] for hi in domain[o.upper]) for o in self.ordered
            )
            if feasible:
                choices[tf] = domain
        return ResolvedSpace(timeframes=list(choices), choices=choices, ordered=self.ordered)


class ResolvedSpace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframes: list[Timeframe]
    choices: dict[Timeframe, dict[str, list[int]]]
    ordered: tuple[Ordered, ...]
