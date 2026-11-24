from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tape import log, portfolio_index
from tape.catalog.catalogs import Catalogs
from tape.core.hashing import Digest
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.runner import RunSpec, execute, execute_book
from tape.service.ledgers import build
from tape.service.results import ResultStore
from tape.venues import Venue

if TYPE_CHECKING:
    from tape.log import Level

_STATE: Final[dict[str, tuple[Catalogs, ResultStore, FeatureGrid]]] = {}


def versions(catalogs: Catalogs) -> str:
    return json.dumps({str(v): d for v, d in catalogs.versions.items()})


def init_worker(
    store_dir: str,
    data_versions: str,
    results_dir: str,
    level: Level,
    grid: str = DEFAULT_GRID.model_dump_json(),
) -> None:
    log.setup(level)
    found = {Venue(v): Digest(d) for v, d in json.loads(data_versions).items()}
    catalogs = Catalogs.at(Path(store_dir), found)
    _STATE["state"] = (catalogs, ResultStore(Path(results_dir)), FeatureGrid.model_validate_json(grid))


def build_portfolios() -> int:
    catalogs, results, _ = _STATE["state"]
    return portfolio_index.build(results, catalogs).rows


def execute_run(payload: str) -> Digest:
    catalogs, results, grid = _STATE["state"]
    spec = RunSpec.model_validate_json(payload)
    result = execute(catalogs.store(spec.ticker), spec, grid)
    results.save(result)
    return result.key


def execute_book_run(payload: str) -> Digest:
    catalogs, results, grid = _STATE["state"]
    specs = [RunSpec.model_validate(item) for item in json.loads(payload)]
    book = execute_book(catalogs.common([s.ticker for s in specs]), specs, grid)
    results.save_book(book)
    return book.key


def init_ledger_worker(
    store_dir: str,
    data_versions: str,
    results_dir: str,
    level: Level,
    grid: str = DEFAULT_GRID.model_dump_json(),
) -> None:
    os.nice(10)
    init_worker(store_dir, data_versions, results_dir, level, grid)


def build_ledger(key: str) -> int:
    catalogs, results, grid = _STATE["state"]
    return build(catalogs, results, grid, Digest(key))
