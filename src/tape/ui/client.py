from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING, Final

import httpx2
import pandas as pd
from pydantic import TypeAdapter

from tape.benchmarks import Comparison
from tape.fundamentals import Valuation
from tape.service.ledgers import LedgerStatus
from tape.service.models import (
    Catalog,
    RunDetail,
    RunSummary,
    StrategyInfo,
    StrategySpace,
    UniverseInfo,
)
from tape.service.results import BARS_PER_ROW
from tape.service.sweeps import SweepStatus
from tape.sweep import SweepSpec
from tape.universe import Classification, Profiles

if TYPE_CHECKING:
    from tape.runner import RunSpec
    from tape.service.results import Table


JSON: Final = {"content-type": "application/json"}
STRATEGIES: Final = TypeAdapter(dict[str, StrategyInfo])
UNIVERSES: Final = TypeAdapter(list[UniverseInfo])


class ApiError(Exception):
    pass


class GoneError(ApiError):
    pass


def _detail(r: httpx2.Response) -> str:
    if r.headers.get("content-type", "").startswith("application/json"):
        detail = r.json().get("detail", r.text)
        return detail if isinstance(detail, str) else str(detail)
    return r.text.strip() or r.reason_phrase


class Client:
    __slots__ = ("_http",)

    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self._http = httpx2.Client(base_url=base_url, timeout=timeout)

    def _get(self, path: str) -> httpx2.Response:
        r = self._http.get(path)
        if r.status_code == 410:
            raise GoneError(_detail(r))
        if r.status_code != 200:
            raise ApiError(f"GET {path}: {r.status_code} {r.text}")
        return r

    def catalog(self) -> Catalog:
        return Catalog.model_validate(self._get("/catalog").json())

    def strategies(self) -> dict[str, StrategyInfo]:
        return STRATEGIES.validate_python(self._get("/strategies").json())

    def space(self, strategy: str, ticker: str, end: date | None = None) -> StrategySpace:
        params = {"ticker": ticker} | ({"end": end.isoformat()} if end else {})
        r = self._http.get(f"/strategies/{strategy}/space", params=params)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {r.text}")
        return StrategySpace.model_validate(r.json())

    def run(self, spec: RunSpec) -> RunSummary:
        r = self._http.post("/runs", content=spec.model_dump_json(), headers=JSON)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {_detail(r)}")
        return RunSummary.model_validate(r.json())

    def start_sweep(self, spec: SweepSpec) -> SweepStatus:
        r = self._http.post("/sweeps", content=spec.model_dump_json(), headers=JSON)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {_detail(r)}")
        return SweepStatus.model_validate(r.json())

    def sweep(self, key: str) -> SweepStatus:
        return SweepStatus.model_validate(self._get(f"/sweeps/{key}").json())

    def sweep_summary(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/sweeps/{key}/summary").content))

    def sweep_spec(self, key: str) -> SweepSpec:
        return SweepSpec.model_validate(self._get(f"/sweeps/{key}/spec").json())

    def sweep_paths(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/sweeps/{key}/paths").content))

    def detail(self, key: str) -> RunDetail:
        return RunDetail.model_validate(self._get(f"/runs/{key}").json())

    def universes(self) -> list[UniverseInfo]:
        return UNIVERSES.validate_python(self._get("/universes").json())

    def classification(self) -> list[Classification]:
        return [Classification.model_validate(e) for e in self._get("/universes/classification").json()]

    def profiles(self, before: str) -> Profiles:
        return Profiles.model_validate(self._get(f"/universes/profiles?before={before}").json())

    def comparison(self, key: str) -> Comparison:
        return Comparison.model_validate(self._get(f"/runs/{key}/comparison").json())

    def ledger(self, key: str) -> LedgerStatus:
        return LedgerStatus.model_validate(self._get(f"/runs/{key}/ledger").json())

    def build_ledger(self, key: str) -> LedgerStatus:
        r = self._http.post(f"/runs/{key}/ledger")
        if r.status_code != 200:
            raise ApiError(f"POST /runs/{key}/ledger: {r.status_code} {_detail(r)}")
        return LedgerStatus.model_validate(r.json())

    def ledger_file(self, key: str) -> bytes:
        return self._get(f"/runs/{key}/ledger.xlsx").content

    def valuation(self, key: str) -> Valuation:
        return Valuation.model_validate(self._get(f"/runs/{key}/valuation").json())

    def days(self, key: str) -> tuple[list[date], date | None]:
        body = self._get(f"/runs/{key}/days").json()
        recent = body["recent"]
        return [date.fromisoformat(d) for d in body["days"]], date.fromisoformat(recent) if recent else None

    def window(
        self, key: str, table: Table, start: date, end: date, max_rows: int
    ) -> tuple[pd.DataFrame, int]:
        query = f"start={start.isoformat()}&end={end.isoformat()}&max_rows={max_rows}"
        response = self._get(f"/runs/{key}/{table}?{query}")
        return pd.read_parquet(io.BytesIO(response.content)), int(response.headers.get(BARS_PER_ROW, "1"))

    def table(self, key: str, table: Table) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/runs/{key}/{table}").content))
