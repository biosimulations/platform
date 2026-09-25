"""Mounted page contract, request isolation, failure policy and concurrency."""

import asyncio
import time
from typing import NoReturn

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.common.upstream import UPSTREAM_TIMEOUT_SECONDS
from biosim_server.dependencies import get_http_client
from biosim_server.pages import service
from tests.pages.test_mapping import satellite
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
RESOURCES = ['files', 'specifications', 'logs']
PAGE = "/runs/example/page"


async def hang_until_cancelled(name: str, cancelled: set[str]) -> NoReturn:
    """Stand in for an upstream call that never answers; record when it is cancelled."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancelled.add(name)
        raise
    raise AssertionError("unreachable: the event is never set")


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
        # Satellites start alongside identity, so every request is issued even when
        # identity fails; with these instant mocks they finish before it is read.
        # Cancellation of satellites still in flight is proven separately below.
        assert len(seen) == 4


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


async def test_run_page_satellites_start_before_identity_completes() -> None:
    """Regression guard: satellite requests reach upstream while identity is unanswered.

    Identity answers only once all three satellites have arrived. A serial
    identity-then-satellites implementation never issues them, so the bounded wait
    expires and the page fails instead of returning 200 -- it cannot hang the suite.
    """
    started: set[str] = set()
    satellites_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            await asyncio.wait_for(satellites_started.wait(), timeout=2)
            return httpx.Response(200, json=payload("run"))
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            satellites_started.set()
        return httpx.Response(200, json=satellite(resource))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 200, response.text
    assert started == set(RESOURCES)


@pytest.mark.parametrize("failure,expected", [(404, 404), (500, 502), ("timeout", 504), ("drift", 502)])
async def test_run_page_identity_failure_cancels_satellites(failure: int | str, expected: int) -> None:
    """An identity failure, including drift found only at parse time, cancels in-flight satellites."""
    started: set[str] = set()
    cancelled: set[str] = set()
    satellites_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            # Fail only once every satellite request is waiting on upstream.
            await asyncio.wait_for(satellites_started.wait(), timeout=2)
            if failure == "timeout":
                raise httpx.ReadTimeout("secret", request=request)
            if failure == "drift":
                return httpx.Response(200, json={"secret": "unrelated"})
            assert isinstance(failure, int)
            return httpx.Response(failure, text="secret")
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            satellites_started.set()
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == expected, response.text
    assert "secret" not in response.text
    assert cancelled == set(RESOURCES)


async def test_run_page_satellite_failure_cancels_siblings() -> None:
    """A failed satellite fails the page with 502 and cancels the satellites still in flight."""
    started: set[str] = set()
    cancelled: set[str] = set()
    satellites_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("run"))
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            satellites_started.set()
        if resource == "files":
            await asyncio.wait_for(satellites_started.wait(), timeout=2)
            return httpx.Response(500, text="secret")
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 502, response.text
    assert "secret" not in response.text
    assert cancelled == {"specifications", "logs"}


async def test_run_page_budget_is_derived_from_the_upstream_serial_depth() -> None:
    """The budget must be able to fire, and must not be dead code above the real cost.

    Every upstream call is bounded *per phase* by the shared client's httpx timeout,
    so the run page (one serial hop: identity ∥ satellites) cannot take less than
    one timeout and cannot legitimately need two. A budget under the serial depth
    would cancel healthy requests; the old flat 60 s was above anything the
    implementation could reach, so it could never fire.
    """
    budget = service._PAGE_TIMEOUTS["run"]
    assert UPSTREAM_TIMEOUT_SECONDS < budget < 2 * UPSTREAM_TIMEOUT_SECONDS


@pytest.mark.parametrize("stalled", ["identity", "satellites"])
async def test_run_page_total_timeout(stalled: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exceeding the page budget returns 504 and cancels every upstream call still in flight."""
    monkeypatch.setitem(service._PAGE_TIMEOUTS, "run", 0.1)
    cancelled: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            if stalled == "satellites":
                return httpx.Response(200, json=payload("run"))
            await hang_until_cancelled("identity", cancelled)
        await hang_until_cancelled(request.url.path.split("/")[1], cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == 504, response.text
    assert response.json() == {"detail": "Timed out while loading the run page."}
    assert cancelled == set(RESOURCES) | ({"identity"} if stalled == "identity" else set())


async def test_run_page_critical_path_is_max_not_sum() -> None:
    """Benchmark guard: the run page costs one upstream round trip, not two.

    Every upstream call takes ``delay``. Identity-then-satellites cannot finish in
    under ``2 * delay`` because asyncio.sleep never returns early, so that is the
    regression threshold; concurrent assembly finishes in about ``delay``, leaving a
    full ``delay`` of slack for local overhead. Ordering itself is proven without a
    clock by test_run_page_satellites_start_before_identity_completes.
    """
    delay = 0.2

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("run"))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            start = time.monotonic()
            response = await caller.get(PAGE)
            elapsed = time.monotonic() - start
    assert response.status_code == 200, response.text
    assert delay <= elapsed < 2 * delay, f"run page took {elapsed * 1000:.0f}ms with {delay * 1000:.0f}ms upstream calls"


@pytest.mark.parametrize("failure,expected", [(404, 404), (500, 502), ("timeout", 504)])
async def test_run_page_identity_error_precedes_an_earlier_satellite_failure(failure: int | str, expected: int) -> None:
    """Mixed failure (RUN-MIN-001): identity's error wins even when a satellite failed first.

    Identity is awaited before satellite results are consumed, so an identity
    404/502/504 is the response even though ``files`` had already failed. That is
    the deliberate "identity error wins" semantics a fail-fast refactor would
    silently change, so it is pinned here rather than left to the single-resource
    failure tests.
    """
    started: set[str] = set()
    cancelled: set[str] = set()
    files_failed = asyncio.Event()
    all_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            # Answer only once the satellite failure has already happened.
            await asyncio.wait_for(files_failed.wait(), timeout=2)
            if failure == "timeout":
                raise httpx.ReadTimeout("secret", request=request)
            assert isinstance(failure, int)
            return httpx.Response(failure, text="secret")
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            all_started.set()
        if resource == "files":
            await asyncio.wait_for(all_started.wait(), timeout=2)
            files_failed.set()
            return httpx.Response(500, text="secret")
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            response = await caller.get(PAGE)
    assert response.status_code == expected, response.text
    assert "secret" not in response.text
    assert cancelled == {"specifications", "logs"}


async def test_run_page_caller_cancellation_drains_upstream_calls() -> None:
    """Caller cancellation propagates and every in-flight upstream call is drained.

    A disconnect must not leave identity or a satellite running after the request
    is gone. Asserts the propagated CancelledError and that all four calls were
    cancelled, without an unhandled-task warning.
    """
    started: set[str] = set()
    cancelled: set[str] = set()
    all_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            started.add("identity")
            if started == set(RESOURCES) | {"identity"}:
                all_started.set()
            await hang_until_cancelled("identity", cancelled)
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES) | {"identity"}:
            all_started.set()
        await hang_until_cancelled(resource, cancelled)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        task = asyncio.create_task(service.assemble_run_page(upstream, "example"))
        await asyncio.wait_for(all_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert started == set(RESOURCES) | {"identity"}
    assert cancelled == set(RESOURCES) | {"identity"}
