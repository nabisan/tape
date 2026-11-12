from __future__ import annotations

import io
import time
from typing import TYPE_CHECKING, Any, Final

import httpx2
import pandas as pd
from pydantic import TypeAdapter

from tape.benchmarks import Comparison
from tape.fundamentals import Valuation
from tape.log import Log
from tape.service.ledgers import LedgerStatus
from tape.service.models import (
    Catalog,
    RunDays,
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
    from datetime import date

    from tape.runner import RunSpec
    from tape.service.results import Table


JSON: Final = {"content-type": "application/json"}
STRATEGIES: Final = TypeAdapter(dict[str, StrategyInfo])
UNIVERSES: Final = TypeAdapter(list[UniverseInfo])
SWEEPS: Final = TypeAdapter(list[SweepStatus])
KEYS: Final = TypeAdapter(list[str])


class ApiError(Exception):
    pass


class GoneError(ApiError):
    pass


class UnavailableError(ApiError):
    pass


READ: Final = 15.0
RUN: Final = 600.0
DOWNLOAD: Final = 120.0
SLOW: Final = 1.0


def _detail(r: httpx2.Response) -> str:
    if r.headers.get("content-type", "").startswith("application/json"):
        detail = r.json().get("detail", r.text)
        return detail if isinstance(detail, str) else str(detail)
    return r.text.strip() or r.reason_phrase


class Client:
    __slots__ = ("_http", "_log")

    def __init__(self, base_url: str, transport: httpx2.BaseTransport | None = None) -> None:
        self._http = httpx2.Client(base_url=base_url, timeout=READ, transport=transport)
        self._log = Log("Client")

    def _send(self, method: str, path: str, *, timeout: float = READ, **request: Any) -> httpx2.Response:
        started = time.perf_counter()
        try:
            r = self._http.request(method, path, timeout=timeout, **request)
        except httpx2.TimeoutException as e:
            self._log.warn("timed out", method=method, path=path, seconds=timeout)
            raise UnavailableError(f"the server did not answer within {timeout:g}s") from e
        except httpx2.TransportError as e:
            self._log.warn("unreachable", method=method, path=path, reason=e)
            raise UnavailableError(f"the server is unreachable: {e}") from e
        elapsed = time.perf_counter() - started
        if elapsed > SLOW:
            self._log.warn("slow", method=method, path=path, ms=round(1000 * elapsed))
        if r.status_code == 410:
            raise GoneError(_detail(r))
        if r.status_code != 200:
            raise ApiError(f"{method} {path}: {r.status_code} {_detail(r)}")
        return r

    def _get(self, path: str, *, timeout: float = READ) -> httpx2.Response:
        return self._send("GET", path, timeout=timeout)

    def catalog(self) -> Catalog:
        return Catalog.model_validate(self._get("/catalog").json())

    def strategies(self) -> dict[str, StrategyInfo]:
        return STRATEGIES.validate_python(self._get("/strategies").json())

    def space(self, strategy: str, ticker: str, end: date | None = None) -> StrategySpace:
        params = {"ticker": ticker} | ({"end": end.isoformat()} if end else {})
        return StrategySpace.model_validate(
            self._send("GET", f"/strategies/{strategy}/space", params=params).json()
        )

    def run(self, spec: RunSpec) -> RunSummary:
        r = self._send("POST", "/runs", timeout=RUN, content=spec.model_dump_json(), headers=JSON)
        return RunSummary.model_validate(r.json())

    def start_sweep(self, spec: SweepSpec) -> SweepStatus:
        r = self._send("POST", "/sweeps", timeout=RUN, content=spec.model_dump_json(), headers=JSON)
        return SweepStatus.model_validate(r.json())

    def running_sweeps(self) -> list[SweepStatus]:
        return SWEEPS.validate_python(self._get("/sweeps").json())

    def stop_sweep(self, key: str) -> SweepStatus:
        return SweepStatus.model_validate(self._send("POST", f"/sweeps/{key}/stop").json())

    def building_ledgers(self) -> list[str]:
        return KEYS.validate_python(self._get("/ledgers").json())

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
        return LedgerStatus.model_validate(self._send("POST", f"/runs/{key}/ledger").json())

    def ledger_file(self, key: str) -> bytes:
        return self._get(f"/runs/{key}/ledger.xlsx", timeout=DOWNLOAD).content

    def valuation(self, key: str) -> Valuation:
        return Valuation.model_validate(self._get(f"/runs/{key}/valuation").json())

    def days(self, key: str) -> RunDays:
        return RunDays.model_validate(self._get(f"/runs/{key}/days").json())

    def window(
        self, key: str, table: Table, start: date, end: date, max_rows: int
    ) -> tuple[pd.DataFrame, int]:
        query = f"start={start.isoformat()}&end={end.isoformat()}&max_rows={max_rows}"
        response = self._get(f"/runs/{key}/{table}?{query}")
        return pd.read_parquet(io.BytesIO(response.content)), int(response.headers.get(BARS_PER_ROW, "1"))

    def table(self, key: str, table: Table) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/runs/{key}/{table}").content))
