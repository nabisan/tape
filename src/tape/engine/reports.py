from __future__ import annotations

import math
from typing import TYPE_CHECKING, Annotated

import numpy as np
import pandas as pd
from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

if TYPE_CHECKING:
    from tape.calendar import Sessions
    from tape.catalog.bars import Bars
    from tape.engine.backtest import Run

TRADING_DAYS = 252


def _ts(ns: int | None) -> pd.Timestamp | None:
    return None if ns is None else pd.Timestamp(ns, unit="ns")


def orders(run: Run) -> pd.DataFrame:
    return pd.DataFrame(
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
                "reason": o.reason,
            }
            for o in run.orders
        ]
    )


def fills(run: Run) -> pd.DataFrame:
    return pd.DataFrame(
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
        ]
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
    return pd.DataFrame(rows)


def equity(run: Run, sessions: Sessions, initial_cash: float) -> pd.DataFrame:
    marks = run.sessions
    eq = pd.Series(marks.equity, dtype=float)
    peak = np.maximum.accumulate(np.r_[initial_cash, marks.equity])[1:]
    return pd.DataFrame(
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


def _from_wire(v: object) -> object:
    return math.nan if v is None else v


def _to_wire(v: float) -> float | None:
    return None if math.isnan(v) else v


type Measure = Annotated[float, BeforeValidator(_from_wire), PlainSerializer(_to_wire, when_used="json")]


class Metrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: str
    end: str
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
    calmar: Measure
    exposure_pct: Measure
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


def _ratio(a: float, b: float) -> float:
    return a / b if b else math.nan


def metrics(eq: pd.DataFrame, tr: pd.DataFrame, initial_cash: float) -> Metrics:
    r = eq["return"].to_numpy(dtype=float)
    values = eq["equity"].to_numpy(dtype=float)
    final = float(values[-1]) if len(values) else initial_cash
    years = len(r) / TRADING_DAYS
    downside = r[r < 0]
    under = eq["drawdown"].to_numpy() < 0
    longest = run_length = 0
    for flag in under:
        run_length = run_length + 1 if flag else 0
        longest = max(longest, run_length)
    closed = tr[~tr["open"]] if len(tr) else tr
    pnl = closed["net_pnl"].to_numpy(dtype=float) if len(closed) else np.empty(0)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    vol = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(r) > 1 else math.nan
    cagr = (final / initial_cash) ** (1 / years) - 1 if years > 0 and final > 0 else math.nan
    max_dd = float(eq["drawdown_pct"].min()) if len(eq) else 0.0
    closes = eq["close"].to_numpy(dtype=float)
    return Metrics(
        start=str(eq["date"].iloc[0].date()) if len(eq) else "",
        end=str(eq["date"].iloc[-1].date()) if len(eq) else "",
        sessions=len(eq),
        initial_cash=initial_cash,
        final_equity=final,
        net_pnl=final - initial_cash,
        total_return_pct=100 * (final / initial_cash - 1),
        cagr_pct=100 * cagr,
        volatility_pct=100 * vol,
        sharpe=_ratio(float(r.mean()) * TRADING_DAYS, vol) if len(r) > 1 else math.nan,
        sortino=_ratio(
            float(r.mean()) * TRADING_DAYS,
            float(np.sqrt((downside**2).sum() / len(r)) * math.sqrt(TRADING_DAYS)),
        )
        if len(r)
        else math.nan,
        max_drawdown_pct=max_dd,
        max_drawdown_sessions=longest,
        calmar=_ratio(100 * cagr, -max_dd),
        exposure_pct=100 * float((eq["position"] != 0).mean()) if len(eq) else 0.0,
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
        buy_hold_return_pct=100 * (closes[-1] / closes[0] - 1) if len(closes) else math.nan,
    )
