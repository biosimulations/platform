"""Failure isolation and latency bounds for the Auth0 token/Management client.

AUTH-MAJ-005 and AUTH-MIN-004. The behaviours pinned here:

  * a failed token refresh is *shared* for a bounded cooldown instead of every
    waiter repeating the same POST (N concurrent cold callers used to issue N
    serialized token requests during an outage);
  * a malformed 200 from the token endpoint cannot populate the cache, and is a
    classified failure rather than a bare ``KeyError``;
  * the operation budget covers lock wait, token acquisition and the resource
    call, including a request that has already started (a prepared check before
    sleeping is not a deadline);
  * one pooled transport is reused across calls and released at shutdown.

``httpx.MockTransport`` with local scripts, no network, no credentials, no real
sleeps. Socket reuse itself cannot be proven with a mock transport; reuse is
asserted structurally (one client instance across calls).
"""

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common.auth.auth0_management import (
    Auth0ManagementRateLimited,
    Auth0ManagementUnavailable,
    create_password_change_ticket,
    get_auth0_user,
)

TICKET = "https://test-tenant.us.auth0.com/u/reset-verify?ticket=abc123"
_TRANSPORT_ERROR = "__transport_error__"


def _settings(**overrides: Any) -> SimpleNamespace:
    auth0 = SimpleNamespace(
        domain="test-tenant.us.auth0.com",
        management_client_id="mgmt-id",
        management_client_secret="mgmt-secret",
        password_reset_client_id="spa-id",
    )
    for key, value in overrides.items():
        setattr(auth0, key, value)
    return SimpleNamespace(auth0=auth0)


@pytest.fixture
def patch_client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stub settings and install a scripted transport over ``httpx.AsyncClient``."""
    monkeypatch.setattr(mgmt, "get_settings", lambda: _settings())
    sleep_mock = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)
    real_client = httpx.AsyncClient
    calls: list[httpx.AsyncClient] = []

    def install(handler: Any) -> None:
        transport = httpx.MockTransport(handler)

        def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            client = real_client(*args, **kwargs)  # type: ignore[arg-type]
            calls.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

    return SimpleNamespace(install=install, sleep=sleep_mock, clients=calls)


class _Endpoint:
    """Routes /oauth/token, JWKS-less resource calls and the ticket endpoint."""

    def __init__(self, *, token_response: Any = None, resource: Any = None, stall: bool = False) -> None:
        self.token_response = (
            httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            if token_response is None
            else token_response
        )
        self.resource = resource if resource is not None else httpx.Response(200, json={"name": "Jane"})
        # A never-set event rather than a sleep: the test stubs `asyncio.sleep`
        # (to keep the retry backoff instant), so a sleep-based delay would not
        # actually hold the request open. This models a stalled body stream.
        self.stall = stall
        self.token_posts = 0
        self.resource_calls = 0
        self.ticket_posts = 0

    async def _stall_if_asked(self) -> None:
        if self.stall:
            await asyncio.Event().wait()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            if self.token_response == _TRANSPORT_ERROR:
                raise httpx.ConnectError("simulated outage", request=request)
            return self.token_response
        if request.url.path == "/api/v2/tickets/password-change":
            self.ticket_posts += 1
            await self._stall_if_asked()
            if self.resource == _TRANSPORT_ERROR:
                raise httpx.ConnectError("simulated outage", request=request)
            return self.resource if isinstance(self.resource, httpx.Response) else httpx.Response(201, json={"ticket": TICKET})
        self.resource_calls += 1
        await self._stall_if_asked()
        if self.resource == _TRANSPORT_ERROR:
            raise httpx.ConnectError("simulated outage", request=request)
        return self.resource if isinstance(self.resource, httpx.Response) else httpx.Response(200, json=self.resource)


@pytest.mark.asyncio
async def test_concurrent_cold_failure_issues_one_token_post(patch_client: SimpleNamespace) -> None:
    """Five simultaneous cold callers must not become five serialized POSTs."""
    endpoint = _Endpoint(token_response=_TRANSPORT_ERROR)
    patch_client.install(endpoint)

    results = await asyncio.gather(
        *(get_auth0_user("auth0|abc") for _ in range(5)), return_exceptions=True
    )

    assert endpoint.token_posts == 1
    assert all(isinstance(result, Auth0ManagementUnavailable) for result in results)


@pytest.mark.asyncio
async def test_concurrent_cold_success_issues_one_token_post(patch_client: SimpleNamespace) -> None:
    endpoint = _Endpoint()
    patch_client.install(endpoint)

    results = await asyncio.gather(
        *(get_auth0_user("auth0|abc") for _ in range(5)), return_exceptions=True
    )

    assert endpoint.token_posts == 1
    assert all(result == {"name": "Jane"} for result in results)


@pytest.mark.asyncio
async def test_failed_refresh_is_not_repeated_inside_the_cooldown(
    patch_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _Endpoint(token_response=_TRANSPORT_ERROR)
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    # A later caller inside the cooldown shares that failure without any request.
    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    assert endpoint.token_posts == 1

    # Past the window, the next caller tries again -- the failure is not cached
    # as permanent.
    monkeypatch.setattr(mgmt, "_token_refresh_failed_until", 0.0)
    endpoint.token_response = httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert endpoint.token_posts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"access_token": "tok"}, {"access_token": "tok", "expires_in": "soon"},
     {"access_token": "tok", "expires_in": 0}, {"access_token": "", "expires_in": 60}, []],
    ids=["empty", "no-expires", "string-expires", "zero-expires", "empty-token", "not-an-object"],
)
async def test_malformed_token_response_is_classified_and_leaves_the_cache_empty(
    patch_client: SimpleNamespace, body: Any,
) -> None:
    """A 200 with an unexpected body is a classified failure, never a KeyError."""
    endpoint = _Endpoint(token_response=httpx.Response(200, json=body))
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    assert mgmt._token_cache["access_token"] is None


@pytest.mark.asyncio
async def test_token_endpoint_rate_limit_is_distinguishable(patch_client: SimpleNamespace) -> None:
    """Auth0 throttling the token endpoint is a retryable 503, not a 502."""
    endpoint = _Endpoint(token_response=httpx.Response(429, json={"message": "slow down"}))
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementRateLimited):
        await get_auth0_user("auth0|abc")


@pytest.mark.asyncio
async def test_operation_deadline_bounds_an_in_flight_request(
    patch_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is enforced *around* the request, not merely before sleeping."""
    endpoint = _Endpoint(stall=True)
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.1)

    started = time.monotonic()
    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "the request outlived its operation budget"
    assert endpoint.resource_calls == 1, "an exhausted budget must not start a retry"


@pytest.mark.asyncio
async def test_operation_deadline_covers_lock_wait(
    patch_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting behind another refresh counts against the same budget."""
    patch_client.install(_Endpoint())
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.1)
    await mgmt._token_refresh_lock.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(Auth0ManagementUnavailable):
            await get_auth0_user("auth0|abc")
        assert time.monotonic() - started < 0.5
    finally:
        mgmt._token_refresh_lock.release()
    # The failed waiter never acquired the lock, so it is usable straight after.
    assert mgmt._token_refresh_lock.locked() is False


@pytest.mark.asyncio
async def test_cancellation_while_waiting_leaves_the_lock_usable(
    patch_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_client.install(_Endpoint())
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 5.0)
    await mgmt._token_refresh_lock.acquire()
    try:
        task = asyncio.create_task(get_auth0_user("auth0|abc"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        mgmt._token_refresh_lock.release()

    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 5.0)
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}


@pytest.mark.asyncio
async def test_ticket_issuance_is_single_attempt_and_bounded(
    patch_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out ticket POST is never repeated: it may already have minted one."""
    endpoint = _Endpoint(stall=True, resource=httpx.Response(201, json={"ticket": TICKET}))
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.1)

    with pytest.raises(Auth0ManagementUnavailable):
        await create_password_change_ticket("auth0|abc")
    assert endpoint.ticket_posts == 1


@pytest.mark.asyncio
async def test_one_pooled_client_is_reused_and_released(patch_client: SimpleNamespace) -> None:
    """AUTH-MIN-004: the transport is lifecycle-owned, not per-call."""
    endpoint = _Endpoint(resource=httpx.Response(201, json={"ticket": TICKET}))
    patch_client.install(endpoint)

    client = mgmt.get_auth0_http_client()
    assert mgmt.get_auth0_http_client() is client

    assert await create_password_change_ticket("auth0|abc") == TICKET
    assert await create_password_change_ticket("auth0|abc") == TICKET
    assert endpoint.ticket_posts == 2
    assert len(patch_client.clients) == 1, "a client was constructed per call"

    await mgmt.close_auth0_http_client()
    assert mgmt._http_client is None
    assert client.is_closed
