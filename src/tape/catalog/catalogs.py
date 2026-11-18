from __future__ import annotations

from typing import TYPE_CHECKING

from tape.catalog.store import Store
from tape.venues import Venue

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from tape.core.hashing import Digest
    from tape.core.types import Ticker


class Catalogs:
    __slots__ = ("_stores", "_venue_of")

    def __init__(self, stores: Mapping[Venue, Store]) -> None:
        if not stores:
            raise FileNotFoundError("no catalog for any venue")
        self._stores = dict(stores)
        self._venue_of: dict[Ticker, Venue] = {}
        for venue, store in self._stores.items():
            for ticker in store.manifest.available:
                if ticker in self._venue_of:
                    raise ValueError(f"{ticker} is listed by both {self._venue_of[ticker]} and {venue}")
                self._venue_of[ticker] = venue

    @classmethod
    def open(cls, store_dir: Path) -> Catalogs:
        stores = {}
        for venue in Venue:
            try:
                stores[venue] = Store.open(store_dir, venue=venue)
            except FileNotFoundError:
                continue
        return cls(stores)

    @classmethod
    def at(cls, store_dir: Path, versions: Mapping[Venue, Digest]) -> Catalogs:
        return cls({venue: Store.open(store_dir, version) for venue, version in versions.items()})

    @property
    def versions(self) -> dict[Venue, Digest]:
        return {venue: store.manifest.data_version for venue, store in self._stores.items()}

    @property
    def venues(self) -> list[Venue]:
        return list(self._stores)

    @property
    def available(self) -> list[Ticker]:
        return sorted(self._venue_of)

    def has(self, ticker: Ticker) -> bool:
        return ticker in self._venue_of

    def venue(self, ticker: Ticker) -> Venue:
        found = self._venue_of.get(ticker)
        if found is None:
            raise KeyError(f"{ticker} is in no catalog")
        return found

    def store(self, ticker: Ticker) -> Store:
        return self._stores[self.venue(ticker)]

    def of(self, venue: Venue) -> Store | None:
        return self._stores.get(venue)

    def common(self, tickers: Iterable[Ticker]) -> Store:
        venues = {self.venue(t) for t in tickers}
        if len(venues) != 1:
            names = ", ".join(sorted(venues)) or "none"
            raise KeyError(f"tickers must share one venue, found {names}")
        return self._stores[venues.pop()]
