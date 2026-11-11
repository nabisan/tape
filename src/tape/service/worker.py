from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tape import log
from tape.catalog.store import Store
from tape.core.hashing import Digest
from tape.features.grid import DEFAULT_GRID, FeatureGrid
from tape.runner import RunSpec, execute
from tape.service.ledgers import build
from tape.service.results import ResultStore

if TYPE_CHECKING:
    from tape.log import Level

_STATE: Final[dict[str, tuple[Store, ResultStore, FeatureGrid]]] = {}


def init_worker(
    store_dir: str,
    data_version: str,
    results_dir: str,
    level: Level,
    grid: str = DEFAULT_GRID.model_dump_json(),
) -> None:
    log.setup(level)
    store = Store.open(Path(store_dir), Digest(data_version))
    _STATE["state"] = (store, ResultStore(Path(results_dir)), FeatureGrid.model_validate_json(grid))


def execute_run(payload: str) -> Digest:
    store, results, grid = _STATE["state"]
    result = execute(store, RunSpec.model_validate_json(payload), grid)
    results.save(result)
    return result.key


def init_ledger_worker(
    store_dir: str,
    data_version: str,
    results_dir: str,
    level: Level,
    grid: str = DEFAULT_GRID.model_dump_json(),
) -> None:
    os.nice(10)
    init_worker(store_dir, data_version, results_dir, level, grid)


def build_ledger(key: str) -> int:
    store, results, grid = _STATE["state"]
    return build(store, results, grid, Digest(key))
