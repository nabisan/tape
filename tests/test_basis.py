from __future__ import annotations

from datetime import date

import pandas as pd

from tape.book import BookStats
from tape.engine.reports import Metrics
from tape.fundamentals import Valuation
from tape.lab import report, views
from tape.runner import RunSpec
from tape.service.models import VenueInfo
from tape.venues import Venue, VolumeKind
from tests.test_sweep import sma

FX = VenueInfo(
    venue=Venue.FX,
    label="FX",
    data_version="v",
    zone="UTC",
    periods=260,
    volume=VolumeKind.TICKS,
    first_session=date(2020, 11, 16),
    last_session=date(2026, 11, 17),
)


def run(strategy: str, params: dict[str, object]) -> RunSpec:
    return RunSpec.model_validate(
        {
            "ticker": "USDJPY",
            "start": "2020-11-16",
            "end": "2026-11-17",
            "strategy": strategy,
            "params": params,
        }
    )


def metrics(**over: object) -> Metrics:
    fields: dict[str, object] = dict.fromkeys(Metrics.model_fields, 0.0)
    fields |= {
        "start": date(2020, 11, 16),
        "end": date(2026, 11, 17),
        "sessions": 1_558,
        "initial_cash": 10_000.0,
        "final_equity": 11_691.0,
        "max_drawdown_sessions": 0,
        "trades": 0,
        "first_signal": None,
        "last_signal": None,
    }
    return Metrics.model_validate(fields | over)


def test_performance_shows_its_span_in_trades_and_its_base_in_returns() -> None:
    m = metrics()
    found = report.performance(
        {"A": ("k", m)},
        Valuation(ticker="USDJPY", start=None, end=None),
        frozenset(),
        frozenset(),
        venue=FX,
        specs={"A": run("sma_cross", {"timeframe": "1h", "fast": 20, "slow": 50})},
    )
    assert "Sample" not in found and "risk-free" not in found
    returns = found.split("<h5>Returns</h5>")[1].split("</table>")[0]
    for row in ("Capital $", "Final equity $", "Years", "CAGR %"):
        assert row in returns
    trades = found.split("<h5>Trades</h5>")[1].split("</table>")[0]
    assert "<td>Start</td><td>16 Nov 2020</td>" in trades
    assert "<td>End</td><td>17 Nov 2026</td>" in trades
    assert "<td>Sessions</td><td>1,558</td>" in trades
    assert "<h5>Costs</h5>" not in found
    risk = found.split("<h5>Risk</h5>")[1].split("</table>")[0]
    assert "<td>Stop-loss %</td><td>&ndash;</td>" in risk


def test_a_default_stop_loss_is_shown_in_risk_and_the_simulation_header() -> None:
    found = report.performance(
        {"A": ("k", metrics())},
        Valuation(ticker="USDJPY", start=None, end=None),
        frozenset(),
        frozenset(),
        venue=FX,
        specs={"A": run("high_breakout", {"timeframe": "1h"})},
    )
    assert "<td>Stop-loss %</td><td>0.25</td>" in found
    breakout = sma().model_copy(update={"strategy": "high_breakout", "grid": {}, "params": {}})
    frame = pd.DataFrame(
        {
            "ticker": ["EURUSD"],
            "key": ["k"],
            "total_return_pct": [1.0],
            "sharpe": [0.1],
            "max_drawdown_pct": [-1.0],
        }
    )
    head = views.header("k" * 16, breakout, frame, "High-Breakout", folio=None, source="")
    assert "stop loss 0.25%" in head


def test_the_book_shows_its_period_years_and_final_value() -> None:
    stats = BookStats(
        start=date(2020, 11, 16),
        end=date(2026, 11, 17),
        sessions=1_560,
        capital=50_000.0,
        final_equity=57_295.0,
        total_return_pct=14.59,
        cagr_pct=2.30,
        volatility_pct=4.0,
        sharpe=0.5,
        max_drawdown_pct=-7.18,
        trough=date(2026, 5, 25),
        max_drawdown_sessions=480,
        max_capital_loss_pct=-0.21,
        drawdown_now_pct=-5.14,
    )
    found = views.book_stats(stats, 260)
    assert "<th>Period</th><th>Years</th><th>Capital</th><th>Final value</th>" in found
    assert "<td>16 Nov 2020 &ndash; 17 Nov 2026</td><td>6.00</td><td>$50,000</td><td>$57,295.00</td>" in found
    assert "risk-free" not in found
