from __future__ import annotations

import html
import math
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import streamlit as st

from tape.core.types import DATE
from tape.universe import sic_division

if TYPE_CHECKING:
    from tape.ui.client import Client
    from tape.universe import Classification, Profiles

ALL: Final = "All catalog"
QUINTILES: Final = ("Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest")
FULL_WINDOW: Final = 0.95


class Scheme(StrEnum):
    GICS = "GICS"
    SIC = "SIC"


def levels(entry: Classification, scheme: Scheme) -> tuple[str, str] | None:
    if scheme is Scheme.GICS:
        return (entry.gics.sector, entry.gics.industry) if entry.gics is not None else None
    if entry.sic is None:
        return None
    return (sic_division(entry.sic.code) or "Nonclassifiable", f"{entry.sic.code} {entry.sic.industry}")


def quintiles(values: dict[str, float]) -> dict[str, int]:
    names = sorted(values, key=values.__getitem__)
    return {t: min(4, i * 5 // len(names)) for i, t in enumerate(names)} if names else {}


@st.cache_data(ttl=3600)
def _universes(_client: Client) -> list[dict[str, Any]]:
    return _client.universes()


@st.cache_resource
def _classification(_client: Client) -> list[Classification]:
    return _client.classification()


@st.cache_resource(max_entries=32)
def _profiles(_client: Client, before: str) -> Profiles:
    return _client.profiles(before)


def universe(client: Client, catalog: list[str]) -> tuple[str, list[str], str]:
    found = {u["membership"]["title"]: u for u in _universes(client)}
    name = st.selectbox("Universe", [ALL, *found], key="basket:universe")
    if name == ALL:
        return name, catalog, f"all {len(catalog):,} catalog tickers"
    u = found[name]
    m = u["membership"]
    as_of = date.fromisoformat(m["as_of"]).strftime(DATE)
    note = f"{name} members as of {as_of}, today's list, survivorship-biased"
    if u["missing"]:
        note += f"; {len(u['missing'])} not in the catalog"
    return name, list(u["in_catalog"]), note


def classify(client: Client, tickers: list[str]) -> tuple[list[str], list[str]]:
    scheme = Scheme(
        st.segmented_control("Classification", list(Scheme), default=Scheme.GICS, key="basket:scheme")
        or Scheme.GICS
    )
    entries: dict[str, Classification] = {str(e.ticker): e for e in _classification(client)}
    tiers = {t: levels(entries[t], scheme) for t in tickers if t in entries}
    sectors = sorted({lv[0] for lv in tiers.values() if lv is not None})
    chosen = st.multiselect("Sector", sectors, key=f"basket:sector:{scheme}", placeholder="All sectors")
    pool = [t for t, lv in tiers.items() if lv is not None and (not chosen or lv[0] in chosen)]
    industries = sorted({lv[1] for t in pool if (lv := tiers[t]) is not None})
    picked = st.multiselect(
        "Industry",
        industries,
        key=f"basket:industry:{scheme}:{'|'.join(chosen)}",
        placeholder="All industries",
    )
    if not chosen and not picked:
        return tickers, []
    kept = [t for t in pool if (lv := tiers[t]) is not None and (not picked or lv[1] in picked)]
    unclassified = len(tickers) - sum(lv is not None for lv in tiers.values())
    notes = [f"{scheme} {', '.join(picked or chosen)}"]
    if unclassified:
        notes.append(f"{unclassified} without a {scheme} classification excluded")
    return kept, notes


def volatility(client: Client, tickers: list[str], start: date) -> tuple[list[str], list[str]]:
    found = _profiles(client, start.isoformat())
    if found.first is None or found.last is None or len(found.profiles) == 0:
        st.caption("Volatility baskets need a year of data before the start date.")
        return tickers, []
    members = set(tickers)
    full: dict[str, float] = {
        str(p.ticker): p.volatility_pct
        for p in found.profiles
        if p.ticker in members
        and p.sessions >= FULL_WINDOW * found.window
        and not math.isnan(p.volatility_pct)
    }
    window = f"{found.first.strftime(DATE)} to {found.last.strftime(DATE)}"
    if not full:
        st.caption(f"No ticker has a full year of data before the start ({window}).")
        return tickers, []
    picked = st.multiselect(
        "Volatility quintile",
        QUINTILES,
        key="basket:vol",
        placeholder="Any volatility",
        help=f"annualized, {window}",
    )
    if not picked:
        return tickers, []
    bucket = quintiles(full)
    wanted = {QUINTILES.index(q) for q in picked}
    kept = [t for t in tickers if t in bucket and bucket[t] in wanted]
    lo, hi = (np.percentile(list(full.values()), q) for q in (0, 100))
    notes = [f"vol {', '.join(picked)} within the basket, measured {window} (range {lo:.0f}% to {hi:.0f}%)"]
    if dropped := len(tickers) - len(full):
        notes.append(f"{dropped} without a full year before the start excluded")
    return kept, notes


def summary(tickers: list[str], notes: list[str]) -> None:
    lines = "".join(f"<div>{html.escape(n)}</div>" for n in notes)
    st.html(f'<div class="t-basket"><b>{len(tickers):,} tickers</b>{lines}</div>')
