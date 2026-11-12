from __future__ import annotations

import math
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import numpy.typing as npt
import pandas as pd
import pyarrow as pa
from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

from tape.engine import tables
from tape.engine.ledger import event

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tape.calendar import Sessions
    from tape.catalog.bars import Bars
    from tape.engine.backtest import Context, Run
    from tape.engine.ledger import Ledger
    from tape.engine.orders import Order

TRADING_DAYS = 252


def _ts(ns: int | None) -> pd.Timestamp | None:
    return None if ns is None else pd.Timestamp(ns, unit="ns")


def _moment(ns: int | None) -> datetime | None:
    return None if ns is None else pd.Timestamp(ns, unit="ns").to_pydatetime()


def orders(run: Run) -> pd.DataFrame:
    return tables.build(
        [
            {
                "order_id": o.id,
                "submitted": _ts(o.submitted_ns),
                "side": o.side.value,
                "type": o.type.value,
                "quantity": o.quantity,
                "price": o.price,
                "tif": o.tif.value,
                "status": o.status.value,
                "closed": _ts(o.closed_ns),
                "fill_price": o.fill_price,
                "tag": o.tag,
                "origin": o.origin.value,
                "reason": o.reason,
            }
            for o in run.orders
        ],
        tables.ORDERS,
    )


def fills(run: Run) -> pd.DataFrame:
    return tables.build(
        [
            {
                "order_id": f.order_id,
                "time": _ts(f.time_ns),
                "side": f.side.value,
                "quantity": f.quantity,
                "price": f.price,
                "commission": f.commission,
                "tag": f.tag,
            }
            for f in run.fills
        ],
        tables.FILLS,
    )


def trades(run: Run, m1: Bars) -> pd.DataFrame:
    rows = []
    last_ns, last_close = int(m1.time_ns[-1]), float(m1.close[-1])
    if len(run.sessions.close):
        last_close = float(run.sessions.close[-1])
    for t in run.trades:
        exit_ns = t.exit_ns if t.exit_ns is not None else last_ns
        exit_px = t.exit_price if t.closed else last_close
        a = int(np.searchsorted(m1.time_ns, t.entry_ns, side="left"))
        b = int(np.searchsorted(m1.time_ns, exit_ns, side="left"))
        hi = max(float(m1.high[a:b].max()) if b > a else exit_px, exit_px)
        lo = min(float(m1.low[a:b].min()) if b > a else exit_px, exit_px)
        adverse, favourable = (lo, hi) if t.sign > 0 else (hi, lo)
        qty = t.max_quantity
        open_pnl = 0.0 if t.closed else t.sign * t.quantity * (last_close - t.entry_price)
        gross = t.realized + open_pnl
        rows.append(
            {
                "trade_id": t.id,
                "side": t.side.value,
                "quantity": qty,
                "entry_time": _ts(t.entry_ns),
                "entry_price": t.entry_price,
                "exit_time": _ts(t.exit_ns),
                "exit_price": exit_px,
                "exit_tag": t.exit_tag if t.closed else "open",
                "duration": pd.Timedelta(exit_ns - t.entry_ns, unit="ns"),
                "gross_pnl": gross,
                "commission": t.commission,
                "net_pnl": gross - t.commission,
                "return_pct": 100 * (gross - t.commission) / (t.entry_price * qty),
                "mae_pct": 100 * t.sign * (adverse - t.entry_price) / t.entry_price,
                "mfe_pct": 100 * t.sign * (favourable - t.entry_price) / t.entry_price,
                "open": not t.closed,
            }
        )
    return tables.build(rows, tables.TRADES)


def equity(run: Run, sessions: Sessions, initial_cash: float) -> pd.DataFrame:
    marks = run.sessions
    eq = pd.Series(marks.equity, dtype=float)
    peak = np.maximum.accumulate(np.r_[initial_cash, marks.equity])[1:]
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(sessions.day[marks.session]),
            "equity": eq,
            "pnl": eq.diff().fillna(eq.iloc[0] - initial_cash if len(eq) else 0.0),
            "return": eq.pct_change().fillna(eq.iloc[0] / initial_cash - 1 if len(eq) else 0.0),
            "drawdown": eq - peak,
            "drawdown_pct": 100 * (eq / peak - 1),
            "position": marks.position,
            "close": marks.close,
        }
    )
    return tables.table(frame, tables.EQUITY).to_pandas()


def _from_wire(v: object) -> object:
    return math.nan if v is None else v


def _to_wire(v: float) -> float | None:
    return None if math.isnan(v) else v


type Measure = Annotated[float, BeforeValidator(_from_wire), PlainSerializer(_to_wire, when_used="json")]


def _day(v: object) -> object:
    return None if v == "" else v


type Day = Annotated[date | None, BeforeValidator(_day)]


class Metrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: Day
    end: Day
    sessions: int
    initial_cash: Measure
    final_equity: Measure
    net_pnl: Measure
    total_return_pct: Measure
    cagr_pct: Measure
    volatility_pct: Measure
    sharpe: Measure
    sortino: Measure
    max_drawdown_pct: Measure
    max_drawdown_sessions: int
    max_capital_loss_pct: Measure
    calmar: Measure
    time_in_market_pct: Measure
    trades: int
    win_rate_pct: Measure
    avg_win: Measure
    avg_loss: Measure
    payoff_ratio: Measure
    profit_factor: Measure
    expectancy: Measure
    avg_trade_return_pct: Measure
    avg_duration_hours: Measure
    total_commission: Measure
    buy_hold_return_pct: Measure
    first_signal: datetime | None = None
    last_signal: datetime | None = None


def _ratio(a: float, b: float) -> float:
    return a / b if b else math.nan


def capital_loss_pct(equity: npt.NDArray[np.float64], initial_cash: float) -> float:
    if not initial_cash > 0:
        return math.nan
    if not len(equity):
        return 0.0
    return 100 * min(0.0, float(equity.min()) / initial_cash - 1)


def time_in_market_pct(bar_ns: npt.NDArray[np.int64], tr: pd.DataFrame) -> float:
    if not len(bar_ns):
        return math.nan
    held = np.zeros(len(bar_ns) + 1, dtype=np.int64)
    if len(tr):
        entry = tr["entry_time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
        closed = tr["exit_time"].to_numpy(dtype="datetime64[ns]").view(np.int64)
        exit_ = np.where(tr["open"].to_numpy(dtype=bool), np.iinfo(np.int64).max, closed)
        lo = np.maximum(np.searchsorted(bar_ns, entry, side="right") - 1, 0)
        hi = np.searchsorted(bar_ns, exit_, side="left") - 1
        live = hi >= lo
        np.add.at(held, lo[live], 1)
        np.add.at(held, hi[live] + 1, -1)
    return 100 * float((np.cumsum(held[:-1]) > 0).mean())


def annualized(r: npt.NDArray[np.float64], growth: float) -> tuple[float, float, float]:
    years = len(r) / TRADING_DAYS
    vol = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(r) > 1 else math.nan
    cagr = growth ** (1 / years) - 1 if years > 0 and growth > 0 else math.nan
    sharpe = _ratio(float(r.mean()) * TRADING_DAYS, vol) if len(r) > 1 else math.nan
    return vol, cagr, sharpe


def metrics(
    eq: pd.DataFrame,
    tr: pd.DataFrame,
    initial_cash: float,
    orders: Sequence[Order],
    first_open: float,
    *,
    bar_ns: npt.NDArray[np.int64],
) -> Metrics:
    signals = sorted(ns for o in orders if (ns := o.signal_ns) is not None)
    r = eq["return"].to_numpy(dtype=float)
    values = eq["equity"].to_numpy(dtype=float)
    final = float(values[-1]) if len(values) else initial_cash
    downside = r[r < 0]
    under = eq["drawdown"].to_numpy() < 0
    longest = run_length = 0
    for flag in under:
        run_length = run_length + 1 if flag else 0
        longest = max(longest, run_length)
    closed = tr[~tr["open"]] if len(tr) else tr
    pnl = closed["net_pnl"].to_numpy(dtype=float) if len(closed) else np.empty(0)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    vol, cagr, sharpe = annualized(r, final / initial_cash)
    max_dd = float(eq["drawdown_pct"].min()) if len(eq) else 0.0
    closes = eq["close"].to_numpy(dtype=float)
    return Metrics(
        start=eq["date"].iloc[0].date() if len(eq) else None,
        end=eq["date"].iloc[-1].date() if len(eq) else None,
        sessions=len(eq),
        initial_cash=initial_cash,
        final_equity=final,
        net_pnl=final - initial_cash,
        total_return_pct=100 * (final / initial_cash - 1),
        cagr_pct=100 * cagr,
        volatility_pct=100 * vol,
        sharpe=sharpe,
        sortino=_ratio(
            float(r.mean()) * TRADING_DAYS,
            float(np.sqrt((downside**2).sum() / len(r)) * math.sqrt(TRADING_DAYS)),
        )
        if len(r)
        else math.nan,
        max_drawdown_pct=max_dd,
        max_drawdown_sessions=longest,
        max_capital_loss_pct=capital_loss_pct(values, initial_cash),
        calmar=_ratio(100 * cagr, -max_dd),
        time_in_market_pct=time_in_market_pct(bar_ns, tr),
        trades=len(pnl),
        win_rate_pct=100 * _ratio(len(wins), len(pnl)),
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        payoff_ratio=_ratio(
            float(wins.mean()) if len(wins) else 0.0, -float(losses.mean()) if len(losses) else 0.0
        ),
        profit_factor=_ratio(float(wins.sum()), -float(losses.sum())),
        expectancy=float(pnl.mean()) if len(pnl) else 0.0,
        avg_trade_return_pct=float(closed["return_pct"].mean()) if len(closed) else 0.0,
        avg_duration_hours=float(closed["duration"].dt.total_seconds().mean() / 3600) if len(closed) else 0.0,
        total_commission=float(tr["commission"].sum()) if len(tr) else 0.0,
        buy_hold_return_pct=100 * (closes[-1] / first_open - 1) if len(closes) else math.nan,
        first_signal=_moment(signals[0]) if signals else None,
        last_signal=_moment(signals[-1]) if signals else None,
    )


def ledger(book: Ledger, decision: Bars, ctx: Context, initial_cash: float) -> pa.Table:
    marks = book.marks
    at = np.fromiter((m.bar for m in marks), dtype=np.int64, count=len(marks))
    features = ctx.features()
    schema = tables.ledger(f.name for f, _ in features)
    equity = np.fromiter((m.equity for m in marks), dtype=np.float64, count=len(marks))
    previous = np.r_[initial_cash, equity[:-1]] if len(marks) else equity
    columns: dict[str, Any] = {
        "time": decision.time_ns[at].astype("datetime64[ns]"),
        "open": decision.open[at],
        "high": decision.high[at],
        "low": decision.low[at],
        "close": decision.close[at],
        "volume": decision.volume[at].astype(np.uint64),
        **{f.name: values[at] for f, values in features},
        "state": [m.state.value for m in marks],
        "trade": [m.trade for m in marks],
        "position": [m.position for m in marks],
        "avg_cost": [m.avg_cost for m in marks],
        "buy_trigger": [m.buy_trigger for m in marks],
        "sell_trigger": [m.sell_trigger for m in marks],
        "stop": [m.stop for m in marks],
        "event": [" | ".join(event(f, ctx.orders[f.order_id - 1]) for f in m.fills) for m in marks],
        "realized_pnl": [m.realized for m in marks],
        "commission": [m.commission for m in marks],
        "unrealized_pnl": [m.unrealized for m in marks],
        "bar_pnl": equity - previous,
        "cumulative_pnl": equity - initial_cash,
        "equity": equity,
    }
    try:
        found = pa.table(
            {n: pa.array(columns[n], type=schema.field(n).type, from_pandas=True) for n in schema.names},
            schema=schema,
        )
    except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
        raise tables.SchemaError(f"ledger does not match its declared schema: {e}") from e
    return tables.check(found, schema)
