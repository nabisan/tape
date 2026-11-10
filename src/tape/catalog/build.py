from __future__ import annotations

import hashlib
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import repeat
from typing import TYPE_CHECKING, Final

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from tape import calendar
from tape.catalog import source
from tape.catalog.bars import aggregate, regular_hours
from tape.catalog.store import (
    LAYOUT,
    MANIFEST,
    SCHEMA_VERSION,
    SESSIONS,
    Manifest,
    Status,
    TickerReport,
    bar_path,
    write_bars,
    write_sessions,
)
from tape.core.hashing import Digest, digest
from tape.core.types import NS_PER_DAY, Ticker, Timeframe, ticker
from tape.log import Log

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tape.calendar import Sessions
    from tape.core.types import I32

REFERENCE: Final = Ticker("SPY")

log = Log("Catalog")


class CatalogError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Outcome:
    report: TickerReport
    present: I32
    non_session: npt.NDArray[np.datetime64]


def source_files(source_dir: Path, tickers: Sequence[Ticker] | None, reference: Ticker) -> dict[Ticker, Path]:
    found: dict[Ticker, Path] = {}
    for path in sorted(source_dir.glob("*.parquet")):
        try:
            found[ticker(path.stem)] = path
        except ValueError:
            log.warn("skipping file with invalid ticker name", path=path)
    if not found:
        raise CatalogError(f"no parquet files in {source_dir} (resolves to {source_dir.resolve()})")
    wanted = found.keys() if tickers is None else {*tickers, reference}
    missing = sorted(set(wanted) - found.keys())
    if missing:
        raise CatalogError(f"no source file for {', '.join(missing)} in {source_dir}")
    if reference not in found:
        raise CatalogError(f"reference {reference} missing from {source_dir}")
    return {t: found[t] for t in sorted(wanted)}


def _file_digest(path: Path) -> tuple[Digest, int]:
    with path.open("rb") as f:
        h = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=16))
    return Digest(h.hexdigest()), path.stat().st_size


def _day_range(path: Path) -> tuple[date, date] | None:
    try:
        time = pq.read_table(path, columns=["time"])["time"].cast(pa.int64()).to_numpy()
    except OSError, pa.ArrowException:
        return None
    if not time.size:
        return None
    lo, hi = (np.datetime64(int(v), "ms").astype("datetime64[D]").item() for v in (time.min(), time.max()))
    return lo, hi


def _rejected(t: Ticker, d: Digest, size: int, reason: str, source_rows: int = 0) -> _Outcome:
    report = TickerReport(
        ticker=t,
        status=Status.REJECTED,
        reason=reason,
        source_digest=d,
        source_bytes=size,
        source_rows=source_rows,
    )
    return _Outcome(report, np.empty(0, np.int32), np.empty(0, "datetime64[D]"))


def _build_ticker(t: Ticker, path: Path, expected: Digest, sessions: Sessions, root: Path) -> _Outcome:
    data = path.read_bytes()
    actual = Digest(hashlib.blake2b(data, digest_size=16).hexdigest())
    if actual != expected:
        raise CatalogError(f"{path} changed during the build")
    try:
        minutes, drops = source.read(data)
    except source.SourceError as e:
        return _rejected(t, actual, len(data), str(e))

    days = np.unique(minutes.time_ns // NS_PER_DAY).astype("datetime64[D]")
    non_session = np.setdiff1d(days, sessions.day)
    m1 = regular_hours(minutes, sessions)
    if not len(m1):
        return _rejected(t, actual, len(data), "no regular-hours bars", source_rows=len(minutes))

    present = np.unique(m1.session)
    first, last = int(present[0]), int(present[-1])
    missing = np.setdiff1d(np.arange(first, last + 1, dtype=np.int32), present)
    counts: dict[Timeframe, int] = {}
    for tf in Timeframe:
        bars = aggregate(m1, sessions, tf)
        write_bars(bars, bar_path(root, t, tf))
        counts[tf] = len(bars)

    report = TickerReport(
        ticker=t,
        status=Status.OK,
        source_digest=actual,
        source_bytes=len(data),
        source_rows=len(minutes) + drops.total,
        regular_rows=len(m1),
        non_session_days=len(non_session),
        first_session=sessions.day[first].item(),
        last_session=sessions.day[last].item(),
        sessions_expected=last - first + 1,
        sessions_missing=tuple(sessions.day[missing].tolist()),
        drops=drops,
        bars=counts,
    )
    return _Outcome(report, present, non_session)


def _check_reference(outcome: _Outcome, sessions: Sessions) -> None:
    r = outcome.report
    if r.status is not Status.OK:
        raise CatalogError(f"reference {r.ticker} rejected: {r.reason}")
    absent = np.setdiff1d(np.arange(len(sessions), dtype=np.int32), outcome.present)
    if absent.size:
        days = ", ".join(map(str, sessions.day[absent][:10]))
        raise CatalogError(f"reference {r.ticker} has no bars in {absent.size} calendar sessions: {days}")
    if outcome.non_session.size:
        days = ", ".join(map(str, outcome.non_session[:10]))
        raise CatalogError(f"reference {r.ticker} trades on {outcome.non_session.size} non-sessions: {days}")


def _log_report(r: TickerReport) -> None:
    if r.status is Status.REJECTED:
        log.warn("rejected", ticker=r.ticker, reason=r.reason)
        return
    fields = {"ticker": r.ticker, "rows": r.regular_rows, "sessions": r.sessions_expected}
    if r.sessions_missing or r.drops.total or r.non_session_days:
        log.warn(
            "built with gaps",
            **fields,
            missing=len(r.sessions_missing),
            dropped=r.drops.total,
            non_session_days=r.non_session_days,
        )
    else:
        log.info("built", **fields)


def build(
    source_dir: Path,
    store_dir: Path,
    *,
    tickers: Sequence[Ticker] | None = None,
    reference: Ticker = REFERENCE,
    workers: int = 1,
) -> Manifest:
    files = source_files(source_dir, tickers, reference)
    log.info("sources", dir=source_dir, files=len(files), workers=workers)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        hashed = dict(zip(files, pool.map(_file_digest, files.values()), strict=True))
        data_version = digest(
            {"calendar": calendar.LIBRARY, "sources": {t: d for t, (d, _) in hashed.items()}}
        )
        root = store_dir / LAYOUT / data_version
        if (root / MANIFEST).is_file():
            log.info("catalog up to date", data_version=data_version, root=root)
            return Manifest.model_validate_json((root / MANIFEST).read_bytes())

        ranges = [r for r in pool.map(_day_range, files.values()) if r is not None]
        if not ranges:
            raise CatalogError(f"no readable source file in {source_dir}")
        sessions = calendar.sessions(min(lo for lo, _ in ranges), max(hi for _, hi in ranges))
        log.info("calendar", sessions=len(sessions), first=sessions.day[0], last=sessions.day[-1])

        tmp = root.with_name(f".{data_version}.{os.getpid()}.tmp")
        try:
            tmp.mkdir(parents=True)
            outcomes: dict[Ticker, _Outcome] = {}
            for outcome in pool.map(
                _build_ticker,
                files,
                files.values(),
                [d for d, _ in hashed.values()],
                repeat(sessions),
                repeat(tmp),
            ):
                outcomes[outcome.report.ticker] = outcome
                _log_report(outcome.report)
            _check_reference(outcomes[reference], sessions)

            manifest = Manifest(
                schema_version=SCHEMA_VERSION,
                data_version=data_version,
                built_at=datetime.now(UTC),
                calendar=calendar.CALENDAR,
                calendar_library=calendar.LIBRARY,
                reference=reference,
                first_session=sessions.day[0].item(),
                last_session=sessions.day[-1].item(),
                sessions=len(sessions),
                tickers={t: o.report for t, o in outcomes.items()},
            )
            write_sessions(sessions, tmp / SESSIONS)
            (tmp / MANIFEST).write_text(manifest.model_dump_json(indent=2))
            tmp.rename(root)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    rejected = len(files) - len(manifest.available)
    log.info("catalog built", data_version=data_version, tickers=len(manifest.available), rejected=rejected)
    return manifest
