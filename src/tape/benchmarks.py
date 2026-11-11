from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, ConfigDict

from tape.core.types import Ticker, Timeframe
from tape.engine.reports import Measure, annualized

if TYPE_CHECKING:
    from tape.catalog.store import Store

REFERENCE: Final = Ticker("QQQ")
MARKET: Final = (Ticker("QQQ"), Ticker("SPY"))

type F64 = npt.NDArray[np.float64]


class Exposure(BaseModel):
    model_config = ConfigDict(frozen=True)

    beta: Measure
    correlation: Measure


class Benchmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    total_return_pct: Measure
    cagr_pct: Measure
    volatility_pct: Measure
    sharpe: Measure
    max_drawdown_pct: Measure
    exposure: Exposure


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: str
    run: Exposure
    benchmarks: list[Benchmark]


def closes(store: Store, ticker: Ticker, days: pd.DatetimeIndex) -> F64:
    bars = store.bars(ticker, Timeframe.D1)
    dates = pd.to_datetime(store.sessions().day[bars.session])
    return np.asarray(pd.Series(bars.close, index=dates).reindex(days).ffill(), dtype=np.float64)


def exposure(r: F64, reference: F64) -> Exposure:
    x, y = r[1:], reference[1:]
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or float(y.var()) == 0.0:
        return Exposure(beta=math.nan, correlation=math.nan)
    return Exposure(
        beta=float(np.cov(x, y, ddof=1)[0, 1] / y.var(ddof=1)),
        correlation=float(np.corrcoef(x, y)[0, 1]),
    )


def hold(ticker: Ticker, close: F64, reference: F64) -> Benchmark:
    r = np.r_[0.0, close[1:] / close[:-1] - 1]
    growth = float(close[-1] / close[0])
    vol, cagr, sharpe = annualized(r, growth)
    return Benchmark(
        ticker=ticker,
        total_return_pct=100 * (growth - 1),
        cagr_pct=100 * cagr,
        volatility_pct=100 * vol,
        sharpe=sharpe,
        max_drawdown_pct=float(100 * (close / np.maximum.accumulate(close) - 1).min()),
        exposure=exposure(r, reference),
    )


def compare(store: Store, ticker: Ticker, eq: pd.DataFrame) -> Comparison:
    days = pd.DatetimeIndex(eq["date"])
    names = list(dict.fromkeys([ticker, *MARKET]))
    series = {t: closes(store, t, days) for t in names if t in store.manifest.available}
    ref = series[REFERENCE]
    ref_r = np.r_[0.0, ref[1:] / ref[:-1] - 1]
    return Comparison(
        reference=REFERENCE,
        run=exposure(eq["return"].to_numpy(dtype=float), ref_r),
        benchmarks=[hold(t, c, ref_r) for t, c in series.items()],
    )
