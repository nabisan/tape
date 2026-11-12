from __future__ import annotations

import math

import numpy as np
import pytest

from tape.benchmarks import Prices, exposure, hold
from tape.core.types import Ticker


def test_exposure_of_reference_is_one() -> None:
    rng = np.random.default_rng(7)
    r = np.r_[0.0, rng.normal(0, 0.01, 500)]
    e = exposure(r, r)
    assert e.beta == pytest.approx(1.0)
    assert e.correlation == pytest.approx(1.0)


def test_exposure_scales_with_leverage() -> None:
    rng = np.random.default_rng(11)
    ref = np.r_[0.0, rng.normal(0, 0.01, 500)]
    e = exposure(2 * ref, ref)
    assert e.beta == pytest.approx(2.0)
    assert e.correlation == pytest.approx(1.0)


def test_exposure_needs_data() -> None:
    e = exposure(np.zeros(2), np.zeros(2))
    assert math.isnan(e.beta)
    assert math.isnan(e.correlation)


def test_hold_buys_the_first_open_and_sells_the_last_close() -> None:
    p = Prices(open=80.0, close=np.array([100.0, 120.0, 90.0, 150.0]))
    r = p.returns()
    assert r == pytest.approx([0.25, 0.2, -0.25, 150 / 90 - 1])
    b = hold(Ticker("X"), p, r)
    assert b.total_return_pct == pytest.approx(87.5)
    assert b.max_drawdown_pct == pytest.approx(-25.0)
    assert b.exposure.beta == pytest.approx(1.0)
    assert b.cagr_pct == pytest.approx(100 * (1.875 ** (252 / 4) - 1))


def test_hold_drawdown_counts_a_first_day_drop_from_the_open() -> None:
    b = hold(Ticker("X"), Prices(open=100.0, close=np.array([90.0, 95.0])), np.zeros(2))
    assert b.max_drawdown_pct == pytest.approx(-10.0)
