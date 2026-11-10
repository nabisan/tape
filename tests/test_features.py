from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from pydantic import TypeAdapter, ValidationError

from tape.catalog.bars import Bars
from tape.features.kinds import Atr, ChannelHigh, ChannelLow, Ema, FeatureSpec, Sma, Source

ADAPTER: TypeAdapter[FeatureSpec] = TypeAdapter(FeatureSpec)
SPECS: list[FeatureSpec] = [
    Sma(window=20),
    Ema(window=10),
    ChannelHigh(window=5),
    ChannelLow(window=5),
    Atr(window=14),
]


def bars(n: int = 300, seed: int = 0) -> Bars:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    pad = np.abs(rng.normal(0, 0.003, n)) * close
    return Bars(
        time_ns=np.arange(n, dtype=np.int64),
        session=np.zeros(n, dtype=np.int32),
        open=open_,
        high=np.maximum(open_, close) + pad,
        low=np.minimum(open_, close) - pad,
        close=close,
        volume=rng.integers(1, 1000, n).astype(np.uint64),
        count=np.ones(n, dtype=np.uint32),
    )


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_causal(spec: FeatureSpec) -> None:
    b = bars()
    cut = 200
    rng = np.random.default_rng(9)
    shock = np.r_[np.ones(cut), rng.uniform(0.5, 1.5, len(b) - cut)]
    widen = np.r_[np.ones(cut), np.full(len(b) - cut, 1.01)]
    future = replace(
        b, open=b.open * shock, high=b.high * shock * widen, low=b.low * shock / widen, close=b.close * shock
    )
    np.testing.assert_array_equal(spec.compute(b)[:cut], spec.compute(future)[:cut])


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_warmup_is_nan_then_finite(spec: FeatureSpec) -> None:
    v = spec.compute(bars())
    assert bool(np.isnan(v[: spec.warmup()]).all())
    assert bool(np.isfinite(v[spec.warmup() :]).all())


def test_sma_matches_mean() -> None:
    b = bars()
    v = Sma(window=20).compute(b)
    assert v[50] == pytest.approx(b.close[31:51].mean())


def test_channel_excludes_current_bar() -> None:
    b = bars()
    hi, lo = ChannelHigh(window=5).compute(b), ChannelLow(window=5).compute(b)
    assert hi[50] == b.high[45:50].max()
    assert lo[50] == b.low[45:50].min()


def test_spec_round_trips_and_identity() -> None:
    spec = ADAPTER.validate_python({"kind": "sma", "window": 50})
    assert spec == Sma(window=50)
    assert spec.id == Sma(window=50, source=Source.CLOSE).id
    assert spec.id != Sma(window=51).id
    assert spec.id != Ema(window=50).id
    assert Sma(window=50, source=Source.VOLUME).name == "sma_volume_50"


def test_invalid_window_rejected() -> None:
    with pytest.raises(ValidationError):
        Sma(window=1)


def test_nan_input_refused() -> None:
    b = bars()
    close = b.close.copy()
    close[10] = np.nan
    with pytest.raises(AssertionError, match="finite"):
        Sma(window=5).compute(replace(b, close=close))
