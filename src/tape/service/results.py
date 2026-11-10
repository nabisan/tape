from __future__ import annotations

import os
import shutil
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd

from tape.engine.reports import Metrics
from tape.runner import RunSpec

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

    from tape.core.hashing import Digest
    from tape.runner import Result


class Table(StrEnum):
    EQUITY = "equity"
    TRADES = "trades"
    ORDERS = "orders"
    FILLS = "fills"
    BARS = "bars"


class ResultStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def exists(self, key: Digest) -> bool:
        return (self.root / key / "metrics.json").is_file()

    def save(self, result: Result) -> None:
        final = self.root / result.key
        if final.exists():
            return
        tmp = self.root / f".{result.key}.{os.getpid()}.tmp"
        tmp.mkdir()
        try:
            frames = {
                Table.EQUITY: result.equity,
                Table.TRADES: result.trades,
                Table.ORDERS: result.orders,
                Table.FILLS: result.fills,
                Table.BARS: result.bars,
            }
            for table, frame in frames.items():
                frame.to_parquet(tmp / f"{table}.parquet", index=False, compression="zstd")
            (tmp / "spec.json").write_text(result.spec.model_dump_json(indent=2))
            (tmp / "metrics.json").write_text(result.metrics.model_dump_json(indent=2))
            tmp.rename(final)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            if not final.exists():
                raise

    def metrics(self, key: Digest) -> Metrics:
        return Metrics.model_validate_json((self.root / key / "metrics.json").read_bytes())

    def spec(self, key: Digest) -> RunSpec:
        return RunSpec.model_validate_json((self.root / key / "spec.json").read_bytes())

    def table(self, key: Digest, table: Table) -> bytes:
        return (self.root / key / f"{table}.parquet").read_bytes()

    def sweep_summary(self, key: Digest) -> Path:
        return self.root / "sweeps" / f"{key}.parquet"

    def save_sweep(self, key: Digest, spec: BaseModel, frame: pd.DataFrame) -> None:
        path = self.sweep_summary(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        frame.to_parquet(tmp, index=False, compression="zstd")
        path.with_suffix(".json").write_text(spec.model_dump_json(indent=2))
        tmp.replace(path)

    def frame(self, key: Digest, table: Table) -> pd.DataFrame:
        return pd.read_parquet(self.root / key / f"{table}.parquet")
