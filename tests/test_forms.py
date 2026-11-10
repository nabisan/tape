from __future__ import annotations

from tape.strategies.sma_cross import SmaCrossConfig
from tape.ui.forms import visible

PROPS = SmaCrossConfig.model_json_schema()["properties"]


def test_sizing_shows_only_its_own_field() -> None:
    assert visible(PROPS["notional"], {"sizing": "notional"})
    assert not visible(PROPS["shares"], {"sizing": "notional"})
    assert visible(PROPS["shares"], {"sizing": "shares"})
    assert not visible(PROPS["notional"], {"sizing": "shares"})
    assert visible(PROPS["direction"], {})


def test_amounts_step_in_round_numbers() -> None:
    assert (PROPS["notional"]["step"], PROPS["shares"]["step"]) == (10_000, 10)
