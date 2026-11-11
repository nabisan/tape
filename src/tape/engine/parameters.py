from __future__ import annotations

import itertools
from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, model_validator

from tape.core.types import Timeframe
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.features.kinds import FeatureKind, spec_for

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
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


def admits(ordered: Sequence[Ordered], values: Mapping[str, int]) -> bool:
    return all(
        o.holds(values[o.lower], values[o.upper]) for o in ordered if o.lower in values and o.upper in values
    )


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

    def warmup(self, name: str, window: int) -> int:
        return max(spec_for(kind, window).warmup() for kind in self.windows[name].kinds)

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
    ready: dict[Timeframe, dict[str, dict[int, date]]] = {}

    def options(self, name: str, domain: Sequence[int], partners: Mapping[str, Sequence[int]]) -> list[int]:
        values = list(domain)
        for o in self.ordered:
            if o.upper == name and o.lower in partners:
                lows = partners[o.lower]
                values = [w for w in values if any(o.holds(lo, w) for lo in lows)]
            if o.lower == name and o.upper in partners:
                highs = partners[o.upper]
                values = [w for w in values if any(o.holds(w, hi) for hi in highs)]
        return values

    def count(self, grid: Mapping[str, Sequence[int]]) -> int:
        names = list(grid)
        combos = (dict(zip(names, combo, strict=True)) for combo in itertools.product(*grid.values()))
        return sum(admits(self.ordered, values) for values in combos)
