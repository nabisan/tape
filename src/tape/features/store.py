from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from tape.features.kinds import Atr, ChannelHigh, ChannelLow, Ema, Sma, Source

if TYPE_CHECKING:
    from collections.abc import Callable

    from tape.catalog.bars import Bars
    from tape.catalog.store import Store
    from tape.core.types import F64, Ticker, Timeframe
    from tape.features.kinds import FeatureSpec

INDEX: Final = "features/adopted.json"
BAR_COLUMNS: Final = ("time", "open", "high", "low", "close", "volume")


def source_column(spec: FeatureSpec) -> str:
    if isinstance(spec, Sma) and spec.source is Source.VOLUME:
        return f"vol_sma_{spec.window}"
    return spec.name


PARSERS: Final[dict[str, Callable[[int], FeatureSpec]]] = {
    "sma": lambda n: Sma(window=n),
    "ema": lambda n: Ema(window=n),
    "chan_high": lambda n: ChannelHigh(window=n),
    "chan_low": lambda n: ChannelLow(window=n),
    "atr": lambda n: Atr(window=n),
    "vol_sma": lambda n: Sma(window=n, source=Source.VOLUME),
}


def parse_column(name: str) -> FeatureSpec | None:
    kind, _, window = name.rpartition("_")
    make = PARSERS.get(kind)
    return make(int(window)) if make is not None and window.isdigit() else None


class Adopted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    talib: str
    files: dict[str, dict[str, list[str]]]


def part(root: Path, dataset: str, timeframe: Timeframe, ticker: Ticker) -> Path:
    return root / dataset / f"timeframe={timeframe.value}" / f"ticker={ticker}" / "part-0.parquet"


def bars_match(bars: Bars, path: Path) -> bool:
    t = pq.read_table(path, columns=list(BAR_COLUMNS))
    if t.num_rows != len(bars):
        return False
    return bool(
        np.array_equal(t["time"].cast(pa.int64()).to_numpy(), bars.time_ns)
        and np.array_equal(t["open"].to_numpy(), bars.open)
        and np.array_equal(t["high"].to_numpy(), bars.high)
        and np.array_equal(t["low"].to_numpy(), bars.low)
        and np.array_equal(t["close"].to_numpy(), bars.close)
        and np.array_equal(t["volume"].to_numpy(), bars.volume)
    )


class FeatureStore:
    __slots__ = ("_adopted", "_root")

    def __init__(self, store: Store) -> None:
        index = store.root / INDEX
        self._adopted = Adopted.model_validate_json(index.read_bytes()) if index.is_file() else None
        self._root = None if self._adopted is None else Path(self._adopted.source)

    def get(self, ticker: Ticker, timeframe: Timeframe, spec: FeatureSpec) -> F64 | None:
        if self._adopted is None or self._root is None:
            return None
        columns = self._adopted.files.get(timeframe.value, {}).get(ticker)
        name = source_column(spec)
        if columns is None or name not in columns:
            return None
        path = part(self._root, "features", timeframe, ticker)
        return np.asarray(pq.read_table(path, columns=[name])[name].to_numpy(), dtype=np.float64)
