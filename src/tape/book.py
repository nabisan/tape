from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from tape.core.hashing import Digest
from tape.core.types import Ticker
from tape.engine import tables
from tape.engine.reports import Measure, annualized
from tape.sweep import RETIRED, SUMMARY

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

BOOK: Final = tables.declare(
    [
        ("date", tables.TIME),
        ("equity", tables.F64),
        ("capital", tables.F64),
        ("pnl", tables.F64),
        ("drawdown", tables.F64),
        ("drawdown_pct", tables.F64),
        ("below_capital_pct", tables.F64),
        ("live", tables.I64),
    ]
)
NOT_PARAMETERS: Final = frozenset({"ticker", "key", *SUMMARY, *RETIRED})


class BookError(ValueError):
    pass


class BookStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date
    sessions: int
    capital: Measure
    final_equity: Measure
    total_return_pct: Measure
    cagr_pct: Measure
    volatility_pct: Measure
    sharpe: Measure
    max_drawdown_pct: Measure
    trough: date
    max_drawdown_sessions: int
    max_capital_loss_pct: Measure
    drawdown_now_pct: Measure


def parameters(columns: Collection[str]) -> list[str]:
    return [c for c in columns if c not in NOT_PARAMETERS]


def members(summary: pd.DataFrame, run: str) -> dict[Ticker, Digest]:
    hit = summary.index[summary["key"] == run]
    if not len(hit):
        raise BookError(f"run {run[:8]} is not part of this simulation")
    same = np.ones(len(summary), dtype=bool)
    for name in parameters(summary.columns):
        same &= (summary[name] == summary.at[hit[0], name]).to_numpy(dtype=bool)
    chosen = summary[same]
    if chosen["ticker"].duplicated().any():
        raise BookError(f"run {run[:8]}'s parameters repeat a ticker, so they do not define one book")
    return {Ticker(str(t)): Digest(str(k)) for t, k in zip(chosen["ticker"], chosen["key"], strict=True)}


def combine(sleeves: Mapping[Ticker, pd.Series], cash: float) -> pd.DataFrame:
    if not sleeves:
        raise BookError("a book needs at least one run")
    if not cash > 0:
        raise BookError(f"each sleeve needs positive starting cash, not {cash}")
    marks = pd.DataFrame(dict(sleeves)).sort_index()
    if marks.empty:
        raise BookError("the runs in this book have no equity marks")
    seen = marks.notna()
    live = (seen.cummax() & seen[::-1].cummax()[::-1]).sum(axis=1)
    equity = marks.ffill().fillna(cash).sum(axis=1).to_numpy(dtype=np.float64)
    capital = cash * len(sleeves)
    peak = np.maximum.accumulate(np.r_[capital, equity])[1:]
    return pd.DataFrame(
        {
            "date": marks.index.to_numpy(dtype="datetime64[ns]"),
            "equity": equity,
            "capital": np.full(len(equity), capital),
            "pnl": np.diff(equity, prepend=capital),
            "drawdown": equity - peak,
            "drawdown_pct": 100 * (equity / peak - 1),
            "below_capital_pct": 100 * np.minimum(0.0, equity / capital - 1),
            "live": live.to_numpy(dtype=np.int64),
        }
    )


def encode(frame: pd.DataFrame) -> bytes:
    try:
        found = pa.Table.from_pandas(frame, schema=BOOK, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError, KeyError) as e:
        raise tables.SchemaError(f"book does not match its declared schema: {e}") from e
    buffer = io.BytesIO()
    pq.write_table(tables.check(found, BOOK), buffer, compression="zstd")
    return buffer.getvalue()


def decode(body: bytes) -> pd.DataFrame:
    found = pq.read_table(io.BytesIO(body))
    if not found.schema.equals(BOOK):
        raise tables.SchemaError(f"book arrived as {found.schema}, expected {BOOK}")
    return found.to_pandas()


def stats(frame: pd.DataFrame) -> BookStats:
    if frame.empty:
        raise BookError("an empty book has no statistics")
    equity = frame["equity"].to_numpy(dtype=np.float64)
    capital = float(frame["capital"].iloc[0])
    final = float(equity[-1])
    returns = np.diff(equity, prepend=capital) / np.r_[capital, equity[:-1]]
    vol, cagr, sharpe = annualized(returns, final / capital)
    under = frame["drawdown"].to_numpy() < 0
    longest = length = 0
    for flag in under:
        length = length + 1 if flag else 0
        longest = max(longest, length)
    days = frame["date"].dt.date
    return BookStats(
        start=days.iloc[0],
        end=days.iloc[-1],
        sessions=len(frame),
        capital=capital,
        final_equity=final,
        total_return_pct=100 * (final / capital - 1),
        cagr_pct=100 * cagr,
        volatility_pct=100 * vol,
        sharpe=sharpe,
        max_drawdown_pct=float(frame["drawdown_pct"].min()),
        trough=days.iloc[int(frame["drawdown_pct"].to_numpy().argmin())],
        max_drawdown_sessions=longest,
        max_capital_loss_pct=float(frame["below_capital_pct"].min()),
        drawdown_now_pct=float(frame["drawdown_pct"].iloc[-1]),
    )
