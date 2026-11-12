from __future__ import annotations

from tape.engine.exits import REQUIRES, Exit, applies
from tape.engine.reports import Metrics
from tape.strategies.registry import STRATEGIES


def test_metric_requirements_name_real_metrics() -> None:
    assert set(REQUIRES) <= set(Metrics.model_fields)


def test_win_rate_needs_a_take_profit() -> None:
    assert not applies("win_rate_pct", {Exit.SIGNAL})
    assert not applies("win_rate_pct", {Exit.STOP_LOSS})
    assert applies("win_rate_pct", {Exit.STOP_LOSS, Exit.TAKE_PROFIT})
    assert applies("sharpe", set())
    assert applies("", set())


def test_strategies_declare_their_exits() -> None:
    assert STRATEGIES["sma_cross"].exits == {Exit.SIGNAL}
    assert STRATEGIES["high_breakout"].exits == {Exit.STOP_LOSS}
    assert STRATEGIES["low_breakdown"].exits == {Exit.STOP_LOSS}
