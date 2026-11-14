from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from tape.baskets import ALL, CUSTOM, BasketQuery, Scheme, basket
from tape.core.types import Ticker
from tape.universe import Classification, Gics, Index, Membership, Profile, Profiles, Sic
from tests.test_sweep import client, root

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

__all__ = ["client", "root"]

CATALOG = ["AAPL", "MSFT", "XOM", "JPM", "NVDA", "KO"]
NDX = Membership(
    index=Index.NDX,
    title="Nasdaq-100",
    as_of=date(2026, 11, 10),
    source="test",
    point_in_time=False,
    tickers=tuple(Ticker(t) for t in ("AAPL", "MSFT", "NVDA", "ZZZZ")),
)


def tech(t: str, industry: str, sic: str) -> Classification:
    return Classification(
        ticker=Ticker(t),
        gics=Gics(sector="Information Technology", industry=industry),
        sic=Sic(code=sic, industry="Computers"),
    )


ENTRIES = {
    "AAPL": tech("AAPL", "Technology Hardware", "3571"),
    "MSFT": tech("MSFT", "Software", "7372"),
    "NVDA": tech("NVDA", "Semiconductors", "3674"),
    "XOM": Classification(ticker=Ticker("XOM"), gics=Gics(sector="Energy", industry="Oil & Gas")),
}
START = date(2024, 6, 3)
NONE = Profiles(before=START, window=252, first=None, last=None, profiles=[])
YEAR = Profiles(
    before=START,
    window=252,
    first=date(2023, 6, 1),
    last=date(2024, 5, 31),
    profiles=[
        Profile(ticker=Ticker(t), sessions=252, volatility_pct=v, dollar_volume=1e9, price=100.0)
        for t, v in (("AAPL", 20.0), ("MSFT", 25.0), ("XOM", 30.0), ("JPM", 35.0), ("NVDA", 60.0))
    ]
    + [Profile(ticker=Ticker("KO"), sessions=40, volatility_pct=15.0, dollar_volume=1e9, price=60.0)],
)


def test_universes_offer_the_catalog_each_index_and_custom() -> None:
    found = basket(BasketQuery(start=START), CATALOG, [NDX], ENTRIES, NONE)
    assert [u.key for u in found.universes] == [ALL, "ndx", CUSTOM]
    assert found.tickers == CATALOG and found.notes[0] == "all 6 catalog tickers"
    assert found.quintiles == [] and "need a year of data" in found.notes[-1]


def test_an_index_keeps_its_catalog_members_and_says_what_is_missing() -> None:
    found = basket(BasketQuery(universe="ndx", start=START), CATALOG, [NDX], ENTRIES, NONE)
    assert found.tickers == ["AAPL", "MSFT", "NVDA"]
    assert found.notes[0] == (
        "Nasdaq-100 members as of 10 Nov 2026, today's list, survivorship-biased; 1 not in the catalog"
    )


def test_sector_and_industry_narrow_the_basket_and_name_the_filter() -> None:
    q = BasketQuery(universe=ALL, sectors=("Information Technology",), start=START)
    found = basket(q, CATALOG, [NDX], ENTRIES, NONE)
    assert found.tickers == ["AAPL", "MSFT", "NVDA"]
    assert found.sectors == ["Energy", "Information Technology"]
    assert found.industries == ["Semiconductors", "Software", "Technology Hardware"]
    assert "GICS Information Technology" in found.notes
    assert "2 without a GICS classification excluded" in found.notes
    narrow = basket(q.model_copy(update={"industries": ("Software",)}), CATALOG, [NDX], ENTRIES, NONE)
    assert narrow.tickers == ["MSFT"] and "GICS Software" in narrow.notes


def test_sic_groups_by_division_and_code() -> None:
    found = basket(BasketQuery(scheme=Scheme.SIC, start=START), CATALOG, [NDX], ENTRIES, NONE)
    assert found.sectors == ["Manufacturing", "Services"]
    assert "7372 Computers" in found.industries


def test_a_custom_basket_keeps_only_catalog_tickers_in_order() -> None:
    q = BasketQuery(universe=CUSTOM, custom=(Ticker("XOM"), Ticker("ZZZZ"), Ticker("AAPL")), start=START)
    assert basket(q, CATALOG, [NDX], ENTRIES, NONE).tickers == ["XOM", "AAPL"]


def test_volatility_quintiles_rank_tickers_with_a_full_year() -> None:
    open_ = basket(BasketQuery(start=START), CATALOG, [NDX], ENTRIES, YEAR)
    assert open_.quintiles[0] == "Q1 lowest" and open_.window == "01 Jun 2023 to 31 May 2024"
    calm = basket(BasketQuery(volatility=(0,), start=START), CATALOG, [NDX], ENTRIES, YEAR)
    assert calm.tickers == ["AAPL"]
    assert (
        calm.notes[-2].startswith("vol Q1 lowest within the basket") and "range 20% to 60%" in calm.notes[-2]
    )
    assert calm.notes[-1] == "1 without a full year before the start excluded"


def test_the_basket_route_answers_with_a_typed_basket(client: TestClient) -> None:
    r = client.post("/universes/basket", json={"universe": "all", "start": "2024-03-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tickers"] == ["AAPL", "SPY"] and body["universes"][0]["key"] == "all"
    assert client.post("/universes/basket", json={"universe": "all"}).status_code == 422
