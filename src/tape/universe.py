from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx2
import numpy as np
from pydantic import BaseModel, ConfigDict

from tape.core.types import Ticker, Timeframe
from tape.engine.reports import TRADING_DAYS, Measure

if TYPE_CHECKING:
    from tape.catalog.store import Store


class Index(StrEnum):
    SP500 = "sp500"
    NDX = "ndx"


TITLES: Final = {Index.SP500: "S&P 500", Index.NDX: "Nasdaq-100"}
SOURCES: Final = {
    Index.SP500: "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    Index.NDX: "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
}
NASDAQ_STAMP: Final = "%b %d, %Y %I:%M %p"
TABLE_ID: Final = "constituents"
SYMBOL_HEADERS: Final = ("Symbol", "Ticker")
DIRECTORY: Final = Path(__file__).with_name("universes")
CLASSIFICATION: Final = DIRECTORY / "classification.json"
AGENT: Final = "tape/0.1 reference-data"
SEC_TICKERS: Final = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS: Final = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_INTERVAL: Final = 0.125
CONTACT: Final = "TAPE_SEC_CONTACT"
WINDOW: Final = TRADING_DAYS


class Membership(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: Index
    title: str
    as_of: date
    source: str
    point_in_time: bool
    tickers: tuple[Ticker, ...]


class Gics(BaseModel):
    model_config = ConfigDict(frozen=True)

    sector: str
    industry: str


class Sic(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    industry: str


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: Ticker
    name: str | None = None
    gics: Gics | None = None
    sic: Sic | None = None


class Classifications(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    sources: dict[str, str]
    entries: tuple[Classification, ...]


class Listing(BaseModel):
    model_config = ConfigDict(frozen=True)

    tickers: tuple[Ticker, ...]
    as_of: date
    names: dict[Ticker, str] = {}
    gics: dict[Ticker, Gics] = {}


class Profile(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: Ticker
    sessions: int
    volatility_pct: Measure
    dollar_volume: Measure
    price: Measure


class Profiles(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: date
    window: int
    first: date | None
    last: date | None
    profiles: list[Profile]


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.rows: list[list[str]] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and (self.depth or dict(attrs).get("id") == TABLE_ID):
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.rows.append([])
        elif self.depth == 1 and tag in {"td", "th"}:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag in {"td", "th"} and self.cell is not None and self.rows:
            self.rows[-1].append("".join(self.cell).strip())
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def rows(page: str) -> list[dict[str, str]]:
    table = _Table()
    table.feed(page)
    if not table.rows:
        raise ValueError(f"no table with id {TABLE_ID!r}")
    header, *body = table.rows
    if not any(h in header for h in SYMBOL_HEADERS):
        raise ValueError(f"no symbol column in {header}")
    symbol = next(h for h in SYMBOL_HEADERS if h in header)
    return [dict(zip(header, row, strict=False)) for row in body if row and row[header.index(symbol)]]


def parse(page: str) -> tuple[Ticker, ...]:
    symbol = next(h for h in SYMBOL_HEADERS if h in rows(page)[0])
    return tuple(sorted({Ticker(r[symbol]) for r in rows(page)}))


def wikipedia(response: httpx2.Response, today: date) -> Listing:
    table = rows(response.text)
    return Listing(
        tickers=tuple(sorted({Ticker(r["Symbol"]) for r in table})),
        as_of=today,
        names={Ticker(r["Symbol"]): r["Security"] for r in table if r.get("Security")},
        gics={
            Ticker(r["Symbol"]): Gics(sector=r["GICS Sector"], industry=r["GICS Sub-Industry"])
            for r in table
            if r.get("GICS Sector") and r.get("GICS Sub-Industry")
        },
    )


def nasdaq(response: httpx2.Response, today: date) -> Listing:
    data = response.json()["data"]
    table = [r for r in data["data"]["rows"] if r.get("symbol")]
    stamp = data.get("date")
    return Listing(
        tickers=tuple(sorted({Ticker(r["symbol"].strip()) for r in table})),
        as_of=datetime.strptime(stamp, NASDAQ_STAMP).date() if stamp else today,
        names={Ticker(r["symbol"].strip()): r["companyName"] for r in table if r.get("companyName")},
    )


type Reader = Callable[[httpx2.Response, date], Listing]

READERS: Final[dict[Index, Reader]] = {Index.SP500: wikipedia, Index.NDX: nasdaq}


def listing(index: Index, today: date) -> Listing:
    response = httpx2.get(SOURCES[index], headers={"User-Agent": AGENT}, timeout=30)
    response.raise_for_status()
    return READERS[index](response, today)


def membership(index: Index, found: Listing) -> Membership:
    return Membership(
        index=index,
        title=TITLES[index],
        as_of=found.as_of,
        source=SOURCES[index],
        point_in_time=False,
        tickers=found.tickers,
    )


SIC_DIVISIONS: Final = (
    (1, 9, "Agriculture, Forestry & Fishing"),
    (10, 14, "Mining"),
    (15, 17, "Construction"),
    (20, 39, "Manufacturing"),
    (40, 49, "Transportation, Communications & Utilities"),
    (50, 51, "Wholesale Trade"),
    (52, 59, "Retail Trade"),
    (60, 67, "Finance, Insurance & Real Estate"),
    (70, 89, "Services"),
    (91, 99, "Public Administration"),
)


def sic_division(code: str) -> str | None:
    group = int(code[:2]) if code[:2].isdigit() else -1
    return next((name for lo, hi, name in SIC_DIVISIONS if lo <= group <= hi), None)


def sec_symbol(ticker: Ticker) -> str:
    return ticker.replace(".", "-").upper()


def sic(
    tickers: Iterable[Ticker],
    contact: str,
    transport: httpx2.BaseTransport | None = None,
    interval: float = SEC_INTERVAL,
) -> dict[Ticker, Sic]:
    out: dict[Ticker, Sic] = {}
    headers = {"User-Agent": f"{AGENT} {contact}"}
    with httpx2.Client(headers=headers, timeout=30, transport=transport) as http:
        directory = http.get(SEC_TICKERS)
        directory.raise_for_status()
        ciks = {str(row["ticker"]).upper(): int(row["cik_str"]) for row in directory.json().values()}
        for ticker in tickers:
            cik = ciks.get(sec_symbol(ticker))
            if cik is None:
                continue
            response = http.get(SEC_SUBMISSIONS.format(cik=cik))
            time.sleep(interval)
            if response.status_code != 200:
                continue
            body = response.json()
            if code := str(body.get("sic") or ""):
                out[ticker] = Sic(code=code, industry=str(body.get("sicDescription") or code))
    return out


def classify(
    tickers: Iterable[Ticker], names: dict[Ticker, str], gics: dict[Ticker, Gics], sics: dict[Ticker, Sic]
) -> tuple[Classification, ...]:
    return tuple(
        Classification(ticker=t, name=names.get(t), gics=gics.get(t), sic=sics.get(t))
        for t in sorted(set(tickers))
    )


def path(index: Index) -> Path:
    return DIRECTORY / f"{index}.json"


def save(membership: Membership) -> Path:
    DIRECTORY.mkdir(exist_ok=True)
    target = path(membership.index)
    target.write_text(membership.model_dump_json(indent=2) + "\n")
    return target


def save_classifications(found: Classifications) -> Path:
    DIRECTORY.mkdir(exist_ok=True)
    CLASSIFICATION.write_text(found.model_dump_json(indent=2) + "\n")
    return CLASSIFICATION


def classifications() -> Classifications | None:
    if not CLASSIFICATION.is_file():
        return None
    return Classifications.model_validate_json(CLASSIFICATION.read_text())


def memberships() -> list[Membership]:
    return [Membership.model_validate_json(path(i).read_text()) for i in Index if path(i).is_file()]


def profiles(store: Store, before: date, window: int = WINDOW) -> Profiles:
    sessions = store.sessions()
    end = int(np.searchsorted(sessions.day, np.datetime64(before, "D"), side="left"))
    start = max(0, end - window)
    days = sessions.day[start:end]
    out: list[Profile] = []
    for ticker in store.manifest.available:
        bars = store.bars(ticker, Timeframe.D1)
        inside = (bars.session >= start) & (bars.session < end)
        close = bars.close[inside]
        volume = bars.volume[inside].astype(np.float64)
        n = len(close)
        r = close[1:] / close[:-1] - 1 if n > 1 else np.empty(0)
        out.append(
            Profile(
                ticker=ticker,
                sessions=n,
                volatility_pct=100 * float(r.std(ddof=1)) * math.sqrt(TRADING_DAYS) if n > 2 else math.nan,
                dollar_volume=float((close * volume).mean()) if n else math.nan,
                price=float(close[-1]) if n else math.nan,
            )
        )
    return Profiles(
        before=before,
        window=window,
        first=days[0].astype(date) if len(days) else None,
        last=days[-1].astype(date) if len(days) else None,
        profiles=out,
    )
