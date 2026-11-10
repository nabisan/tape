from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import talib

if TYPE_CHECKING:
    from tape.core.types import F64


def _input(x: F64) -> F64:
    a = np.ascontiguousarray(x, dtype=np.float64)
    assert bool(np.isfinite(a).all()), "TA-Lib input must be finite"
    return a


def sma(x: F64, n: int) -> F64:
    return np.asarray(talib.SMA(_input(x), timeperiod=n), dtype=np.float64)


def ema(x: F64, n: int) -> F64:
    return np.asarray(talib.EMA(_input(x), timeperiod=n), dtype=np.float64)


def rolling_max(x: F64, n: int) -> F64:
    return np.asarray(talib.MAX(_input(x), timeperiod=n), dtype=np.float64)


def rolling_min(x: F64, n: int) -> F64:
    return np.asarray(talib.MIN(_input(x), timeperiod=n), dtype=np.float64)


def atr(high: F64, low: F64, close: F64, n: int) -> F64:
    return np.asarray(talib.ATR(_input(high), _input(low), _input(close), timeperiod=n), dtype=np.float64)
