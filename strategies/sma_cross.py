import numpy as np
import pandas as pd

from .base import ALL_DIRECTIONS, Param, Strategy


class SmaCrossover(Strategy):
    """Long when the fast SMA crosses above the slow SMA, short when it crosses below."""

    name = "SMA Crossover"
    params = {
        "fast": Param("Fast SMA", 9, 2, 500),
        "slow": Param("Slow SMA", 21, 3, 1000),
    }
    directions = ALL_DIRECTIONS
    supports_intraday = True

    def validate(self, p):
        if p["fast"] >= p["slow"]:
            return "Fast SMA must be shorter than Slow SMA"
        return None

    def generate(self, df: pd.DataFrame, fast: int, slow: int):
        close = df["close"]
        sma_f = close.rolling(int(fast)).mean()
        sma_s = close.rolling(int(slow)).mean()
        above = np.sign(sma_f - sma_s)
        prev = above.shift(1)
        signal = pd.Series(0, index=df.index, dtype="int8")
        signal[(above > 0) & (prev <= 0)] = 1
        signal[(above < 0) & (prev >= 0)] = -1
        # No signals until both SMAs have a full window (comparisons with NaN are False anyway).
        signal.iloc[: int(slow)] = 0
        return signal, {f"SMA {int(fast)}": sma_f, f"SMA {int(slow)}": sma_s}
