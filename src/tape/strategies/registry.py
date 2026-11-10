from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tape.strategies.channel_breakout import ChannelBreakout
from tape.strategies.sma_cross import SmaCross

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tape.engine.strategy import Strategy, StrategyConfig

STRATEGIES: Final[dict[str, type[Strategy[Any]]]] = {s.name: s for s in (SmaCross, ChannelBreakout)}


def create(name: str, params: Mapping[str, object]) -> Strategy[StrategyConfig]:
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; known: {', '.join(STRATEGIES)}")
    cls = STRATEGIES[name]
    strategy: Strategy[StrategyConfig] = cls(cls.config_type.model_validate(params))
    return strategy
