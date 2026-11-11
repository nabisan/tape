from __future__ import annotations

from tape.core.types import Timeframe
from tape.engine.parameters import Ordered, ResolvedSpace, admits

WINDOWS = [5, 10, 20, 50, 100, 150, 200]
ORDER = (Ordered(lower="fast", upper="slow", strict=True),)
SPACE = ResolvedSpace(
    timeframes=[Timeframe.D1],
    choices={Timeframe.D1: {"fast": WINDOWS, "slow": WINDOWS}},
    ordered=ORDER,
)


def test_fast_offers_only_values_with_a_larger_slow() -> None:
    assert SPACE.options("fast", WINDOWS, {"slow": WINDOWS}) == [5, 10, 20, 50, 100, 150]


def test_slow_offers_only_values_above_a_chosen_fast() -> None:
    assert SPACE.options("slow", WINDOWS, {"fast": [10, 20, 50]}) == [20, 50, 100, 150, 200]


def test_single_choice_narrows_like_the_backtest_form() -> None:
    assert SPACE.options("slow", WINDOWS, {"fast": [50]}) == [100, 150, 200]


def test_count_includes_only_ordered_pairs() -> None:
    assert SPACE.count({"fast": [10, 20, 50], "slow": [20, 50, 100]}) == 6
    assert SPACE.count({"fast": [50], "slow": [20]}) == 0
    assert SPACE.count({}) == 1


def test_admits_ignores_unrelated_parameters() -> None:
    assert admits(ORDER, {"fast": 10, "slow": 20})
    assert not admits(ORDER, {"fast": 20, "slow": 20})
    assert admits(ORDER, {"fast": 20})
