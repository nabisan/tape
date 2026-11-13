from __future__ import annotations

from tape.engine.costs import REQUIRES, Cost, charged
from tape.engine.matching import FillModel
from tape.engine.reports import Metrics
from tape.engine.tables import LEDGER_BOOK, TRADES


def test_commission_is_a_cost_only_when_the_fill_model_charges_it() -> None:
    assert FillModel().costs == frozenset()
    assert FillModel(commission_per_share=0.005).costs == {Cost.COMMISSION}
    assert FillModel(min_commission=1.0).costs == {Cost.COMMISSION}


def test_commission_columns_are_shown_only_when_charged() -> None:
    for column in ("commission", "total_commission"):
        assert not charged(column, frozenset())
        assert charged(column, {Cost.COMMISSION})
    assert charged("net_pnl", frozenset())


def test_every_gated_name_exists_somewhere() -> None:
    ledger = {c if isinstance(c, tuple) else c.name for c in LEDGER_BOOK}
    names = (
        set(Metrics.model_fields) | set(TRADES.names) | {c[0] if isinstance(c, tuple) else c for c in ledger}
    )
    assert set(REQUIRES) <= names
