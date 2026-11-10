from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import httpx2
import pandas as pd

from tape.engine.reports import Metrics

if TYPE_CHECKING:
    from tape.service.results import Table


class ApiError(Exception):
    pass


class Client:
    __slots__ = ("_http",)

    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self._http = httpx2.Client(base_url=base_url, timeout=timeout)

    def _get(self, path: str) -> httpx2.Response:
        r = self._http.get(path)
        if r.status_code != 200:
            raise ApiError(f"GET {path}: {r.status_code} {r.text}")
        return r

    def catalog(self) -> dict[str, Any]:
        result: dict[str, Any] = self._get("/catalog").json()
        return result

    def strategies(self) -> dict[str, Any]:
        result: dict[str, Any] = self._get("/strategies").json()
        return result

    def space(self, strategy: str, ticker: str) -> dict[str, Any]:
        r = self._http.get(f"/strategies/{strategy}/space", params={"ticker": ticker})
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {r.text}")
        result: dict[str, Any] = r.json()
        return result

    def run(self, spec: dict[str, Any]) -> tuple[str, Metrics]:
        r = self._http.post("/runs", json=spec)
        if r.status_code != 200:
            raise ApiError(f"{r.status_code}: {r.json().get('detail', r.text)}")
        body = r.json()
        return str(body["key"]), Metrics.model_validate(body["metrics"])

    def table(self, key: str, table: Table) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(f"/runs/{key}/{table}").content))
