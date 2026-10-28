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
    ResendOutcome,
    create_password_change_ticket,
    get_auth0_user,
    resend_auth0_verification_email,
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

    def __init__(
        self, *, token_response: Any = None, resource: Any = None, stall: bool = False
    ) -> None:
        self.token_response = (
            httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            if token_response is None
            else token_response
        )
        self.resource = (
            resource
            if resource is not None
            else httpx.Response(200, json={"name": "Jane"})
        )
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
            return (
                self.resource
                if isinstance(self.resource, httpx.Response)
                else httpx.Response(201, json={"ticket": TICKET})
            )
        self.resource_calls += 1
        await self._stall_if_asked()
        if self.resource == _TRANSPORT_ERROR:
            raise httpx.ConnectError("simulated outage", request=request)
        return (
            self.resource
            if isinstance(self.resource, httpx.Response)
            else httpx.Response(200, json=self.resource)
        )


@pytest.mark.asyncio
async def test_concurrent_cold_failure_issues_one_token_post(
    patch_client: SimpleNamespace,
) -> None:
    """Five simultaneous cold callers must not become five serialized POSTs."""
    endpoint = _Endpoint(token_response=_TRANSPORT_ERROR)
    patch_client.install(endpoint)

    results = await asyncio.gather(
        *(get_auth0_user("auth0|abc") for _ in range(5)), return_exceptions=True
    )

    assert endpoint.token_posts == 1
    assert all(isinstance(result, Auth0ManagementUnavailable) for result in results)


@pytest.mark.asyncio
async def test_concurrent_cold_success_issues_one_token_post(
    patch_client: SimpleNamespace,
) -> None:
    endpoint = _Endpoint()
    patch_client.install(endpoint)

    results = await asyncio.gather(
        *(get_auth0_user("auth0|abc") for _ in range(5)), return_exceptions=True
    )

    assert endpoint.token_posts == 1
    assert all(result == {"name": "Jane"} for result in results)


@pytest.mark.asyncio
async def test_failed_refresh_is_not_repeated_inside_the_cooldown(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
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
    endpoint.token_response = httpx.Response(
        200, json={"access_token": "token", "expires_in": 3600}
    )
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert endpoint.token_posts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"access_token": "tok"},
        {"access_token": "tok", "expires_in": "soon"},
        {"access_token": "tok", "expires_in": 0},
        {"access_token": "", "expires_in": 60},
        [],
    ],
    ids=[
        "empty",
        "no-expires",
        "string-expires",
        "zero-expires",
        "empty-token",
        "not-an-object",
    ],
)
async def test_malformed_token_response_is_classified_and_leaves_the_cache_empty(
    patch_client: SimpleNamespace,
    body: Any,
) -> None:
    """A 200 with an unexpected body is a classified failure, never a KeyError."""
    endpoint = _Endpoint(token_response=httpx.Response(200, json=body))
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    assert mgmt._token_cache["access_token"] is None


@pytest.mark.asyncio
async def test_token_endpoint_rate_limit_is_distinguishable(
    patch_client: SimpleNamespace,
) -> None:
    """Auth0 throttling the token endpoint is a retryable 503, not a 502."""
    endpoint = _Endpoint(
        token_response=httpx.Response(429, json={"message": "slow down"})
    )
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementRateLimited):
        await get_auth0_user("auth0|abc")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,advertised",
    [
        ({}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS),
        ({"Retry-After": "7.5"}, 8),
        ({"Retry-After": "20"}, 20),
        ({"Retry-After": "900"}, mgmt._MGMT_RETRY_AFTER_CEILING_SECONDS),
        ({"Retry-After": "soon"}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS),
        # Shorter than the shared cooldown: everyone is told the cooldown, the
        # moment the token endpoint will actually be tried again.
        ({"Retry-After": "1"}, int(mgmt._MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS)),
    ],
    ids=[
        "no-header",
        "fractional",
        "auth0-delay",
        "clamped",
        "malformed",
        "below-cooldown",
    ],
)
async def test_rate_limited_refresh_stays_rate_limited_inside_the_cooldown(
    patch_client: SimpleNamespace,
    headers: dict[str, str],
    advertised: int,
) -> None:
    """A shared 429 replays as a 429 with the same return time, not a 502.

    The first caller is told Auth0's own delay (or the fallback); callers turned
    away by the cooldown are told what is left of that same delay, and nobody
    re-posts to the token endpoint before it has passed.
    """
    endpoint = _Endpoint(
        token_response=httpx.Response(
            429, json={"message": "slow down"}, headers=headers
        )
    )
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementRateLimited) as first:
        await get_auth0_user("auth0|abc")
    with pytest.raises(Auth0ManagementRateLimited) as replayed:
        await get_auth0_user("auth0|abc")
    assert endpoint.token_posts == 1
    assert first.value.retry_after == advertised
    assert advertised - 1 <= replayed.value.retry_after <= advertised
    # The cooldown is never shorter than the delay callers were given.
    assert mgmt._token_refresh_failed_until - time.time() > advertised - 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,advertised",
    [({}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS), ({"Retry-After": "7"}, 7)],
    ids=["no-header", "auth0-delay"],
)
async def test_ticket_endpoint_rate_limit_carries_auth0s_delay(
    patch_client: SimpleNamespace,
    headers: dict[str, str],
    advertised: int,
) -> None:
    endpoint = _Endpoint(resource=httpx.Response(429, json={}, headers=headers))
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementRateLimited) as raised:
        await create_password_change_ticket("auth0|abc")
    assert raised.value.retry_after == advertised
    assert endpoint.ticket_posts == 1


@pytest.mark.asyncio
async def test_cooldown_failure_kind_follows_the_latest_failure(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 followed by an outage replays the outage, and success clears both."""
    endpoint = _Endpoint(
        token_response=httpx.Response(429, json={"message": "slow down"})
    )
    patch_client.install(endpoint)
    with pytest.raises(Auth0ManagementRateLimited):
        await get_auth0_user("auth0|abc")

    monkeypatch.setattr(mgmt, "_token_refresh_failed_until", 0.0)
    endpoint.token_response = _TRANSPORT_ERROR
    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    with pytest.raises(Auth0ManagementUnavailable) as replayed:
        await get_auth0_user("auth0|abc")
    assert not isinstance(replayed.value, Auth0ManagementRateLimited)
    assert endpoint.token_posts == 2

    monkeypatch.setattr(mgmt, "_token_refresh_failed_until", 0.0)
    endpoint.token_response = httpx.Response(
        200, json={"access_token": "token", "expires_in": 3600}
    )
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert mgmt._token_refresh_failure_rate_limited is False


@pytest.mark.asyncio
async def test_operation_deadline_bounds_an_in_flight_request(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
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
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
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


async def _yield_until_lock_has_a_waiter(lock: asyncio.Lock) -> None:
    """Run the loop until some task is queued on ``lock``.

    ``patch_client`` replaces ``asyncio.sleep`` with an ``AsyncMock``, so
    ``await asyncio.sleep(0)`` would return without yielding and a task created
    just before it would never have started. A loop-scheduled future yields for
    real. ``_waiters`` is CPython's private queue of pending ``acquire()`` calls;
    reading it is the only way to prove the cancellation lands *during* lock
    acquisition rather than before the task ever ran.
    """
    loop = asyncio.get_running_loop()
    for _ in range(100):
        if lock._waiters:
            return
        tick = loop.create_future()
        loop.call_soon(tick.set_result, None)
        await tick
    raise AssertionError("the task never queued on the refresh lock")


@pytest.mark.asyncio
async def test_cancellation_while_waiting_leaves_the_lock_usable(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_client.install(_Endpoint())
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 5.0)
    lock = mgmt._token_refresh_lock
    await lock.acquire()
    try:
        task = asyncio.create_task(get_auth0_user("auth0|abc"))
        await _yield_until_lock_has_a_waiter(lock)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The cancelled waiter left the queue rather than staying parked on it.
        assert not lock._waiters
    finally:
        lock.release()

    # Had the cancelled waiter been handed the lock, it would still be held here.
    assert lock.locked() is False
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}


@pytest.mark.asyncio
async def test_ticket_issuance_is_single_attempt_and_bounded(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out ticket POST is never repeated: it may already have minted one."""
    endpoint = _Endpoint(
        stall=True, resource=httpx.Response(201, json={"ticket": TICKET})
    )
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.1)

    with pytest.raises(Auth0ManagementUnavailable):
        await create_password_change_ticket("auth0|abc")
    assert endpoint.ticket_posts == 1


@pytest.mark.asyncio
async def test_one_pooled_client_is_reused_and_released(
    patch_client: SimpleNamespace,
) -> None:
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


# --- verification-email eligibility + one job attempt -----------------------

_RESEND_USER = {
    "user_id": "auth0|abc",
    "email": "person@example.test",
    "email_verified": False,
    "identities": [
        {"provider": "auth0", "connection": "Username-Password-Authentication"}
    ],
}
_RESEND_JOB = {"id": "job_1", "type": "verification_email", "status": "pending"}


class _ResendEndpoint:
    def __init__(
        self,
        *,
        user: Any = None,
        job: Any = None,
        stall_user: bool = False,
        stall_job: bool = False,
    ) -> None:
        self.user = _RESEND_USER if user is None else user
        self.job = _RESEND_JOB if job is None else job
        self.stall_user = stall_user
        self.stall_job = stall_job
        self.token_posts = 0
        self.user_gets = 0
        self.job_posts = 0
        self.user_started = asyncio.Event()
        self.job_started = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            return httpx.Response(
                200, json={"access_token": "token", "expires_in": 3600}
            )
        if request.url.path == "/api/v2/users/auth0|abc":
            self.user_gets += 1
            self.user_started.set()
            if self.stall_user:
                await asyncio.Event().wait()
            if self.user == _TRANSPORT_ERROR:
                raise httpx.ConnectError("simulated outage", request=request)
            return (
                self.user
                if isinstance(self.user, httpx.Response)
                else httpx.Response(200, json=self.user)
            )
        if request.url.path == "/api/v2/jobs/verification-email":
            self.job_posts += 1
            self.job_started.set()
            if self.stall_job:
                await asyncio.Event().wait()
            if self.job == _TRANSPORT_ERROR:
                raise httpx.ConnectError("simulated outage", request=request)
            return (
                self.job
                if isinstance(self.job, httpx.Response)
                else httpx.Response(201, json=self.job)
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.mark.asyncio
async def test_resend_uses_one_pooled_client_and_token_for_both_calls(
    patch_client: SimpleNamespace,
) -> None:
    endpoint = _ResendEndpoint()
    patch_client.install(endpoint)
    client = mgmt.get_auth0_http_client()

    assert await resend_auth0_verification_email("auth0|abc") is ResendOutcome.ACCEPTED
    assert await resend_auth0_verification_email("auth0|abc") is ResendOutcome.ACCEPTED
    assert endpoint.token_posts == 1
    assert endpoint.user_gets == 2
    assert endpoint.job_posts == 2
    assert len(patch_client.clients) == 1
    assert mgmt.get_auth0_http_client() is client
    assert not client.is_closed

    await mgmt.close_auth0_http_client()
    assert client.is_closed


@pytest.mark.asyncio
async def test_resend_deadline_covers_a_stalled_lookup_without_a_job(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _ResendEndpoint(stall_user=True)
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(mgmt, "_SIDE_EFFECT_RESERVE_SECONDS", 0.1)

    started = time.monotonic()
    with pytest.raises(Auth0ManagementUnavailable):
        await resend_auth0_verification_email("auth0|abc")
    assert time.monotonic() - started < 1.0
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 0


@pytest.mark.asyncio
async def test_lookup_entering_the_post_reserve_sends_no_job(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _ResendEndpoint()
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_SIDE_EFFECT_RESERVE_SECONDS", 10.0)
    # Keep token acquisition out of this measurement: the deadline in question
    # is the remaining time after the eligibility read, not the token's expiry.
    mgmt._token_cache["access_token"] = "warm-token"
    mgmt._token_cache["expires_at"] = time.time() + 3600
    # The user read itself advances the clock by 24 seconds. All other clock
    # reads see the same instant, including the HTTP wait's own deadline setup.
    current = 100.0
    real_get = mgmt._get_user

    def monotonic() -> float:
        return current

    async def finish_lookup(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal current
        user = await real_get(*args, **kwargs)
        current = 124.9
        return user

    monkeypatch.setattr(mgmt, "_monotonic", monotonic)
    monkeypatch.setattr(mgmt, "_operation_deadline", lambda: 125.0)
    monkeypatch.setattr(mgmt, "_get_user", finish_lookup)

    with pytest.raises(Auth0ManagementUnavailable, match="reserve"):
        await resend_auth0_verification_email("auth0|abc")
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 0


@pytest.mark.asyncio
async def test_token_budget_already_exhausted_sends_no_lookup_or_job(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold cache and a deadline that is already due fails before any HTTP call.

    This covers token-budget exhaustion only. It does not reach the eligibility
    read or the post-lookup reserve check. A lookup that consumes the reserve is
    ``test_lookup_entering_the_post_reserve_sends_no_job``. A token request that
    uses part of a still-open budget is ``test_slow_token_preserves_or_refuses_the_post_reserve``.
    """
    endpoint = _ResendEndpoint()
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_SIDE_EFFECT_RESERVE_SECONDS", 10.0)
    monkeypatch.setattr(mgmt, "_operation_deadline", lambda: 100.0)
    monkeypatch.setattr(mgmt, "_monotonic", lambda: 100.0)

    with pytest.raises(
        Auth0ManagementUnavailable, match="token refresh budget exhausted"
    ):
        await resend_auth0_verification_email("auth0|abc")
    assert endpoint.token_posts == 0
    assert endpoint.user_gets == 0
    assert endpoint.job_posts == 0


class _Clock:
    """Module-local deadline clock. It does not replace ``time.monotonic``."""

    def __init__(self) -> None:
        self.now = 0.0
        self.at_job: float | None = None

    def __call__(self) -> float:
        return self.now


class _TimedToken(_ResendEndpoint):
    def __init__(self, clock: _Clock, advance_to: float) -> None:
        super().__init__()
        self._clock = clock
        self._advance_to = advance_to

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self._clock.now = self._advance_to
        if request.url.path == "/api/v2/jobs/verification-email":
            self._clock.at_job = self._clock.now
        return await super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "advance_to,expect_job",
    [(5.0, True), (16.0, False)],
    ids=["reserve-remains", "token-consumes-reserve"],
)
async def test_slow_token_preserves_or_refuses_the_post_reserve(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    advance_to: float,
    expect_job: bool,
) -> None:
    """Token acquisition spends the shared budget; the job keeps a full phase or is not sent."""
    clock = _Clock()
    endpoint = _TimedToken(clock, advance_to)
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_monotonic", clock)

    if expect_job:
        assert (
            await resend_auth0_verification_email("auth0|abc") is ResendOutcome.ACCEPTED
        )
        assert endpoint.token_posts == 1
        assert endpoint.user_gets == 1
        assert endpoint.job_posts == 1
        assert clock.at_job is not None
        # The deadline was taken at t=0, so it is one full operation budget later.
        assert (
            mgmt._MGMT_OPERATION_DEADLINE_SECONDS - clock.at_job
            >= mgmt._SIDE_EFFECT_RESERVE_SECONDS
        )
    else:
        with pytest.raises(Auth0ManagementUnavailable, match="GET user"):
            await resend_auth0_verification_email("auth0|abc")
        assert endpoint.token_posts == 1
        assert endpoint.user_gets == 0
        assert endpoint.job_posts == 0


@pytest.mark.asyncio
async def test_already_verified_live_user_sends_no_job(
    patch_client: SimpleNamespace,
) -> None:
    endpoint = _ResendEndpoint(user={**_RESEND_USER, "email_verified": True})
    patch_client.install(endpoint)
    assert (
        await resend_auth0_verification_email("auth0|abc")
        is ResendOutcome.ALREADY_VERIFIED
    )
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 0


@pytest.mark.asyncio
async def test_hanging_resend_job_is_bounded_and_not_replayed(
    patch_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _ResendEndpoint(stall_job=True)
    patch_client.install(endpoint)
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(mgmt, "_SIDE_EFFECT_RESERVE_SECONDS", 0.1)

    started = time.monotonic()
    with pytest.raises(Auth0ManagementUnavailable):
        await resend_auth0_verification_email("auth0|abc")
    assert time.monotonic() - started < 1.0
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "500", "502", "503"])
async def test_ambiguous_resend_failures_never_replay_the_job(
    patch_client: SimpleNamespace,
    failure: str,
) -> None:
    if failure == "transport":
        endpoint = _ResendEndpoint(job=_TRANSPORT_ERROR)
    else:
        endpoint = _ResendEndpoint(
            job=httpx.Response(int(failure), json={"message": "upstream"})
        )
    patch_client.install(endpoint)

    with pytest.raises(Auth0ManagementUnavailable):
        await resend_auth0_verification_email("auth0|abc")
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,advertised",
    [
        ({}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS),
        ({"Retry-After": "7.5"}, 8),
        ({"Retry-After": "nan"}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS),
        ({"Retry-After": "inf"}, mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS),
    ],
    ids=["missing", "fractional", "nan", "infinity"],
)
async def test_resend_job_429_has_a_finite_typed_retry_hint(
    patch_client: SimpleNamespace,
    headers: dict[str, str],
    advertised: int,
) -> None:
    endpoint = _ResendEndpoint(job=httpx.Response(429, headers=headers))
    patch_client.install(endpoint)
    with pytest.raises(Auth0ManagementRateLimited) as raised:
        await resend_auth0_verification_email("auth0|abc")
    assert raised.value.retry_after == advertised
    assert endpoint.job_posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["lookup", "job"])
async def test_cancellation_during_resend_propagates_without_a_replay(
    patch_client: SimpleNamespace,
    stage: str,
) -> None:
    endpoint = _ResendEndpoint(stall_user=stage == "lookup", stall_job=stage == "job")
    patch_client.install(endpoint)
    task = asyncio.create_task(resend_auth0_verification_email("auth0|abc"))
    started = endpoint.user_started if stage == "lookup" else endpoint.job_started
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == (1 if stage == "job" else 0)


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_resend_token_starts_no_job(
    patch_client: SimpleNamespace,
) -> None:
    endpoint = _ResendEndpoint()
    patch_client.install(endpoint)
    lock = mgmt._token_refresh_lock
    await lock.acquire()
    try:
        task = asyncio.create_task(resend_auth0_verification_email("auth0|abc"))
        await _yield_until_lock_has_a_waiter(lock)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        lock.release()
    assert endpoint.user_gets == 0
    assert endpoint.job_posts == 0
    assert await resend_auth0_verification_email("auth0|abc") is ResendOutcome.ACCEPTED
