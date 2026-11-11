from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import pyarrow as pa
import pyarrow.compute as pc

from tape.engine.ledger import BarState
from tape.engine.orders import OrderOrigin, OrderSide, OrderStatus, OrderType, TimeInForce
from tape.engine.portfolio import PositionSide

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from enum import StrEnum

    import pandas as pd

TIME: Final[pa.DataType] = pa.timestamp("ns")
F64: Final[pa.DataType] = pa.float64()
I64: Final[pa.DataType] = pa.int64()
TEXT: Final[pa.DataType] = pa.string()
DOMAIN: Final = b"domain"


type Column = tuple[str, pa.DataType] | pa.Field[Any]


def enum(name: str, kind: type[StrEnum]) -> pa.Field[Any]:
    return pa.field(name, TEXT, metadata={DOMAIN: ",".join(m.value for m in kind).encode()})


def declare(columns: Iterable[Column]) -> pa.Schema:
    return pa.schema([c if isinstance(c, pa.Field) else pa.field(c[0], c[1]) for c in columns])


ORDERS: Final = declare(
    [
        ("order_id", I64),
        ("submitted", TIME),
        enum("side", OrderSide),
        enum("type", OrderType),
        ("quantity", I64),
        ("price", F64),
        enum("tif", TimeInForce),
        enum("status", OrderStatus),
        ("closed", TIME),
        ("fill_price", F64),
        ("tag", TEXT),
        enum("origin", OrderOrigin),
        ("reason", TEXT),
    ]
)
FILLS: Final = declare(
    [
        ("order_id", I64),
        ("time", TIME),
        enum("side", OrderSide),
        ("quantity", I64),
        ("price", F64),
        ("commission", F64),
        ("tag", TEXT),
    ]
)
TRADES: Final = declare(
    [
        ("trade_id", I64),
        enum("side", PositionSide),
        ("quantity", I64),
        ("entry_time", TIME),
        ("entry_price", F64),
        ("exit_time", TIME),
        ("exit_price", F64),
        ("exit_tag", TEXT),
        ("duration", pa.duration("ns")),
        ("gross_pnl", F64),
        ("commission", F64),
        ("net_pnl", F64),
        ("return_pct", F64),
        ("mae_pct", F64),
        ("mfe_pct", F64),
        ("open", pa.bool_()),
    ]
)
EQUITY: Final = declare(
    [
        ("date", TIME),
        ("equity", F64),
        ("pnl", F64),
        ("return", F64),
        ("drawdown", F64),
        ("drawdown_pct", F64),
        ("position", I64),
        ("close", F64),
    ]
)
PRICE: Final[tuple[tuple[str, pa.DataType], ...]] = (
    ("time", TIME),
    ("open", F64),
    ("high", F64),
    ("low", F64),
    ("close", F64),
    ("volume", pa.uint64()),
    ("equity", F64),
)


LEDGER_BAR: Final[tuple[tuple[str, pa.DataType], ...]] = (
    ("time", TIME),
    ("open", F64),
    ("high", F64),
    ("low", F64),
    ("close", F64),
    ("volume", pa.uint64()),
)
LEDGER_BOOK: Final[tuple[Column, ...]] = (
    enum("state", BarState),
    ("trade", I64),
    ("position", I64),
    ("avg_cost", F64),
    ("buy_trigger", F64),
    ("sell_trigger", F64),
    ("stop", F64),
    ("event", TEXT),
    ("realized_pnl", F64),
    ("commission", F64),
    ("unrealized_pnl", F64),
    ("bar_pnl", F64),
    ("cumulative_pnl", F64),
    ("equity", F64),
)


class SchemaError(ValueError):
    pass


def domains(schema: pa.Schema) -> dict[str, frozenset[str]]:
    return {
        f.name: frozenset(f.metadata[DOMAIN].decode().split(","))
        for f in schema
        if f.metadata is not None and DOMAIN in f.metadata
    }


def check(found: pa.Table, schema: pa.Schema) -> pa.Table:
    for name, allowed in domains(schema).items():
        column = found[name]
        outside = pc.unique(
            pc.filter(column, pc.invert(pc.is_in(column, value_set=pa.array(sorted(allowed)))))
        )
        if len(outside):
            raise SchemaError(f"column {name} holds {outside.to_pylist()} outside {sorted(allowed)}")
    return found


def bars(indicators: Iterable[str]) -> pa.Schema:
    return pa.schema([*PRICE, *((name, F64) for name in indicators)])


def bars_for(names: Sequence[str]) -> pa.Schema:
    base = {name for name, _ in PRICE}
    return bars(n for n in names if n not in base)


def ledger(indicators: Iterable[str]) -> pa.Schema:
    return declare([*LEDGER_BAR, *((name, F64) for name in indicators), *LEDGER_BOOK])


def paths(columns: Iterable[str]) -> pa.Schema:
    single: pa.DataType = pa.float32()
    return pa.schema([("date", TIME), *((name, single) for name in columns)])


def arrow_type(value: object) -> pa.DataType:
    match value:
        case bool():
            return pa.bool_()
        case int():
            return I64
        case float():
            return F64
        case str():
            return TEXT
        case _:
            raise SchemaError(f"no column type for {type(value).__name__} value {value!r}")


def summary(params: Mapping[str, object], metrics: Mapping[str, pa.DataType]) -> pa.Schema:
    swept = [(name, arrow_type(value)) for name, value in params.items()]
    return pa.schema([("ticker", TEXT), *swept, ("key", TEXT), *metrics.items()])


def arrow(rows: Sequence[Mapping[str, object]], schema: pa.Schema) -> pa.Table:
    try:
        found = pa.Table.from_pylist(list(rows), schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
        raise SchemaError(f"rows do not match the declared schema: {e}") from e
    return check(found, schema)


def build(rows: Sequence[Mapping[str, object]], schema: pa.Schema) -> pd.DataFrame:
    return arrow(rows, schema).to_pandas()


def table(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    if list(frame.columns) != schema.names:
        raise SchemaError(f"columns {list(frame.columns)} do not match the declared {schema.names}")
    try:
        found = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
        raise SchemaError(f"values do not match the declared schema: {e}") from e
    return check(found, schema)


def conform(found: pa.Table, schema: pa.Schema) -> pa.Table:
    if not found.schema.names and found.num_rows == 0:
        return schema.empty_table()
    if found.schema.names != schema.names:
        raise SchemaError(f"stored columns {found.schema.names} do not match the declared {schema.names}")
    if found.schema.equals(schema):
        return check(found, schema)
    try:
        cast = found.cast(schema)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError) as e:
        raise SchemaError(f"stored values do not match the declared schema: {e}") from e
    return check(cast, schema)
