from __future__ import annotations

from datetime import date

from tape.engine.backtest import ExecutionSpec
from tape.strategies.breakout import HighBreakoutConfig
from tape.strategies.sma_cross import SmaCrossConfig
from tape.ui import forms
from tape.ui.forms import fixed, visible

PROPS = SmaCrossConfig.model_json_schema()["properties"]


def test_sizing_shows_only_its_own_field() -> None:
    assert visible(PROPS["notional"], {"sizing": "notional"})
    assert not visible(PROPS["shares"], {"sizing": "notional"})
    assert visible(PROPS["shares"], {"sizing": "shares"})
    assert not visible(PROPS["notional"], {"sizing": "shares"})
    assert visible(PROPS["direction"], {})


def test_amounts_step_in_round_numbers() -> None:
    assert (PROPS["notional"]["step"], PROPS["shares"]["step"]) == (10_000, 10)


def test_fixed_values_are_not_inputs() -> None:
    props = HighBreakoutConfig.model_json_schema()["properties"]
    assert fixed(props["direction"])
    assert props["direction"]["const"] == "long_only"
    assert not fixed(props["stop_loss_pct"])
    assert not fixed(PROPS["direction"])


def test_presets_only_offer_ranges_inside_the_history() -> None:
    last = date(2026, 4, 30)
    assert forms.presets(date(2018, 5, 1), last) == ["1Y", "3Y", "5Y", "Max"]
    assert forms.presets(date(2023, 4, 30), last) == ["1Y", "3Y", "Max"]
    assert forms.presets(date(2024, 1, 2), last) == ["1Y", "Max"]
    assert forms.presets(date(2025, 11, 3), last) == ["Max"]
    assert forms.preset_span("3Y", date(2018, 5, 1), last) == (date(2023, 4, 30), last)
    assert forms.preset_span("Max", date(2024, 1, 2), last) == (date(2024, 1, 2), last)
    assert forms.years_before(date(2024, 2, 29), 1) == date(2023, 2, 28)


def test_capital_follows_the_notional_for_notional_sizing() -> None:
    base = ExecutionSpec()
    sized = forms.capital("high_breakout", {"timeframe": "5m", "notional": 10_000.0}, base)
    assert sized.initial_cash == 10_000.0
    assert forms.capital("sma_cross", {}, base).initial_cash == SmaCrossConfig().notional
    shares = forms.capital("sma_cross", {"sizing": "shares", "shares": 250}, base)
    assert shares.initial_cash == base.initial_cash
