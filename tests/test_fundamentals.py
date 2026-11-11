from __future__ import annotations

import math
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.fundamentals import SCHEMA, Fundamentals, FundamentalsError, load, plausible, save

if TYPE_CHECKING:
    from pathlib import Path

START = date(2018, 5, 1)


def _source(path: Path, rows: list[tuple[str, date, float, float, float]]) -> Path:
    t = pa.table(
        {
            "date": pa.array([d.strftime("%d-%m-%Y") for _, d, *_ in rows], pa.large_string()),
            "ticker": pa.array([r[0] for r in rows]).dictionary_encode(),
            "close": pa.array([10.0] * len(rows)),
            "shares": pa.array([r[2] for r in rows], pa.float64()),
            "market_cap": pa.array([r[2] * 10.0 for r in rows], pa.float64()),
            "ttm_net_income": pa.array([r[3] for r in rows], pa.float64()),
            "ni_period_end": pa.array([d.strftime("%d-%m-%Y") for _, d, *_ in rows], pa.large_string()),
            "pe_ratio": pa.array([r[4] for r in rows], pa.float64()),
        }
    )
    pq.write_table(t, path)
    return path


def _days(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def test_plausible_masks_isolated_unit_errors_and_keeps_gradual_change() -> None:
    shares = np.array([100e6] * 5 + [3.6e14] * 3 + [101e6] * 5 + [100.0] * 2 + [102e6] * 5 + [140e6] * 5)
    ok = plausible(shares)
    assert ok[:5].all() and ok[8:13].all() and ok[15:].all()
    assert not ok[5:8].any() and not ok[13:15].any()


def test_plausible_handles_missing_and_single_runs() -> None:
    assert plausible(np.array([np.nan, np.nan])).all()
    assert plausible(np.array([5e8] * 4)).all()
    assert plausible(np.array([], dtype=np.float64)).shape == (0,)


def test_load_types_sorts_and_flags(tmp_path: Path) -> None:
    days = _days(12)
    rows = [("ZZ", d, 50e6, 1e6, 500.0) for d in days]
    rows += [("AA", d, 3.6e14 if 4 <= i < 6 else 2e9, -5e6, math.nan) for i, d in enumerate(days)]
    t = load(_source(tmp_path / "src.parquet", rows))
    assert t.schema.equals(SCHEMA)
    assert t["ticker"].to_pylist()[:12] == ["AA"] * 12
    assert t["day"].to_pylist()[:3] == days[:3]
    assert t["plausible"].to_pylist()[:12] == [i not in (4, 5) for i in range(12)]
    assert all(t["plausible"].to_pylist()[12:])


def test_load_rejects_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"ticker": ["A"], "date": ["01-05-2018"]}), path)
    with pytest.raises(FundamentalsError, match="lacks columns"):
        load(path)


def test_zero_rows_round_trip(tmp_path: Path) -> None:
    t = load(_source(tmp_path / "empty.parquet", []))
    assert t.num_rows == 0 and t.schema.equals(SCHEMA)
    save(t, tmp_path / "store")
    found = Fundamentals.open(tmp_path / "store")
    assert found is not None
    v = found.valuation("AA", START, START)
    assert v.start is None and v.end is None


def test_valuation_as_of_loss_stale_and_masked(tmp_path: Path) -> None:
    days = _days(30)
    rows = [("GOOD", d, 1e9 + i, 2e8, 50.0 + i) for i, d in enumerate(days)]
    rows += [("LOSS", d, 1e9, -3e8, math.nan) for d in days]
    rows += [("BAD", d, 3.6e14 if i >= 27 else 1e9, 1e8, 99.0) for i, d in enumerate(days)]
    save(load(_source(tmp_path / "src.parquet", rows)), tmp_path)
    f = Fundamentals.open(tmp_path)
    assert f is not None

    v = f.valuation("GOOD", START - timedelta(days=3), days[10])
    assert v.start is None
    assert v.end is not None and v.end.day == days[10] and v.end.pe == 60.0
    assert v.end.market_cap == pytest.approx((1e9 + 10) * 10)

    late = f.valuation("GOOD", days[0], days[-1] + timedelta(days=30))
    assert late.end is None

    loss = f.valuation("LOSS", days[0], days[-1]).end
    assert loss is not None and math.isnan(loss.pe) and loss.net_income < 0

    bad = f.valuation("BAD", days[0], days[-1])
    assert bad.start is not None and bad.start.plausible
    assert bad.end is not None and not bad.end.plausible and math.isnan(bad.end.market_cap)

    assert f.valuation("NONE", days[0], days[-1]) == f.valuation("NONE", days[0], days[-1])
    assert f.valuation("NONE", days[0], days[-1]).end is None


def test_open_without_file(tmp_path: Path) -> None:
    assert Fundamentals.open(tmp_path) is None
