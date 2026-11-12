from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from tape import log
from tape.core.types import DATE
from tape.engine.backtest import ENGINE_VERSION
from tape.runner import record
from tape.strategies.registry import STRATEGIES
from tape.workbook import ledger

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Executor, Future
    from datetime import date
    from pathlib import Path

    from pydantic.fields import FieldInfo

    from tape.catalog.store import Store
    from tape.core.hashing import Digest
    from tape.features.grid import FeatureGrid
    from tape.runner import RunSpec
    from tape.service.results import ResultStore

FILE: Final = "ledger.xlsx"
FAILURE: Final = "ledger.error"
MEDIA: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TOLERANCE: Final = 1e-6
DASH: Final = "\u2013"


class LedgerState(StrEnum):
    ABSENT = "absent"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class LedgerStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: LedgerState
    size: int | None = None
    detail: str = ""


class LedgerMismatchError(ValueError):
    pass


def filename(spec: RunSpec) -> str:
    tf = str(spec.params.get("timeframe", "1d"))
    return f"{spec.ticker}_{spec.strategy}_{tf}_{spec.start:%Y%m%d}_{spec.end:%Y%m%d}_mtm.xlsx"


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, int | float):
        return f"{v:,g}"
    return str(v).replace("_", " ")


def _shown(field: FieldInfo, values: dict[str, object]) -> bool:
    extra = field.json_schema_extra
    when = extra.get("when") if isinstance(extra, dict) else None
    return not isinstance(when, dict) or all(values.get(k) == v for k, v in when.items())


def heading(spec: RunSpec, first: date, last: date) -> str:
    cls = STRATEGIES[spec.strategy]
    fields = cls.config_type.model_fields
    values = cls.config_type.model_validate(spec.params).model_dump(mode="json")
    params = " · ".join(
        f"{(fields[k].title or k.replace('_', ' ')).lower()} {_value(v)}"
        for k, v in values.items()
        if k != "timeframe" and _shown(fields[k], values)
    )
    tf = values.get("timeframe", "1d")
    return f"{spec.ticker} · {cls.title} · {tf} · {params} · {first:{DATE}} {DASH} {last:{DATE}}"


def build(store: Store, results: ResultStore, grid: FeatureGrid, key: Digest) -> int:
    folder = results.root / key
    failure = folder / FAILURE
    try:
        spec = results.spec(key)
        expected = results.metrics(key).net_pnl
        rec = record(store, spec, grid)
        if abs(rec.net_pnl - expected) > TOLERANCE * max(1.0, abs(expected)):
            raise LedgerMismatchError(
                f"ledger P&L {rec.net_pnl:,.2f} differs from the stored run's {expected:,.2f}; "
                "rerun the backtest"
            )
        tf = spec.params.get("timeframe", "1d")
        subtitle = (
            f"Mark-to-market ledger · one row per {tf} bar · reconciled with the engine, "
            f"net P&L ${expected:,.2f} · run {key[:8]} · engine v{ENGINE_VERSION}"
        )
        partial = folder / f"{FILE}.partial"
        times = rec.table["time"]
        first = times[0].as_py().date() if len(times) else spec.start
        last = times[-1].as_py().date() if len(times) else spec.end
        rows = ledger(partial, rec.table, rec.trades, heading(spec, first, last), subtitle)
        partial.replace(folder / FILE)
    except (ValueError, OSError) as e:
        failure.write_text(str(e))
        raise
    failure.unlink(missing_ok=True)
    return rows


class Ledgers:
    def __init__(self, results: ResultStore, pool: Callable[[], Executor], job: Callable[[str], int]) -> None:
        self.results = results
        self.pool = pool
        self.job = job
        self.jobs: dict[Digest, Future[int]] = {}
        self.out = log.Log("Ledger")

    def path(self, key: Digest) -> Path:
        return self.results.root / key / FILE

    def building(self) -> list[str]:
        return [k for k, job in self.jobs.items() if not job.done()]

    def status(self, key: Digest) -> LedgerStatus:
        path = self.path(key)
        if path.is_file():
            return LedgerStatus(state=LedgerState.READY, size=path.stat().st_size)
        job = self.jobs.get(key)
        if job is not None and not job.done():
            return LedgerStatus(state=LedgerState.BUILDING)
        if job is not None and (error := job.exception()) is not None:
            return LedgerStatus(state=LedgerState.FAILED, detail=str(error))
        failure = self.results.root / key / FAILURE
        if failure.is_file():
            return LedgerStatus(state=LedgerState.FAILED, detail=failure.read_text())
        return LedgerStatus(state=LedgerState.ABSENT)

    def start(self, key: Digest) -> LedgerStatus:
        current = self.status(key)
        if current.state in {LedgerState.READY, LedgerState.BUILDING}:
            return current
        (self.results.root / key / FAILURE).unlink(missing_ok=True)
        job = self.pool().submit(self.job, key)
        self.jobs[key] = job
        self.out.info("building", key=key)
        job.add_done_callback(lambda f: self._done(key, f))
        return LedgerStatus(state=LedgerState.BUILDING)

    def _done(self, key: Digest, job: Future[int]) -> None:
        if (error := job.exception()) is not None:
            self.out.error("failed", key=key, reason=error)
        else:
            self.out.info("ready", key=key, rows=job.result())
