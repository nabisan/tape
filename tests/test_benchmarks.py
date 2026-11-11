from __future__ import annotations

import math

import numpy as np
import pytest

from tape.benchmarks import exposure, hold
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


def test_hold_return_and_drawdown() -> None:
    close = np.array([100.0, 120.0, 90.0, 150.0])
    b = hold(Ticker("X"), close, np.r_[0.0, close[1:] / close[:-1] - 1])
    assert b.total_return_pct == pytest.approx(50.0)
    assert b.max_drawdown_pct == pytest.approx(-25.0)
    assert b.exposure.beta == pytest.approx(1.0)
