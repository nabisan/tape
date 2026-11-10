from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from tape.catalog.source import columns
from tape.core.types import Ticker, Timeframe
from tape.features.kinds import FeatureId, FeatureKind, FeatureSpec

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable, Mapping
    from datetime import date

    from tape.catalog.store import Store
    from tape.core.types import F64

CATALOG: Final = "features/catalog.parquet"
SPEC: Final[TypeAdapter[FeatureSpec]] = TypeAdapter(FeatureSpec)
SCHEMA: Final = columns(
    feature_id=pa.string(),
    kind=pa.string(),
    spec=pa.string(),
    kind_version=pa.int32(),
    timeframe=pa.string(),
    ticker=pa.string(),
    rows=pa.int64(),
    first_session=pa.date32(),
    last_session=pa.date32(),
    origin=pa.string(),
    path=pa.string(),
    column=pa.string(),
)
KEY: Final = ["feature_id", "timeframe", "ticker"]


class Origin(StrEnum):
    BUILT = "built"
    ADOPTED = "adopted"


@dataclass(frozen=True, slots=True)
class Entry:
    spec: FeatureSpec
    timeframe: Timeframe
    ticker: Ticker
    rows: int
    first_session: date
    last_session: date
    origin: Origin
    path: str
    column: str

    def row(self) -> dict[str, object]:
        return {
            "feature_id": self.spec.id,
            "kind": self.spec.kind.value,
            "spec": SPEC.dump_json(self.spec).decode(),
            "kind_version": self.spec.version,
            "timeframe": self.timeframe.value,
            "ticker": self.ticker,
            "rows": self.rows,
            "first_session": self.first_session,
            "last_session": self.last_session,
            "origin": self.origin.value,
            "path": self.path,
            "column": self.column,
        }


def _entry(r: Mapping[Hashable, object]) -> Entry:
    return Entry(
        spec=SPEC.validate_json(str(r["spec"])),
        timeframe=Timeframe(str(r["timeframe"])),
        ticker=Ticker(str(r["ticker"])),
        rows=int(str(r["rows"])),
        first_session=pd.Timestamp(str(r["first_session"])).date(),
        last_session=pd.Timestamp(str(r["last_session"])).date(),
        origin=Origin(str(r["origin"])),
        path=str(r["path"]),
        column=str(r["column"]),
    )


class FeatureCatalog:
    __slots__ = ("_frame", "path")

    def __init__(self, store: Store) -> None:
        self.path = store.root / CATALOG
        self._frame = (
            pq.read_table(self.path).to_pandas() if self.path.is_file() else SCHEMA.empty_table().to_pandas()
        )

    def __len__(self) -> int:
        return len(self._frame)

    def entries(
        self,
        ticker: Ticker | None = None,
        timeframe: Timeframe | None = None,
        kind: FeatureKind | None = None,
    ) -> list[Entry]:
        f = self._frame
        if ticker is not None:
            f = f[f["ticker"] == ticker]
        if timeframe is not None:
            f = f[f["timeframe"] == timeframe.value]
        if kind is not None:
            f = f[f["kind"] == kind.value]
        return [_entry(r) for r in f.to_dict("records")]

    def lookup(self, ticker: Ticker, timeframe: Timeframe, spec: FeatureSpec) -> Entry | None:
        f = self._frame
        hit = f[(f["feature_id"] == spec.id) & (f["ticker"] == ticker) & (f["timeframe"] == timeframe.value)]
        return _entry(hit.to_dict("records")[0]) if len(hit) else None

    def timeframes(self, ticker: Ticker) -> list[Timeframe]:
        present = set(self._frame.loc[self._frame["ticker"] == ticker, "timeframe"])
        return [tf for tf in Timeframe if tf.value in present]

    def specs(self, ticker: Ticker, timeframe: Timeframe) -> list[FeatureSpec]:
        f = self._frame
        rows = f[(f["ticker"] == ticker) & (f["timeframe"] == timeframe.value)]
        return [SPEC.validate_json(s) for s in rows["spec"]]

    def ids(self, ticker: Ticker, timeframe: Timeframe) -> set[FeatureId]:
        f = self._frame
        return {
            FeatureId(i)
            for i in f.loc[(f["ticker"] == ticker) & (f["timeframe"] == timeframe.value), "feature_id"]
        }

    def values(self, entry: Entry) -> F64:
        table = pq.read_table(entry.path, columns=[entry.column], memory_map=True)
        return np.asarray(table[entry.column].to_numpy(), dtype=np.float64)

    def register(self, entries: Iterable[Entry]) -> int:
        new = pd.DataFrame([e.row() for e in entries], columns=SCHEMA.names)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = pq.read_table(self.path).to_pandas() if self.path.is_file() else self._frame.iloc[0:0]
            merged = pd.concat([current, new], ignore_index=True).drop_duplicates(KEY, keep="last")
            merged = merged.sort_values(["ticker", "timeframe", "kind", "feature_id"], ignore_index=True)
            table = pa.Table.from_pandas(merged, schema=SCHEMA, preserve_index=False)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            pq.write_table(table, tmp, compression="zstd")
            tmp.replace(self.path)
        self._frame = merged
        return len(new)
