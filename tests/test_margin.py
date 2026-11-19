from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from tape.core.hashing import Digest
from tape.core.types import Ticker
from tape.engine.backtest import ENGINE_VERSION, ExecutionSpec
from tape.engine.margin import MarginSpec
from tape.runner import RunSpec
from tape.service.results import ResultStore, stamped
from tape.sweep import SweepSpec

DATA = Digest("d" * 32)


def run(**execution: object) -> RunSpec:
    return RunSpec(
        ticker=Ticker("EURUSD"),
        start=date(2024, 1, 2),
        end=date(2024, 6, 28),
        strategy="sma_cross",
        params={"timeframe": "1h"},
        execution=ExecutionSpec.model_validate(execution),
    )


def test_margin_is_notional_over_leverage_and_maintenance_is_a_share_of_it() -> None:
    m = MarginSpec()
    assert m.initial(10_000) == pytest.approx(333.333333333)
    assert m.maintenance_margin(10_000) == pytest.approx(166.666666667)
    assert m.initial(-10_000) == m.initial(10_000)


@pytest.mark.parametrize(
    "bad", [{"leverage": 1}, {"leverage": 0.5}, {"maintenance": 0}, {"maintenance": 1.5}]
)
def test_impossible_margin_settings_are_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        MarginSpec.model_validate(bad)


def test_an_absent_margin_dumps_exactly_as_before_so_existing_keys_hold() -> None:
    before = {
        "initial_cash": 10_000.0,
        "fill": {
            "commission_per_share": 0.0,
            "min_commission": 0.0,
            "slippage_bps": 0.0,
            "prob_fill_on_limit": 1.0,
            "path": "adverse_first",
            "seed": 0,
        },
        "fill_on": "bar",
        "intraday": False,
        "entry_delay_minutes": 0,
        "squareoff_minutes_before_close": 5,
    }
    assert ExecutionSpec().model_dump(mode="json") == before
    assert run().model_dump(mode="json")["execution"] == before
    assert ExecutionSpec.model_validate_json(ExecutionSpec().model_dump_json()) == ExecutionSpec()


def test_setting_a_margin_changes_run_and_simulation_keys() -> None:
    assert run().key(DATA) != run(margin={"leverage": 30}).key(DATA)
    base = {
        "tickers": ["EURUSD"],
        "start": date(2024, 1, 2),
        "end": date(2024, 6, 28),
        "strategy": "sma_cross",
        "timeframe": "1h",
    }
    plain = SweepSpec.model_validate(base)
    assert "margin" not in plain.model_dump(mode="json")["execution"]
    margined = SweepSpec.model_validate({**base, "execution": {"margin": {"leverage": 30}}})
    assert plain.key != margined.key
    assert SweepSpec.model_validate_json(margined.model_dump_json()) == margined


def test_saved_simulations_carry_the_engine_version_and_drop_stale_paths(tmp_path: Path) -> None:
    results = ResultStore(tmp_path)
    key = Digest("a" * 32)
    table = pa.table({"key": ["r1"]})
    results.sweep_paths(key).parent.mkdir(parents=True, exist_ok=True)
    results.sweep_paths(key).write_bytes(b"stale")
    spec = SweepSpec.model_validate(
        {
            "tickers": ["EURUSD"],
            "start": date(2024, 1, 2),
            "end": date(2024, 6, 28),
            "strategy": "sma_cross",
            "timeframe": "1h",
        }
    )
    results.save_sweep(key, spec, table)
    assert results.sweep_engine(key) == ENGINE_VERSION
    assert not results.sweep_paths(key).exists()
    results.replace_summary(key, table)
    assert results.sweep_engine(key) == ENGINE_VERSION
    pq.write_table(stamped(table, None), results.sweep_summary(key))
    assert results.sweep_engine(key) is None
    results.replace_summary(key, table)
    assert results.sweep_engine(key) is None
