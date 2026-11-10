from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from tape.log import Level

if TYPE_CHECKING:
    from collections.abc import Mapping


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_dir: Path
    store_dir: Path
    results_dir: Path
    log_level: Level = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        home = Path(env.get("TAPE_HOME", "~/tape")).expanduser()
        return cls.model_validate(
            {
                "source_dir": Path(env.get("TAPE_SOURCE_DIR", home / "source")).expanduser(),
                "store_dir": Path(env.get("TAPE_STORE_DIR", home / "store")).expanduser(),
                "results_dir": Path(env.get("TAPE_RESULTS_DIR", home / "results")).expanduser(),
                "log_level": env.get("TAPE_LOG_LEVEL", "INFO"),
            }
        )
