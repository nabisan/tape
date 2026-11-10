from __future__ import annotations

import json
import re
from importlib import metadata
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.features.adopt import AdoptionError, adopt, part
from tape.features.catalog import FeatureCatalog, Origin
from tape.features.kinds import ChannelHigh, FeatureSpec, Sma, Source
from tape.runner import FeaturePolicy, MissingFeaturesError, RunSpec, execute
from tape.strategies.channel_breakout import ChannelBreakout
from tape.strategies.sma_cross import SmaCross
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from pathlib import Path

SPECS: list[FeatureSpec] = [
    Sma(window=4),
    Sma(window=12),
    ChannelHigh(window=10),
    Sma(window=20, source=Source.VOLUME),
]
TIMEFRAMES = [Timeframe.H1, Timeframe.D1]
AAPL, SPY = Ticker("AAPL"), Ticker("SPY")


def source_column(spec: FeatureSpec) -> str:
    return f"vol_sma_{spec.window}" if isinstance(spec, Sma) and spec.source is Source.VOLUME else spec.name


def precompute(store: Store, root: Path, talib: str | None = None) -> Path:
    for tf in TIMEFRAMES:
        for t in (AAPL, SPY):
            b = store.bars(t, tf)
            bars = pa.table(
                {
                    "time": pa.array(b.time_ns.view("datetime64[ns]")),
                    "session": pa.array(b.session),
                    "open": pa.array(b.open),
                    "high": pa.array(b.high),
                    "low": pa.array(b.low),
                    "close": pa.array(b.close),
                    "volume": pa.array(b.volume),
                    "count": pa.array(b.count.astype(np.uint64)),
                }
            )
            feats = pa.table(
                {
                    "time": pa.array(b.time_ns.view("datetime64[ns]")),
                    **{source_column(s): pa.array(s.compute(b)) for s in SPECS},
                }
            )
            for dataset, table in (("bars", bars), ("features", feats)):
                path = part(root, dataset, tf, t)
                path.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(table, path)
    (root / "manifest.json").write_text(json.dumps({"talib": talib or metadata.version("ta-lib")}))
    return root


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return make_store(tmp_path / "base")


def test_matching_files_are_registered_in_the_catalog(store: Store, tmp_path: Path) -> None:
    src = precompute(store, tmp_path / "catalog")
    registered = adopt(tmp_path / "base" / "store", src, audit_every=1)
    assert registered == len(SPECS) * len(TIMEFRAMES) * 2
    catalog = FeatureCatalog(Store.open(tmp_path / "base" / "store"))
    assert catalog.timeframes(AAPL) == [Timeframe.H1, Timeframe.D1]
    assert sorted(s.id for s in catalog.specs(AAPL, Timeframe.H1)) == sorted(s.id for s in SPECS)
    bars = store.bars(AAPL, Timeframe.H1)
    for spec in SPECS:
        entry = catalog.lookup(AAPL, Timeframe.H1, spec)
        assert entry is not None
        assert (entry.origin, entry.rows, entry.column) == (Origin.ADOPTED, len(bars), source_column(spec))
        np.testing.assert_array_equal(catalog.values(entry), spec.compute(bars))
    assert catalog.lookup(AAPL, Timeframe.H1, Sma(window=200)) is None


def test_adopting_twice_replaces_entries(store: Store, tmp_path: Path) -> None:
    src = precompute(store, tmp_path / "catalog")
    adopt(tmp_path / "base" / "store", src)
    adopt(tmp_path / "base" / "store", src)
    assert len(FeatureCatalog(Store.open(tmp_path / "base" / "store"))) == len(SPECS) * len(TIMEFRAMES) * 2


def test_entries_record_session_range(store: Store, tmp_path: Path) -> None:
    adopt(tmp_path / "base" / "store", precompute(store, tmp_path / "catalog"))
    catalog = FeatureCatalog(Store.open(tmp_path / "base" / "store"))
    [entry] = [e for e in catalog.entries(ticker=SPY, timeframe=Timeframe.D1) if e.spec == SPECS[0]]
    days = store.sessions().day
    assert (entry.first_session, entry.last_session) == (days[0].item(), days[-1].item())


def test_bars_that_differ_are_not_adopted(store: Store, tmp_path: Path) -> None:
    src = precompute(store, tmp_path / "catalog")
    path = part(src, "bars", Timeframe.H1, AAPL)
    table = pq.read_table(path)
    close = table["close"].to_numpy().copy()
    close[5] += 0.01
    pq.write_table(table.set_column(table.schema.get_field_index("close"), "close", pa.array(close)), path)
    adopt(tmp_path / "base" / "store", src)
    catalog = FeatureCatalog(Store.open(tmp_path / "base" / "store"))
    assert catalog.timeframes(AAPL) == [Timeframe.D1]
    assert catalog.timeframes(SPY) == [Timeframe.H1, Timeframe.D1]


def test_audit_catches_wrong_values(store: Store, tmp_path: Path) -> None:
    src = precompute(store, tmp_path / "catalog")
    path = part(src, "features", Timeframe.D1, SPY)
    table = pq.read_table(path)
    values = table["sma_4"].to_numpy().copy()
    values[-1] *= 1.001
    pq.write_table(table.set_column(table.schema.get_field_index("sma_4"), "sma_4", pa.array(values)), path)
    with pytest.raises(AdoptionError, match="sma_4: precomputed values differ"):
        adopt(tmp_path / "base" / "store", src, audit_every=1)


def test_talib_version_must_match(store: Store, tmp_path: Path) -> None:
    src = precompute(store, tmp_path / "catalog", talib="0.0.1")
    with pytest.raises(AdoptionError, match=re.escape("TA-Lib 0.0.1")):
        adopt(tmp_path / "base" / "store", src)


def test_runs_on_adopted_features_match_computed(store: Store, tmp_path: Path) -> None:
    spec = RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-02-01",
            "end": "2024-06-28",
            "strategy": "sma_cross",
            "params": {"timeframe": "1h", "fast": 4, "slow": 12},
        }
    )
    computed = execute(store, spec.model_copy(update={"features": FeaturePolicy.COMPUTE}))
    adopt(tmp_path / "base" / "store", precompute(store, tmp_path / "catalog"))
    stored = execute(Store.open(tmp_path / "base" / "store"), spec)
    assert len(computed.fills) > 0
    assert computed.fills.equals(stored.fills)
    assert computed.metrics == stored.metrics


def test_space_resolves_from_the_catalog(store: Store, tmp_path: Path) -> None:
    adopt(tmp_path / "base" / "store", precompute(store, tmp_path / "catalog"))
    catalog = FeatureCatalog(Store.open(tmp_path / "base" / "store"))
    sma = SmaCross.parameters.resolve(catalog, AAPL)
    assert sma.timeframes == [Timeframe.H1, Timeframe.D1]
    assert sma.choices[Timeframe.D1] == {"fast": [4, 12], "slow": [4, 12]}
    channel = ChannelBreakout.parameters.resolve(catalog, AAPL)
    assert channel.timeframes == []


def test_precomputed_policy_refuses_missing(store: Store, tmp_path: Path) -> None:
    adopt(tmp_path / "base" / "store", precompute(store, tmp_path / "catalog"))
    spec = RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-02-01",
            "end": "2024-06-28",
            "strategy": "sma_cross",
            "params": {"timeframe": "1h", "fast": 4, "slow": 50},
        }
    )
    with pytest.raises(MissingFeaturesError, match="not precomputed: sma_50"):
        execute(Store.open(tmp_path / "base" / "store"), spec)
