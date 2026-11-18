from __future__ import annotations

from datetime import date

import pandas as pd

from tape.book import BookStats
from tape.engine.reports import Metrics
from tape.fundamentals import Valuation
from tape.lab import report, views
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


def test_performance_states_its_sample_capital_years_and_conventions() -> None:
    m = metrics()
    found = report.performance(
        {"A": ("k", m)}, Valuation(ticker="USDJPY", start=None, end=None), frozenset(), frozenset(), FX
    )
    assert "Sample 16 Nov 2020 &ndash; 17 Nov 2026 &middot; 1,558 sessions &middot; 5.99 years" in found
    assert "$10,000 &rarr; $11,691.00" in found
    assert "annualised on 260 sessions a year" in found and "risk-free 0%" in found
    returns = found.split("<h5>Returns</h5>")[1].split("</table>")[0]
    for row in ("Capital $", "Final equity $", "Years", "CAGR %"):
        assert row in returns
    assert "<h5>Costs</h5>" not in found


def test_the_benchmark_note_names_what_hold_means_per_venue() -> None:
    assert "no carry" in report.basis(FX)
    assert "without dividends" in report.basis(FX.model_copy(update={"venue": Venue.XNYS}))


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
    assert "1,560 sessions" in found and "annualised on 260 sessions a year" in found


def test_the_simulation_header_states_capital_years_and_sharpe_basis() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "key": ["k"],
            "sessions": [504],
            "total_return_pct": [1.0],
            "sharpe": [0.1],
            "max_drawdown_pct": [-1.0],
        }
    )
    found = views.basis_line(sma(), frame, 252)
    assert "$10,000 per run &middot; up to 2.00 years" in found
    assert "annualised on 252 sessions a year, risk-free 0%" in found
