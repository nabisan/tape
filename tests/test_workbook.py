from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from openpyxl import load_workbook

from tape.core.types import Ticker
from tape.runner import record
from tape.workbook import FIRST_DATA_ROW, FONT, LedgerTooLargeError, band_text, ledger, title
from tests.test_engine_runs import HB_5M, SMA_D1, spec, store

if TYPE_CHECKING:
    from pathlib import Path

    from tape.catalog.store import Store

__all__ = ["store"]


@pytest.mark.parametrize("strategy", [SMA_D1, HB_5M], ids=["sma", "breakout"])
def test_workbook_rows_bands_and_indent(
    store: Store, tmp_path: Path, strategy: tuple[str, dict[str, object]]
) -> None:
    rec = record(store, spec(strategy).model_copy(update={"ticker": Ticker("SPY")}))
    path = tmp_path / "ledger.xlsx"
    written = ledger(path, rec.table, rec.trades, "heading", "subtitle")
    assert written == len(rec.table) + len(rec.trades)
    ws = load_workbook(path).active
    assert ws is not None
    assert ws.cell(1, 1).value == "heading" and ws.cell(1, 1).font.name == FONT
    header = [ws.cell(FIRST_DATA_ROW, c + 1).value for c in range(len(rec.table.schema.names))]
    assert header == [title(n) for n in rec.table.schema.names]
    assert ws.freeze_panes == "B5"
    trade_col = rec.table.schema.names.index("trade") + 1
    bands = rows = 0
    for r in range(FIRST_DATA_ROW + 1, FIRST_DATA_ROW + written + 1):
        first = ws.cell(r, 1)
        if isinstance(first.value, str) and first.value.startswith("Trade "):
            bands += 1
            assert ws.row_dimensions[r].outline_level == 0
            continue
        rows += 1
        held = ws.cell(r, trade_col).value is not None
        assert (ws.row_dimensions[r].outline_level == 1) == held
        assert (first.alignment.indent == 1) == held
    assert bands == len(rec.trades) and rows == len(rec.table)
    assert band_text(rec.trades[1]).startswith("Trade 1 · ")


def test_workbook_refuses_more_rows_than_excel(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = record(store, spec(HB_5M).model_copy(update={"ticker": Ticker("SPY")}))
    monkeypatch.setattr("tape.workbook.EXCEL_ROWS", 10)
    with pytest.raises(LedgerTooLargeError, match="exceed Excel"):
        ledger(tmp_path / "x.xlsx", rec.table, rec.trades, "h", "s")
    assert not (tmp_path / "x.xlsx").exists()
