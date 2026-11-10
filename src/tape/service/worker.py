from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from tape import log
from tape.catalog.store import Store
from tape.core.hashing import Digest
from tape.runner import RunSpec, execute
from tape.service.results import ResultStore

if TYPE_CHECKING:
    from tape.log import Level

_STATE: Final[dict[str, tuple[Store, ResultStore]]] = {}


def init_worker(store_dir: str, data_version: str, results_dir: str, level: Level) -> None:
    log.setup(level)
    _STATE["state"] = (Store.open(Path(store_dir), Digest(data_version)), ResultStore(Path(results_dir)))


def execute_run(payload: str) -> Digest:
    store, results = _STATE["state"]
    result = execute(store, RunSpec.model_validate_json(payload))
    results.save(result)
    return result.key
