from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from tape.engine.reports import Metrics, capital_loss_pct, time_in_market_pct

FIELDS = {
    "start": "2024-01-02",
    "end": "2024-06-28",
    "sessions": 124,
    "initial_cash": 100_000.0,
    "final_equity": 100_000.0,
    "net_pnl": 0.0,
    "total_return_pct": 0.0,
    "cagr_pct": 0.0,
    "volatility_pct": 0.0,
    "sharpe": math.nan,
    "sortino": math.nan,
    "max_drawdown_pct": 0.0,
    "max_drawdown_sessions": 0,
    "max_capital_loss_pct": 0.0,
    "calmar": math.nan,
    "time_in_market_pct": 0.0,
    "trades": 0,
    "win_rate_pct": math.nan,
    "avg_win": 0.0,
    "avg_loss": 0.0,
    "payoff_ratio": math.nan,
    "profit_factor": math.nan,
    "expectancy": 0.0,
    "avg_trade_return_pct": 0.0,
    "avg_duration_hours": 0.0,
    "total_commission": 0.0,
    "buy_hold_return_pct": 3.5,
}


def test_undefined_measures_are_null_on_the_wire_and_nan_in_python() -> None:
    m = Metrics.model_validate(FIELDS)
    wire = json.loads(m.model_dump_json())
    assert wire["sharpe"] is None
    assert wire["profit_factor"] is None
    assert wire["buy_hold_return_pct"] == 3.5
    back = Metrics.model_validate_json(m.model_dump_json())
    assert math.isnan(back.sharpe)
    assert back.buy_hold_return_pct == 3.5
    assert Metrics.model_validate(wire).trades == 0


def test_max_loss_on_capital_is_the_deepest_fall_below_the_start() -> None:
    assert capital_loss_pct(np.array([100.0, 90.0, 300.0, 120.0]), 100.0) == pytest.approx(-10.0)
    assert capital_loss_pct(np.array([110.0, 300.0, 101.0]), 100.0) == 0.0
    assert capital_loss_pct(np.empty(0), 100.0) == 0.0
    assert math.isnan(capital_loss_pct(np.array([90.0]), math.nan))


def _trades(*spans: tuple[str, str | None]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entry_time": pd.Series([a for a, _ in spans], dtype="datetime64[ns]"),
            "exit_time": pd.Series([b for _, b in spans], dtype="datetime64[ns]"),
            "open": [b is None for _, b in spans],
        }
    )


HOURS = pd.date_range("2024-03-04 09:30", periods=7, freq="h").to_numpy(dtype="datetime64[ns]").view(np.int64)


def test_time_in_market_counts_bars_a_position_was_held_in() -> None:
    inside = _trades(("2024-03-04 10:45", "2024-03-04 13:10"))
    assert time_in_market_pct(HOURS, inside) == pytest.approx(100 * 3 / 7)
    flat_by_close = _trades(("2024-03-04 09:30", "2024-03-04 10:30"))
    assert time_in_market_pct(HOURS, flat_by_close) == pytest.approx(100 * 1 / 7)
    assert time_in_market_pct(HOURS, _trades()) == 0.0
    assert time_in_market_pct(HOURS, _trades(("2024-03-04 14:30", None))) == pytest.approx(100 * 2 / 7)
    assert math.isnan(time_in_market_pct(np.empty(0, dtype=np.int64), _trades()))
