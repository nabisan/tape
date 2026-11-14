from __future__ import annotations

import math
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tape.core.types import DATE, Ticker
from tape.universe import sic_division

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tape.universe import Classification, Membership, Profiles

ALL: Final = "all"
CUSTOM: Final = "custom"
QUINTILES: Final = ("Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest")
FULL_WINDOW: Final = 0.95


class Scheme(StrEnum):
    GICS = "GICS"
    SIC = "SIC"


class BasketQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    universe: str = ALL
    custom: tuple[Ticker, ...] = ()
    scheme: Scheme = Scheme.GICS
    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    volatility: tuple[int, ...] = Field(default=(), max_length=len(QUINTILES))
    start: date


class Choice(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str


class Basket(BaseModel):
    model_config = ConfigDict(frozen=True)

    universes: list[Choice]
    tickers: list[str]
    sectors: list[str] = []
    industries: list[str] = []
    quintiles: list[str] = []
    window: str | None = None
    notes: list[str] = []


def levels(entry: Classification, scheme: Scheme) -> tuple[str, str] | None:
    if scheme is Scheme.GICS:
        return (entry.gics.sector, entry.gics.industry) if entry.gics is not None else None
    if entry.sic is None:
        return None
    return (sic_division(entry.sic.code) or "Nonclassifiable", f"{entry.sic.code} {entry.sic.industry}")


def quintiles(values: Mapping[str, float]) -> dict[str, int]:
    names = sorted(values, key=values.__getitem__)
    return {t: min(4, i * 5 // len(names)) for i, t in enumerate(names)} if names else {}


def classify(
    q: BasketQuery, tickers: list[str], entries: Mapping[str, Classification]
) -> tuple[Basket, list[str]]:
    tiers = {t: levels(entries[t], q.scheme) for t in tickers if t in entries}
    sectors = sorted({lv[0] for lv in tiers.values() if lv is not None})
    pool = [t for t, lv in tiers.items() if lv is not None and (not q.sectors or lv[0] in q.sectors)]
    industries = sorted({lv[1] for t in pool if (lv := tiers[t]) is not None})
    found = Basket(universes=[], tickers=tickers, sectors=sectors, industries=industries)
    if not q.sectors and not q.industries:
        return found, []
    kept = [t for t in pool if (lv := tiers[t]) is not None and (not q.industries or lv[1] in q.industries)]
    notes = [f"{q.scheme} {', '.join(q.industries or q.sectors)}"]
    if unclassified := len(tickers) - sum(lv is not None for lv in tiers.values()):
        notes.append(f"{unclassified} without a {q.scheme} classification excluded")
    return found.model_copy(update={"tickers": kept}), notes


def volatility(
    q: BasketQuery, tickers: list[str], found: Profiles
) -> tuple[list[str], str | None, list[str]]:
    if found.first is None or found.last is None or not found.profiles:
        return tickers, None, ["Volatility baskets need a year of data before the start date."]
    members = set(tickers)
    full = {
        str(p.ticker): p.volatility_pct
        for p in found.profiles
        if p.ticker in members
        and p.sessions >= FULL_WINDOW * found.window
        and not math.isnan(p.volatility_pct)
    }
    window = f"{found.first:{DATE}} to {found.last:{DATE}}"
    if not full:
        return tickers, None, [f"No ticker has a full year of data before the start ({window})."]
    if not q.volatility:
        return tickers, window, []
    bucket = quintiles(full)
    wanted = set(q.volatility)
    kept = [t for t in tickers if t in bucket and bucket[t] in wanted]
    lo, hi = (float(np.percentile(list(full.values()), p)) for p in (0, 100))
    picked = ", ".join(QUINTILES[i] for i in sorted(wanted))
    notes = [f"vol {picked} within the basket, measured {window} (range {lo:.0f}% to {hi:.0f}%)"]
    if dropped := len(tickers) - len(full):
        notes.append(f"{dropped} without a full year before the start excluded")
    return kept, window, notes


def basket(
    q: BasketQuery,
    catalog: Sequence[str],
    members: Sequence[Membership],
    entries: Mapping[str, Classification],
    profiles: Profiles,
) -> Basket:
    available = set(catalog)
    universes = [
        Choice(key=ALL, title="All catalog"),
        *(Choice(key=m.index.value, title=m.title) for m in members),
        Choice(key=CUSTOM, title="Custom"),
    ]
    if q.universe == CUSTOM:
        chosen = [str(t) for t in dict.fromkeys(q.custom) if t in available]
        return Basket(universes=universes, tickers=chosen, notes=["custom selection"])
    member = next((m for m in members if m.index.value == q.universe), None)
    if member is None:
        pool, note = list(catalog), f"all {len(catalog):,} catalog tickers"
    else:
        pool = [t for t in member.tickers if t in available]
        note = f"{member.title} members as of {member.as_of:{DATE}}, today's list, survivorship-biased"
        if missing := len(member.tickers) - len(pool):
            note += f"; {missing} not in the catalog"
    classified, class_notes = classify(q, pool, entries)
    kept, window, vol_notes = volatility(q, classified.tickers, profiles)
    return classified.model_copy(
        update={
            "universes": universes,
            "tickers": kept,
            "quintiles": list(QUINTILES) if window else [],
            "window": window,
            "notes": [note, *class_notes, *vol_notes],
        }
    )
