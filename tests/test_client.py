from __future__ import annotations

import httpx2
import pytest

from tape.ui.client import ApiError, Client, GoneError, UnavailableError


def client(handler: object) -> Client:
    return Client("http://tape.test", transport=httpx2.MockTransport(handler))  # type: ignore[arg-type]


def test_timeouts_and_refusals_are_unavailable() -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(UnavailableError, match="did not answer"):
        client(slow).catalog()
    with pytest.raises(UnavailableError, match="unreachable"):
        client(refused).catalog()


def test_status_codes_map_to_typed_errors() -> None:
    gone = client(lambda r: httpx2.Response(410, json={"detail": "computed by an older engine"}))
    with pytest.raises(GoneError, match="older engine"):
        gone.catalog()
    broken = client(lambda r: httpx2.Response(500, json={"detail": "boom"}))
    with pytest.raises(ApiError, match="GET /catalog: 500 boom"):
        broken.catalog()


def test_unavailable_is_an_api_error() -> None:
    assert issubclass(UnavailableError, ApiError)
