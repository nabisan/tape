from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

import httpx2
import numpy as np
import pytest

from tape.core.types import Ticker, Timeframe
from tape.engine.reports import TRADING_DAYS
from tape.ui.basket import quintiles
from tape.universe import Gics, Sic, nasdaq, parse, profiles, sic, sic_division, wikipedia
from tests.test_engine_runs import make_store

if TYPE_CHECKING:
    from tape.catalog.store import Store

PAGE = """
<table class="wikitable" id="other"><tr><th>Symbol</th></tr><tr><td>ZZZ</td></tr></table>
<table class="wikitable sortable" id="constituents">
<tr><th>Symbol</th><th>Security</th></tr>
<tr><td><a href="/x">MMM</a></td><td>3M</td></tr>
<tr><td>BRK.B</td><td>Berkshire <table><tr><td>nested</td></tr></table></td></tr>
<tr><td>AAPL</td><td>Apple</td></tr>
</table>
"""


def test_parse_reads_the_constituents_table_only() -> None:
    assert parse(PAGE) == ("AAPL", "BRK.B", "MMM")


def test_parse_rejects_a_page_without_the_table() -> None:
    with pytest.raises(ValueError, match="constituents"):
        parse("<table id='x'><tr><th>Symbol</th></tr></table>")


def test_nasdaq_reader_takes_symbols_and_the_published_date() -> None:
    body = {
        "data": {"date": "Nov 10, 2026 2:56 PM", "data": {"rows": [{"symbol": "MSFT "}, {"symbol": "AAPL"}]}}
    }
    found = nasdaq(httpx2.Response(200, json=body), date(2026, 11, 11))
    assert found.tickers == ("AAPL", "MSFT")
    assert found.as_of == date(2026, 11, 10)


def test_wikipedia_reader_keeps_gics() -> None:
    page = """<table id="constituents"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>
    <th>GICS Sub-Industry</th></tr><tr><td>F</td><td>Ford</td><td>Consumer Discretionary</td>
    <td>Automobile Manufacturers</td></tr></table>"""
    found = wikipedia(httpx2.Response(200, text=page), date(2026, 11, 11))
    assert found.names == {"F": "Ford"}
    assert found.gics[Ticker("F")] == Gics(
        sector="Consumer Discretionary", industry="Automobile Manufacturers"
    )


def test_sic_maps_tickers_through_the_sec_directory() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["User-Agent"])
        if request.url.path.endswith("company_tickers.json"):
            return httpx2.Response(
                200,
                json={
                    "0": {"cik_str": 1874178, "ticker": "RIVN"},
                    "1": {"cik_str": 1067983, "ticker": "BRK-B"},
                },
            )
        if "CIK0001874178" in request.url.path:
            return httpx2.Response(
                200, json={"sic": "3711", "sicDescription": "Motor Vehicles & Passenger Car Bodies"}
            )
        return httpx2.Response(404)

    found = sic(
        [Ticker("RIVN"), Ticker("BRK.B"), Ticker("NOPE")], "ops@example.com", httpx2.MockTransport(handler), 0
    )
    assert found == {"RIVN": Sic(code="3711", industry="Motor Vehicles & Passenger Car Bodies")}
    assert all(agent.endswith("ops@example.com") for agent in seen)
    assert len(seen) == 3


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return make_store(tmp_path_factory.mktemp("universe"))


def test_profiles_measure_the_window_before_the_start(store: Store) -> None:
    days = store.sessions().day
    before = days[-1].astype(date)
    window = 20
    result = profiles(store, before, window)
    assert result.last == days[-2].astype(date)
    bars = store.bars(Ticker("AAPL"), Timeframe.D1)
    end = len(days) - 1
    close = bars.close[(bars.session >= end - window) & (bars.session < end)]
    expected = 100 * float((close[1:] / close[:-1] - 1).std(ddof=1)) * math.sqrt(TRADING_DAYS)
    aapl = next(p for p in result.profiles if p.ticker == "AAPL")
    assert aapl.sessions == window
    assert aapl.volatility_pct == pytest.approx(expected)
    assert aapl.price == pytest.approx(float(close[-1]))


def test_profiles_before_the_data_are_empty(store: Store) -> None:
    result = profiles(store, store.sessions().day[0].astype(date))
    assert result.first is None
    assert all(p.sessions == 0 and np.isnan(p.volatility_pct) for p in result.profiles)


def test_sic_divisions_follow_the_official_ranges() -> None:
    assert sic_division("3711") == "Manufacturing"
    assert sic_division("6021") == "Finance, Insurance & Real Estate"
    assert sic_division("7372") == "Services"
    assert sic_division("0100") == "Agriculture, Forestry & Fishing"
    assert sic_division("9995") == "Public Administration"
    assert sic_division("") is None


def test_quintiles_split_evenly_by_rank() -> None:
    values = {f"T{i}": float(i) for i in range(10)}
    bucket = quintiles(values)
    assert [bucket[f"T{i}"] for i in range(10)] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert quintiles({}) == {}
