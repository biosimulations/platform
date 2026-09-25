"""Mounted page contract, request isolation, failure policy and concurrency."""

import asyncio

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.common.upstream import UPSTREAM_TIMEOUT_SECONDS
from biosim_server.dependencies import get_http_client
from biosim_server.pages import service
from tests.pages.test_mapping import satellite
from tests.pages.test_run_page import hang_until_cancelled
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
RESOURCES = ['files', 'specifications']
PAGE = "/projects/example/page"


async def test_success_parallel_requests_and_isolation() -> None:
    seen: list[httpx.Request] = []
    started: set[str] = set()
    barrier = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            assert len(seen) == 1
            return httpx.Response(200, json=payload("project"))
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
    assert set(owned) == {'project', 'files', 'simulationRun', 'specifications'}
    assert len(seen) == 3
    expected_id = payload()["id"]
    assert [request.url.path for request in seen] == [
        "/projects/example/summary", *[f"/{resource}/{expected_id}" for resource in RESOURCES],
    ]
    for request in seen:
        assert request.url.host == "upstream.test"
        assert request.url.query == b""
        for header in ["authorization", "cookie", "x-caller"]:
            assert header not in request.headers
    assert set(owned["files"][0]) == {"format", "location", "size", "url"}
    assert owned["specifications"][0]["outputs"][1]["dataSets"][0]["label"] == "Value"
    assert owned["simulationRun"]["modelFormats"] == ["SBML", "CELLML"]


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
        return httpx.Response(200, json=payload("project") if current == "identity" else satellite(current))

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
            response = await caller.get(f"/projects/{segment}/page")
    assert response.status_code == 404
    assert seen == []


@pytest.mark.parametrize("segment", ["a%25b", "a%20b", "a%23b", "a%3Fb", "a.b", "..."])
async def test_path_encoding(segment: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("project"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(f"/projects/{segment}/page")
    assert response.status_code == 200, response.text
    assert len(seen) == 3
    assert seen[0].url.raw_path == f"/projects/{segment}/summary".encode()
    assert all(request.url.raw_path.endswith(b"/" + payload()["id"].encode()) for request in seen[1:])


@pytest.mark.parametrize("run_id,encoded", [("a/b?x=1", b"a%2Fb%3Fx%3D1"), ("a%b", b"a%25b")])
async def test_embedded_run_id_is_encoded(run_id: str, encoded: bytes) -> None:
    raw = payload("project")
    raw["simulationRun"]["id"] = run_id
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=raw) if len(seen) == 1 else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 200
    assert [request.url.raw_path for request in seen[1:]] == [
        b"/files/" + encoded, b"/specifications/" + encoded,
    ]


async def test_all_satellites_404_returns_empty_collections() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("project"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 200, response.text
    assert len(seen) == 3
    owned = response.json()
    assert owned["files"] == []
    assert owned["specifications"] == []


@pytest.mark.parametrize("run_id", [".", ".."])
async def test_embedded_dot_run_id_is_rejected(run_id: str) -> None:
    raw = payload("project")
    raw["simulationRun"]["id"] = run_id
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 404
    assert len(seen) == 1


async def test_project_page_budget_is_derived_from_the_upstream_serial_depth() -> None:
    """Two serial hops (identity → run id → satellites) plus bounded local slack.

    Each upstream call is bounded per phase by the shared client's httpx timeout, so
    a budget below two timeouts would cancel healthy requests and one far above it
    would never fire. Pinning the derivation here keeps the constant and the
    assembler it bounds from drifting apart silently.
    """
    budget = service._PAGE_TIMEOUTS["project"]
    assert 2 * UPSTREAM_TIMEOUT_SECONDS < budget < 3 * UPSTREAM_TIMEOUT_SECONDS


@pytest.mark.parametrize("stalled", ["identity", "satellites"])
async def test_project_page_total_timeout(stalled: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exceeding the page budget returns 504 and cancels every upstream call still in flight."""
    monkeypatch.setitem(service._PAGE_TIMEOUTS, "project", 0.1)
    cancelled: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            if stalled == "satellites":
                return httpx.Response(200, json=payload("project"))
            await hang_until_cancelled("identity", cancelled)
        await hang_until_cancelled(request.url.path.split("/")[1], cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 504, response.text
    assert response.json() == {"detail": "Timed out while loading the project page."}
    # Project satellites need the run id from identity, so a stalled identity starts none.
    assert cancelled == ({"identity"} if stalled == "identity" else set(RESOURCES))


async def test_project_page_identity_barrier_holds_satellites() -> None:
    """Current implementation: no satellite is issued until identity answers.

    Unlike the run page, this ordering is a hard dependency (the satellite paths
    embed the run id from the identity response), so it is asserted on the live
    assembler rather than only on the pre-change baseline in test_phase0_baseline.
    """
    seen: list[str] = []
    identity_pending = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/summary"):
            identity_pending.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            return httpx.Response(200, json=payload("project"))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            request_task = asyncio.create_task(caller.get(PAGE))
            await asyncio.wait_for(identity_pending.wait(), timeout=2)
            for _ in range(10):
                await asyncio.sleep(0)
            assert seen == ["/projects/example/summary"], "a satellite was issued before identity answered"
            release.set()
            response = await request_task
    assert response.status_code == 200, response.text
    expected_id = payload()["id"]
    assert seen == ["/projects/example/summary", *[f"/{resource}/{expected_id}" for resource in RESOURCES]]


async def test_project_page_failed_satellite_cancels_sibling() -> None:
    """A failed satellite fails the page with 502 and cancels the sibling still in flight."""
    started: set[str] = set()
    cancelled: set[str] = set()
    both_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("project"))
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            both_started.set()
        if resource == "files":
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return httpx.Response(500, text="secret")
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 502, response.text
    assert "secret" not in response.text
    assert cancelled == {"specifications"}


async def test_project_page_caller_cancellation_drains_upstream_calls() -> None:
    """Caller cancellation propagates and no upstream call outlives the request.

    A disconnect must not leave the sibling satellite running after the response
    is decided. Asserts the propagated CancelledError and that every in-flight
    child was drained, without an unhandled-task warning.
    """
    started: set[str] = set()
    cancelled: set[str] = set()
    both_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("project"))
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            both_started.set()
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        task = asyncio.create_task(service.assemble_project_page(upstream, "example"))
        await asyncio.wait_for(both_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert started == set(RESOURCES)
    assert cancelled == set(RESOURCES)
