from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx2
import pandas as pd

from tape.benchmarks import Comparison
from tape.engine.reports import Metrics
from tape.service.results import BARS_PER_ROW
from tape.universe import Classification, Profiles

if TYPE_CHECKING:
    from tape.service.results import Table


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

    def catalog(self) -> dict[str, Any]:
        result: dict[str, Any] = self._get("/catalog").json()
        return result

    def strategies(self) -> dict[str, Any]:
        result: dict[str, Any] = self._get("/strategies").json()
        return result

    def space(self, strategy: str, ticker: str, end: str | None = None) -> dict[str, Any]:
        params = {"ticker": ticker} | ({"end": end} if end else {})
        r = self._http.get(f"/strategies/{strategy}/space", params=params)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {r.text}")
        result: dict[str, Any] = r.json()
        return result

    def run(self, spec: dict[str, Any]) -> tuple[str, Metrics]:
        r = self._http.post("/runs", json=spec)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {_detail(r)}")
        body = r.json()
        return str(body["key"]), Metrics.model_validate(body["metrics"])

    def start_sweep(self, spec: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post("/sweeps", json=spec)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {_detail(r)}")
        result: dict[str, Any] = r.json()
        return result

    def sweep(self, key: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/sweeps/{key}").json()
        return result

    def sweep_summary(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/sweeps/{key}/summary").content))

    def sweep_spec(self, key: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/sweeps/{key}/spec").json()
        return result

    def sweep_paths(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/sweeps/{key}/paths").content))

    def metrics(self, key: str) -> Metrics:
        return Metrics.model_validate(self._get(f"/runs/{key}").json()["metrics"])

    def spec(self, key: str) -> dict[str, Any]:
        result: dict[str, Any] = self._get(f"/runs/{key}").json()["spec"]
        return result

    def universes(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._get("/universes").json()
        return result

    def classification(self) -> list[Classification]:
        return [Classification.model_validate(e) for e in self._get("/universes/classification").json()]

    def profiles(self, before: str) -> Profiles:
        return Profiles.model_validate(self._get(f"/universes/profiles?before={before}").json())

    def comparison(self, key: str) -> Comparison:
        return Comparison.model_validate(self._get(f"/runs/{key}/comparison").json())

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
