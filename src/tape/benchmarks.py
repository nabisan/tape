from __future__ import annotations

import math
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Prices:
    open: float
    close: F64

    def returns(self) -> F64:
        c = self.close
        return np.r_[c[0] / self.open - 1, c[1:] / c[:-1] - 1] if len(c) else c


def prices(store: Store, ticker: Ticker, days: pd.DatetimeIndex) -> Prices:
    bars = store.bars(ticker, Timeframe.D1)
    dates = pd.to_datetime(store.sessions().day[bars.session])
    close = np.asarray(pd.Series(bars.close, index=dates).reindex(days).ffill(), dtype=np.float64)
    first = pd.Series(bars.open, index=dates).reindex(days[:1])
    return Prices(open=float(first.iloc[0]) if len(first) else math.nan, close=close)


def exposure(r: F64, reference: F64) -> Exposure:
    x, y = r, reference
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or float(y.var()) == 0.0:
        return Exposure(beta=math.nan, correlation=math.nan)
    return Exposure(
        beta=float(np.cov(x, y, ddof=1)[0, 1] / y.var(ddof=1)),
        correlation=float(np.corrcoef(x, y)[0, 1]),
    )


def hold(ticker: Ticker, p: Prices, reference: F64) -> Benchmark:
    r = p.returns()
    path = np.r_[p.open, p.close]
    growth = float(path[-1] / path[0])
    vol, cagr, sharpe = annualized(r, growth)
    return Benchmark(
        ticker=ticker,
        total_return_pct=100 * (growth - 1),
        cagr_pct=100 * cagr,
        volatility_pct=100 * vol,
        sharpe=sharpe,
        max_drawdown_pct=float(100 * (path / np.maximum.accumulate(path) - 1).min()),
        exposure=exposure(r, reference),
    )


def compare(store: Store, ticker: Ticker, eq: pd.DataFrame) -> Comparison:
    days = pd.DatetimeIndex(eq["date"])
    names = list(dict.fromkeys([ticker, *MARKET]))
    series = {t: prices(store, t, days) for t in names if t in store.manifest.available}
    ref_r = series[REFERENCE].returns() if REFERENCE in series else np.full(len(days), math.nan)
    return Comparison(
        reference=REFERENCE,
        run=exposure(eq["return"].to_numpy(dtype=float), ref_r),
        benchmarks=[hold(t, p, ref_r) for t, p in series.items()],
    )
