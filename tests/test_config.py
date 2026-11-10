from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tape.config import Settings


def test_defaults_under_home() -> None:
    s = Settings.from_env({"TAPE_HOME": "/srv/tape"})
    assert s.source_dir == Path("/srv/tape/source")
    assert s.store_dir == Path("/srv/tape/store")
    assert s.results_dir == Path("/srv/tape/results")
    assert s.log_level == "INFO"


def test_explicit_paths_override_home() -> None:
    s = Settings.from_env(
        {"TAPE_HOME": "/srv/tape", "TAPE_SOURCE_DIR": "/data/1m", "TAPE_LOG_LEVEL": "DEBUG"}
    )
    assert s.source_dir == Path("/data/1m")
    assert s.store_dir == Path("/srv/tape/store")
    assert s.log_level == "DEBUG"


def test_unknown_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env({"TAPE_LOG_LEVEL": "VERBOSE"})


def test_frozen() -> None:
    s = Settings.from_env({})
    with pytest.raises(ValidationError):
        s.log_level = "DEBUG"  # type: ignore[misc]
