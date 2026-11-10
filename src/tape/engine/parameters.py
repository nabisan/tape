from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, model_validator

from tape.core.types import Timeframe
from tape.features.grid import DEFAULT_GRID, FeatureGrid
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

    def resolve(
        self, catalog: FeatureCatalog, ticker: Ticker, grid: FeatureGrid = DEFAULT_GRID
    ) -> ResolvedSpace:
        stored = set(catalog.timeframes(ticker))
        choices: dict[Timeframe, dict[str, list[int]]] = {}
        on_demand: list[Timeframe] = []
        for tf in (tf for tf in Timeframe if tf in stored or grid.covers(tf)):
            present = catalog.ids(ticker, tf)
            available = present | grid.ids(tf)
            windows = sorted(
                {s.window for s in catalog.specs(ticker, tf)}
                | ({s.window for s in grid.specs} if grid.covers(tf) else set())
            )
            domain = {
                name: [w for w in windows if all(spec_for(k, w).id in available for k in p.kinds)]
                for name, p in self.windows.items()
            }
            feasible = all(domain.values()) and all(
                any(o.holds(lo, hi) for lo in domain[o.lower] for hi in domain[o.upper]) for o in self.ordered
            )
            if not feasible:
                continue
            choices[tf] = domain
            needed = {
                spec_for(k, w).id for name, p in self.windows.items() for w in domain[name] for k in p.kinds
            }
            if not needed <= present:
                on_demand.append(tf)
        return ResolvedSpace(
            timeframes=list(choices), choices=choices, ordered=self.ordered, on_demand=on_demand
        )


class ResolvedSpace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframes: list[Timeframe]
    choices: dict[Timeframe, dict[str, list[int]]]
    ordered: tuple[Ordered, ...]
    on_demand: list[Timeframe] = []
