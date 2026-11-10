from __future__ import annotations

import numpy as np
import pandas as pd


def annual_vol_pct(values: pd.Series) -> float:
    """Annualized volatility (%) of a daily price or equity series: stdev of daily returns x sqrt(252)."""
    r = values.dropna().pct_change().dropna()
    return 100 * np.sqrt(252) * r.std() if len(r) > 1 else np.nan


def buy_hold_pct(daily_bars: pd.DataFrame) -> float:
    """Return (%) from buying at the first day's open and holding to the last day's close."""
    if daily_bars is None or daily_bars.empty:
        return np.nan
    return 100 * (daily_bars["close"].iloc[-1] / daily_bars["open"].iloc[0] - 1)


def summarize(daily_equity: pd.Series, trades: pd.DataFrame, initial_capital: float) -> dict:
    eq = daily_equity.dropna()
    out: dict = {}
    if eq.empty:
        return out
    net = eq.iloc[-1] - initial_capital
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    rets = eq.pct_change().dropna()
    peak = eq.cummax()
    dd = (eq - peak) / peak

    out["Net P&L"] = net
    out["Return %"] = 100 * net / initial_capital
    out["CAGR %"] = 100 * ((eq.iloc[-1] / initial_capital) ** (1 / years) - 1) if eq.iloc[-1] > 0 else np.nan
    out["Max DD"] = (eq - peak).min()
    out["Max DD %"] = 100 * dd.min()
    std = rets.std()
    out["Sharpe"] = np.sqrt(252) * rets.mean() / std if std > 0 else np.nan
    out["Annualized Volatility %"] = annual_vol_pct(eq)

    closed = trades[trades["Exit Reason"] != "Open (MTM)"] if len(trades) else trades
    n = len(closed)
    out["Trades"] = n
    if n:
        pnl = closed["Net P&L"]
        # Per-trade return in % of the position's entry value, after costs.
        ret = 100 * pnl / (closed["Qty"] * closed["Entry Price"])
        win, loss = pnl > 0, pnl <= 0
        out["Win %"] = 100 * win.sum() / n
        out["Avg Trade"] = pnl.mean()
        out["Avg Win %"] = ret[win].mean() if win.any() else 0.0
        out["Avg Loss %"] = ret[loss].mean() if loss.any() else 0.0
        out["Largest Win %"] = ret[win].max() if win.any() else 0.0
        out["Largest Loss %"] = ret[loss].min() if loss.any() else 0.0
        out["Total Costs"] = closed["Costs"].sum()
        out["Avg Bars Held"] = closed["Bars Held"].mean()
    out["Profitable Days %"] = 100 * (eq.diff().dropna() > 0).mean() if len(eq) > 1 else np.nan
    return out
