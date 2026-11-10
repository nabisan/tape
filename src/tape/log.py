from __future__ import annotations

import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import TextIO

type Level = Literal["DEBUG", "INFO", "WARN", "ERROR"]

_LEVELS: Final[Mapping[Level, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}
_NAMES: Final = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "FATAL",
}
_COLOURS: Final = {
    logging.DEBUG: "\033[34m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[1;31m",
    logging.CRITICAL: "\033[1;97;41m",
}
_TS: Final = "\033[1;97m"
_COMPONENT: Final = "\033[36m"
_RESET: Final = "\033[0m"

_base_factory = logging.getLogRecordFactory()


def _stamped(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _base_factory(*args, **kwargs)
    record.ts_ns = time.time_ns()
    return record


def timestamp(ns: int) -> str:
    whole = datetime.fromtimestamp(ns // 1_000_000_000, UTC)
    return f"{whole:%Y-%m-%dT%H:%M:%S}.{ns % 1_000_000_000:09d}Z"


class Formatter(logging.Formatter):
    def __init__(self, colour: bool) -> None:
        super().__init__()
        self._colour = colour

    def format(self, record: logging.LogRecord) -> str:
        ns: int = getattr(record, "ts_ns", None) or int(record.created * 1e9)
        level = _NAMES.get(record.levelno, record.levelname)
        fields: Mapping[str, object] = getattr(record, "fields", {})
        tail = "".join(f" {k}={v}" for k, v in fields.items())
        if self._colour:
            line = (
                f"{_TS}{timestamp(ns)}{_RESET} {_COLOURS.get(record.levelno, '')}[{level}]{_RESET} "
                f"{_COMPONENT}{record.name}{_RESET}: {record.getMessage()}{tail}"
            )
        else:
            line = f"{timestamp(ns)} [{level}] {record.name}: {record.getMessage()}{tail}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def setup(level: Level = "INFO", stream: TextIO | None = None, colour: bool | None = None) -> None:
    out = stream if stream is not None else sys.stderr
    if colour is None:
        colour = out.isatty() and "NO_COLOR" not in os.environ
    handler = logging.StreamHandler(out)
    handler.setFormatter(Formatter(colour))
    logging.setLogRecordFactory(_stamped)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(_LEVELS[level])
    logging.captureWarnings(True)


class Log:
    __slots__ = ("_logger",)

    def __init__(self, component: str) -> None:
        self._logger = logging.getLogger(component)

    def debug(self, msg: str, **fields: object) -> None:
        self._emit(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: object) -> None:
        self._emit(logging.INFO, msg, fields)

    def warn(self, msg: str, **fields: object) -> None:
        self._emit(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: object) -> None:
        self._emit(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields: object) -> None:
        self._emit(logging.ERROR, msg, fields, exc_info=True)

    def _emit(self, level: int, msg: str, fields: Mapping[str, object], exc_info: bool = False) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, msg, extra={"fields": fields}, exc_info=exc_info, stacklevel=3)
