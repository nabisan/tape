from __future__ import annotations

import json
import math

from tape.engine.reports import Metrics

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
    "calmar": math.nan,
    "exposure_pct": 0.0,
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
