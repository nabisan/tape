from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pandas as pd

from tape.engine.costs import charged
from tape.lab.report import held
from tape.lab.schema import label
from tape.lab.views import Column, stamp

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from tape.engine.costs import Cost

TRADES: Final[dict[str, Column | None]] = {
    "trade_id": None,
    "side": Column("Side", ""),
    "quantity": Column("Qty", "{:,.0f}"),
    "entry_time": Column("Entry", ""),
    "entry_price": Column("Entry px", "{:,.2f}"),
    "exit_time": Column("Exit", ""),
    "exit_price": Column("Exit px", "{:,.2f}"),
    "exit_tag": Column("Exit on", ""),
    "duration": Column("Held", ""),
    "gross_pnl": None,
    "commission": Column("Comm $", "{:,.2f}"),
    "net_pnl": Column("Net $", "{:,.2f}"),
    "return_pct": Column("Ret %", "{:.2f}"),
    "mae_pct": Column("MAE %", "{:.2f}"),
    "mfe_pct": Column("MFE %", "{:.2f}"),
    "open": None,
}
ORDERS: Final[dict[str, Column | None]] = {
    "order_id": Column("#", "{:,.0f}"),
    "submitted": Column("Submitted", ""),
    "closed": Column("Closed", ""),
    "price": Column("Price", "{:,.2f}"),
    "fill_price": Column("Fill px", "{:,.2f}"),
    "quantity": Column("Qty", "{:,.0f}"),
}


PRICES: Final = frozenset({"entry_price", "exit_price", "price", "fill_price"})
STOCK_DIGITS: Final = 2


def priced(columns: Mapping[str, Column | None], digits: int) -> dict[str, Column | None]:
    if digits == STOCK_DIGITS:
        return dict(columns)
    return {
        c: Column(col.label, f"{{:,.{digits}f}}") if col is not None and c in PRICES else col
        for c, col in columns.items()
    }


def sheet(frame: pd.DataFrame, columns: Mapping[str, Column | None], costs: Collection[Cost]) -> pd.DataFrame:
    keep = [c for c in frame.columns if (c not in columns or columns[c] is not None) and charged(c, costs)]
    shown = frame[keep].copy()
    for c in keep:
        if pd.api.types.is_datetime64_any_dtype(frame[c]):
            shown[c] = [stamp(None if pd.isna(v) else v.to_pydatetime()) for v in frame[c]]
        elif pd.api.types.is_timedelta64_dtype(frame[c]):
            shown[c] = frame[c].map(held)
    names = {c: col.label if (col := columns.get(c)) is not None else label(c) for c in keep}
    return shown.rename(columns=names)
