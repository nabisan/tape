from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.core.hashing import Digest
from tape.engine import tables
from tape.engine.tables import EQUITY, FILLS, TRADES, SchemaError
from tape.service.results import ResultStore, StaleResultError, Table

if TYPE_CHECKING:
    from pathlib import Path

FILL = {
    "order_id": 1,
    "time": pd.Timestamp("2024-03-04 09:30"),
    "side": "buy",
    "quantity": 10,
    "price": 101.5,
    "commission": 0.0,
    "tag": "signal",
}


def test_empty_tables_keep_every_column_and_type() -> None:
    frame = tables.build([], TRADES)
    assert list(frame.columns) == TRADES.names
    assert tables.table(frame, TRADES).schema.equals(TRADES)


def test_rows_with_wrong_types_are_refused() -> None:
    with pytest.raises(SchemaError):
        tables.build([{**FILL, "quantity": "ten"}], FILLS)


def test_frames_must_match_columns_exactly() -> None:
    frame = tables.build([FILL], FILLS)
    with pytest.raises(SchemaError, match="do not match"):
        tables.table(frame.drop(columns=["tag"]), FILLS)
    with pytest.raises(SchemaError, match="do not match"):
        tables.table(frame[list(reversed(frame.columns))], FILLS)


def test_conform_upgrades_column_less_empties_and_rejects_drift() -> None:
    assert tables.conform(pa.table({}), FILLS).schema.equals(FILLS)
    drifted = pa.table({"order_id": [1]})
    with pytest.raises(SchemaError):
        tables.conform(drifted, FILLS)


def test_conform_casts_compatible_legacy_types() -> None:
    legacy = pa.Table.from_pandas(tables.build([FILL], FILLS), preserve_index=False).cast(
        pa.schema([f.with_type(pa.large_string()) if f.type == pa.string() else f for f in FILLS])
    )
    assert tables.conform(legacy, FILLS).schema.equals(FILLS)


def test_bars_take_price_columns_plus_float_indicators() -> None:
    schema = tables.bars_for(["time", "open", "high", "low", "close", "volume", "equity", "sma_20"])
    assert schema.field("sma_20").type == pa.float64()
    assert schema.names[-1] == "sma_20"


def test_summary_types_follow_the_config_values() -> None:
    schema = tables.summary({"fast": 20, "notional": 1e5, "direction": "long_only"}, {"sharpe": tables.F64})
    assert [schema.field(n).type for n in ("fast", "notional", "direction")] == [
        pa.int64(),
        pa.float64(),
        pa.string(),
    ]
    with pytest.raises(SchemaError):
        tables.summary({"bad": [1, 2]}, {})


def test_store_reads_conform_and_flag_unreadable_tables(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    run = tmp_path / "ab12"
    run.mkdir()
    pq.write_table(pa.table({}), run / "fills.parquet")
    assert store.frame(Digest("ab12"), Table.FILLS).columns.tolist() == FILLS.names
    pq.write_table(pa.table({"date": [1]}), run / "equity.parquet")
    with pytest.raises(StaleResultError):
        store.frame(Digest("ab12"), Table.EQUITY)
    assert EQUITY.names[0] == "date"
