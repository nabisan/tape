from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Final

import xlsxwriter

from tape.engine import tables

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pyarrow as pa

    from tape.engine.ledger import TradeSummary

FONT: Final = "JetBrains Mono"
SIZE: Final = 9
INK: Final = "#222222"
BLACK: Final = "#000000"
MUTED: Final = "#666666"
HEADER: Final = "#D9D9D9"
BAND: Final = "#C7C7C7"
GAIN: Final = ("#2E7D32", "#CDEFD7")
LOSS: Final = ("#C0392B", "#F8C7CF")

FIRST_DATA_ROW: Final = 4
EXCEL_ROWS: Final = 1_048_576
BATCH: Final = 65_536
EPOCH_DAYS: Final = 25_569
NS_PER_DAY: Final = 86_400 * 10**9

TIME: Final = "dd mmm yyyy hh:mm:ss"
PRICE: Final = "#,##0.00##"
COUNT: Final = "#,##0"
SIGNED: Final = "#,##0;-#,##0;0"
DASH: Final = "\u2013"
MINUS: Final = "\u2212"
MONEY: Final = f'$#,##0.00;($#,##0.00);"{DASH}"'

TITLES: Final = {
    "time": "Time",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "state": "State",
    "trade": "Trade",
    "position": "Position",
    "avg_cost": "Avg cost",
    "buy_trigger": "Buy trigger",
    "sell_trigger": "Sell trigger",
    "stop": "Stop",
    "event": "Event",
    "realized_pnl": "Realized P&L",
    "commission": "Commission",
    "unrealized_pnl": "Unrealized P&L",
    "bar_pnl": "Bar P&L",
    "cumulative_pnl": "Cumulative P&L",
    "equity": "Equity",
}
KINDS: Final = {
    "time": "time",
    "volume": "count",
    "state": "text",
    "trade": "count",
    "position": "signed",
    "event": "text",
    "realized_pnl": "realized",
    "commission": "money",
    "unrealized_pnl": "pnl",
    "bar_pnl": "pnl",
    "cumulative_pnl": "pnl",
    "equity": "money",
}
WIDTHS: Final = {"time": 21, "text": 11, "count": 9, "signed": 10, "price": 11, "money": 14, "pnl": 15}
EVENT_WIDTH: Final = 46
NUMBER_FORMATS: Final = {
    "time": TIME,
    "count": COUNT,
    "signed": SIGNED,
    "price": PRICE,
    "money": MONEY,
    "pnl": MONEY,
    "realized": MONEY,
}


class LedgerTooLargeError(ValueError):
    pass


def title(name: str) -> str:
    if name in TITLES:
        return TITLES[name]
    kind, _, window = name.partition("_")
    return f"{kind.upper()} {window}".strip()


def kind(name: str) -> str:
    return KINDS.get(name, "price")


class Styles:
    def __init__(self, book: Any) -> None:
        self.book = book
        self.cache: dict[tuple[str, str, int], Any] = {}

    def get(self, k: str, tone: str = "", indent: int = 0) -> Any:
        key = (k, tone, indent)
        if key not in self.cache:
            spec: dict[str, Any] = {"font_name": FONT, "font_size": SIZE, "font_color": INK}
            if k in NUMBER_FORMATS:
                spec["num_format"] = NUMBER_FORMATS[k]
            spec["align"] = "left" if k in {"text", "time"} else "right"
            if indent:
                spec["indent"] = indent
            if tone:
                color, fill = GAIN if tone == "gain" else LOSS
                spec["font_color"] = color
                if k == "realized":
                    spec["bg_color"] = fill
            self.cache[key] = self.book.add_format(spec)
        return self.cache[key]


def _tone(value: float) -> str:
    return "gain" if value > 0 else "loss" if value < 0 else ""


def band_text(t: TradeSummary) -> str:
    side = "Long" if t.long else "Short"
    exit_part = "open" if t.open else f"exit {t.exit_price:,.4f}"
    sign = "+" if t.net_pnl > 0 else MINUS if t.net_pnl < 0 else ""
    result = "" if t.open else f" · net {sign}${abs(t.net_pnl):,.2f}"
    return f"Trade {t.id} · {side} {t.quantity:,} · entry {t.entry_price:,.4f} → {exit_part}{result}"


def ledger(
    path: Path, table: pa.Table, trades: Mapping[int, TradeSummary], heading: str, subtitle: str
) -> int:
    names = table.schema.names
    trade_ids = table["trade"].to_pylist()
    bands = sum(1 for i, t in enumerate(trade_ids) if t is not None and (i == 0 or trade_ids[i - 1] != t))
    if FIRST_DATA_ROW + len(table) + bands > EXCEL_ROWS:
        raise LedgerTooLargeError(
            f"{len(table) + bands:,} rows exceed Excel's {EXCEL_ROWS:,}; "
            "use a coarser timeframe or a shorter span"
        )
    book = xlsxwriter.Workbook(str(path), {"constant_memory": True})
    ws = book.add_worksheet("MTM ledger")
    styles = Styles(book)
    base = {"font_name": FONT, "font_size": SIZE}
    heading_style = book.add_format({**base, "font_size": 12, "bold": True, "font_color": BLACK})
    ws.write_string(0, 0, heading, heading_style)
    ws.write_string(1, 0, subtitle, book.add_format({**base, "font_color": MUTED, "italic": True}))
    header = book.add_format(
        {**base, "bold": True, "bg_color": HEADER, "align": "center", "font_color": BLACK}
    )
    band = book.add_format({**base, "font_size": 10, "bold": True, "bg_color": BAND, "font_color": BLACK})
    band_fill = book.add_format({**base, "bg_color": BAND})
    kinds = [kind(n) for n in names]
    for c, (n, k) in enumerate(zip(names, kinds, strict=True)):
        ws.write_string(3, c, title(n), header)
        ws.set_column(c, c, EVENT_WIDTH if n == "event" else WIDTHS.get(k, 11))
    ws.freeze_panes(FIRST_DATA_ROW, 1)
    row = FIRST_DATA_ROW
    previous: int | None = None
    for batch in table.to_batches(max_chunksize=BATCH):
        columns = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        times = batch.column(names.index("time")).cast(tables.I64).to_numpy(zero_copy_only=False)
        trade_col = columns[names.index("trade")]
        for i in range(batch.num_rows):
            trade = trade_col[i]
            if trade is not None and trade != previous and trade in trades:
                ws.write_string(row, 0, band_text(trades[trade]), band)
                for c in range(1, len(names)):
                    ws.write_blank(row, c, None, band_fill)
                row += 1
            previous = trade
            indent = 1 if trade is not None else 0
            if indent:
                ws.set_row(row, None, None, {"level": 1})
            for c, k in enumerate(kinds):
                v = columns[c][i]
                if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
                    continue
                if k == "time":
                    ws.write_number(row, c, times[i] / NS_PER_DAY + EPOCH_DAYS, styles.get(k, "", indent))
                elif k == "text":
                    ws.write_string(row, c, str(v), styles.get(k, "", indent))
                else:
                    tone = _tone(float(v)) if k in {"pnl", "realized"} else ""
                    ws.write_number(row, c, float(v), styles.get(k, tone, indent))
            row += 1
    ws.autofilter(3, 0, max(row - 1, 3), len(names) - 1)
    ws.outline_settings(True, False, True, False)
    book.close()
    return row - FIRST_DATA_ROW
