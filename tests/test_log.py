from __future__ import annotations

import io
import logging
import re
from typing import TYPE_CHECKING

import pytest

from tape import log

if TYPE_CHECKING:
    from collections.abc import Iterator

LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z \[(?P<level>[A-Z]+)\] (?P<rest>.*)$")


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    out = io.StringIO()
    log.setup("DEBUG", stream=out, colour=False)
    yield out
    logging.getLogger().handlers.clear()


def lines(stream: io.StringIO) -> list[str]:
    return stream.getvalue().splitlines()


def test_line_format(stream: io.StringIO) -> None:
    log.Log("Catalog").info("built", ticker="AAPL", bars=780_000)
    [line] = lines(stream)
    m = LINE.match(line)
    assert m is not None
    assert m["level"] == "INFO"
    assert m["rest"] == "Catalog: built ticker=AAPL bars=780000"


def test_level_names(stream: io.StringIO) -> None:
    out = log.Log("X")
    out.debug("d")
    out.info("i")
    out.warn("w")
    out.error("e")
    assert [LINE.match(x)["level"] for x in lines(stream)] == ["DEBUG", "INFO", "WARN", "ERROR"]  # type: ignore[index]


def test_level_filter() -> None:
    out = io.StringIO()
    log.setup("WARN", stream=out, colour=False)
    log.Log("X").info("hidden")
    log.Log("X").warn("shown")
    assert [x.endswith("X: shown") for x in lines(out)] == [True]
    logging.getLogger().handlers.clear()


def test_exception_has_traceback(stream: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        log.Log("X").exception("failed", job="k1")
    text = stream.getvalue()
    assert "[ERROR] X: failed job=k1" in text
    assert "ValueError: boom" in text


def test_colour_only_when_enabled() -> None:
    out = io.StringIO()
    log.setup("INFO", stream=out, colour=True)
    log.Log("X").info("m")
    assert "\033[1;97m" in out.getvalue()
    logging.getLogger().handlers.clear()


def test_stdlib_loggers_share_format(stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("port %d busy", 8000)
    [line] = lines(stream)
    assert line.endswith("[WARN] uvicorn.error: port 8000 busy")


def test_timestamp_is_utc_nanoseconds() -> None:
    assert log.timestamp(1_700_000_000_123_456_789) == "2023-11-14T22:13:20.123456789Z"
