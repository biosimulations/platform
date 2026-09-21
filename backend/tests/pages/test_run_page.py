"""Mounted page contract, request isolation, failure policy and concurrency."""

import asyncio

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.dependencies import get_http_client
from tests.pages.test_mapping import satellite
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
RESOURCES = ['files', 'specifications', 'logs']
PAGE = "/runs/example/page"


async def test_success_parallel_requests_and_isolation() -> None:
    seen: list[httpx.Request] = []
    started: set[str] = set()
    barrier = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            assert len(seen) == 1
            return httpx.Response(200, json=payload("run"))
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            barrier.set()
        # Serial fetching cannot reach this barrier and fails deterministically.
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return httpx.Response(200, json=satellite(resource))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE + "?includeData=true&x=1&x=2", headers={
                "Authorization": "Bearer secret", "Cookie": "session=secret", "X-Caller": "secret",
            })
    assert response.status_code == 200, response.text
    owned = response.json()
    assert set(owned) == {'files', 'info', 'summary', 'logs', 'specifications'}
    assert len(seen) == 4
    expected_id = "example"
    assert [request.url.path for request in seen] == [
        "/runs/example/summary", *[f"/{resource}/{expected_id}" for resource in RESOURCES],
    ]
    for request in seen:
        assert request.url.host == "upstream.test"
        assert request.url.query == b""
        for header in ["authorization", "cookie", "x-caller"]:
            assert header not in request.headers
    assert set(owned["files"][0]) == {"format", "location", "size", "url"}
    assert owned["specifications"][0]["outputs"][1]["dataSets"][0]["label"] == "Value"
    assert owned["logs"]["sedDocuments"][0]["outputs"][0]["dataSets"][0]["id"] == "dataset"


@pytest.mark.parametrize("resource", ["identity", *RESOURCES])
@pytest.mark.parametrize("failure,expected", [(404, 404), (500, 502), (503, 502), ("timeout", 504), ("invalid", 502), ("drift", 502)])
async def test_failure_policy(resource: str, failure: int | str, expected: int) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        current = "identity" if request.url.path.endswith("/summary") else request.url.path.split("/")[1]
        if current == resource:
            if failure == "timeout":
                raise httpx.ReadTimeout("secret", request=request)
            if failure == "invalid":
                return httpx.Response(200, text="secret invalid JSON")
            if failure == "drift":
                return httpx.Response(200, json={"secret": "unrelated"})
            assert isinstance(failure, int)
            return httpx.Response(failure, text="secret")
        return httpx.Response(200, json=payload("run") if current == "identity" else satellite(current))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    if failure == 404 and resource != "identity":
        assert response.status_code == 200, response.text
        assert response.json()[resource] == (None if resource == "logs" else [])
    else:
        assert response.status_code == expected, response.text
        assert "secret" not in response.text
    if resource == "identity":
        assert len(seen) == 1


@pytest.mark.parametrize("segment", ["%2E", "%2E%2E", ".%2E", "..%2Fruns%2Fsecret"])
async def test_dot_segments_and_slashes_rejected(segment: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(f"/runs/{segment}/page")
    assert response.status_code == 404
    assert seen == []


async def test_all_satellites_404_returns_empty_collections() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("run"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 200, response.text
    assert len(seen) == 4
    owned = response.json()
    assert owned["files"] == []
    assert owned["specifications"] == []
    assert owned["logs"] is None


@pytest.mark.parametrize("segment", ["a%25b", "a%20b", "a%23b", "a%3Fb", "a.b", "..."])
async def test_path_encoding(segment: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("run"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(f"/runs/{segment}/page")
    assert response.status_code == 200, response.text
    assert len(seen) == 4
    assert seen[0].url.raw_path == f"/runs/{segment}/summary".encode()
    assert [request.url.raw_path for request in seen[1:]] == [f"/{resource}/{segment}".encode() for resource in RESOURCES]
