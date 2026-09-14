"""JSON fetching and safe URL construction, independent of routing."""

import httpx
import pytest
from fastapi import HTTPException

from biosim_server.common.upstream import fetch_upstream_json, upstream_url


@pytest.mark.parametrize(("segment", "encoded"), [
    ("a/b", "a%2Fb"), ("a%b", "a%25b"), ("a b", "a%20b"),
    ("a#b", "a%23b"), ("a?b", "a%3Fb"), ("...", "..."), ("a.b", "a.b"),
])
def test_independent_segment_encoding(segment: str, encoded: str) -> None:
    assert upstream_url("runs", segment, "summary") == f"/runs/{encoded}/summary"


@pytest.mark.parametrize("segment", [".", ".."])
def test_dot_segments_rejected(segment: str) -> None:
    with pytest.raises(HTTPException) as error:
        upstream_url("runs", segment, "summary")
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected"), [
    (201, 502), (202, 502), (204, 502), (301, 502), (302, 502), (304, 502),
    (400, 400), (401, 401), (403, 403), (404, 404), (429, 429), (500, 502), (503, 502),
])
async def test_status_translation(status: int, expected: int) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"secret": "internal"})),
        base_url="https://upstream.test",
    ) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json(client, "/summary", resource="run summary")
    assert error.value.status_code == expected
    assert "internal" not in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"invalid json", b"<html>secret</html>", b"[]", b"null", b'"text"'])
async def test_invalid_json_or_non_object_is_502(body: bytes) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        base_url="https://upstream.test",
    ) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json(client, "/summary", resource="run summary")
    assert error.value.status_code == 502
    assert error.value.detail == "The upstream service returned an unexpected run summary."


@pytest.mark.asyncio
@pytest.mark.parametrize(("exception", "status"), [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
async def test_transport_errors(exception: type[httpx.RequestError], status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception("internal secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json(client, "/summary", resource="run summary")
    assert error.value.status_code == status
    assert "internal" not in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"value": 1}, [], [{"value": 1}]])
async def test_object_and_array_helpers(body: object) -> None:
    from biosim_server.common.upstream import fetch_upstream_json_value

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        base_url="https://upstream.test",
    ) as client:
        assert await fetch_upstream_json_value(client, "/resource", resource="resource") == body
        if isinstance(body, dict):
            assert await fetch_upstream_json(client, "/resource", resource="resource") == body
        else:
            with pytest.raises(HTTPException) as error:
                await fetch_upstream_json(client, "/resource", resource="resource")
            assert error.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"invalid", b"null", b'"text"', b"1", b"true"])
async def test_value_helper_rejects_scalars_and_invalid_json(body: bytes) -> None:
    from biosim_server.common.upstream import fetch_upstream_json_value

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        base_url="https://upstream.test",
    ) as client:
        with pytest.raises(HTTPException) as error:
            await fetch_upstream_json_value(client, "/resource", resource="resource")
    assert error.value.status_code == 502
