from __future__ import annotations

import pytest
from pydantic import BaseModel

from tape.core.hashing import canonical, digest
from tape.core.types import Timeframe, ticker


class Spec(BaseModel):
    window: int
    scale: float


def test_canonical_ignores_key_order() -> None:
    assert canonical({"b": 1, "a": [1.5, None]}) == canonical({"a": [1.5, None], "b": 1})
    assert canonical({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_digest_is_content_identity() -> None:
    assert digest(Spec(window=20, scale=0.5)) == digest({"scale": 0.5, "window": 20})
    assert digest(Spec(window=20, scale=0.5)) != digest(Spec(window=21, scale=0.5))
    assert len(digest({})) == 32


def test_nan_has_no_canonical_form() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        canonical({"x": float("nan")})


@pytest.mark.parametrize("symbol", ["AAPL", "F", "BRK.B", "BF.B", "GOOGL"])
def test_ticker_accepts(symbol: str) -> None:
    assert ticker(symbol) == symbol


@pytest.mark.parametrize("symbol", ["aapl", "", "BRK-B", "TOOLONGX", "1ABC", "BRK.BB"])
def test_ticker_rejects(symbol: str) -> None:
    with pytest.raises(ValueError, match="invalid ticker"):
        ticker(symbol)


def test_timeframe_values() -> None:
    assert [tf.value for tf in Timeframe] == ["1m", "5m", "15m", "1h", "4h", "1d"]
