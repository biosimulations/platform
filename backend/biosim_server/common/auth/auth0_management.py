"""Thin async client for the Auth0 Management API (api/v2/*).

Used by PATCH/DELETE /api/v1/me to actually mutate the Auth0 user record --
Auth0 is the single source of truth for identity (no local password storage),
so profile writes have to go through it rather than a local shadow table.

Also used by POST /api/v1/me/password-reset to issue a short-lived hosted
password-change ticket (api/v2/tickets/password-change).

Requires a Machine-to-Machine Auth0 application authorized for the Management
API with `update:users` / `delete:users` (profile writes) and
`create:user_tickets` (password reset) scopes (AUTH0_MANAGEMENT_CLIENT_ID /
AUTH0_MANAGEMENT_CLIENT_SECRET). Callers should check `management_api_configured()`
first and surface a 503 when it's false, rather than let these raise.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
# Refresh a little before actual expiry to avoid racing a request against the
# token dying mid-flight.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60
_token_refresh_lock = asyncio.Lock()
# After a failed refresh, share that failure with every caller for this long
# instead of letting each waiter issue its own serialized token POST. This is
# the difference between "one outbound token request per Auth0 outage window per
# process" and "one per concurrent inbound request": with the Management API
# enabled, N simultaneous /api/v1/me callers used to queue on the refresh lock
# and then each repeat the failing POST in turn. Bounded so a recovered Auth0 is
# picked up on the next window rather than being cached as broken.
_MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS = 5.0
# Module-level so `management_api_configured()`-style callers and tests can see
# it; a float epoch (time.time()), matching _token_cache's clock.
_token_refresh_failed_until: float = 0.0
# Per-phase timeouts for this client's own requests (see _MGMT_OPERATION_*):
# explicit at the client and repeated per request so a future call site cannot
# inherit an unbounded default.
_MGMT_HTTP_TIMEOUT_SECONDS = 10.0
# Whole-operation budget for one Management-backed request: refresh-lock wait +
# token acquisition + the resource call's retry loop. Larger than the retry
# budget alone (15 s) so a warm token is never cut short, and smaller than the
# naive sum of the phases (unbounded lock wait + 10 s token + 15 s retries), so a
# cold Auth0 outage cannot hold an interactive request open indefinitely.
_MGMT_OPERATION_DEADLINE_SECONDS = 25.0


def management_api_configured() -> bool:
    settings = get_settings().auth0
    return bool(settings.management_client_id and settings.management_client_secret)


# --- lifecycle-owned transport (AUTH-MIN-004) ---------------------------------
# One pooled client for Auth0 token + Management API calls, deliberately
# separate from the public page upstream client (dependencies.get_http_client):
# that one sends no credentials and must never inherit this one's defaults, and
# this one must never be reused for caller-controlled destinations. Constructed
# lazily so an app/test client that skips lifespan still works, and closed by
# shutdown_standalone().

_http_client: httpx.AsyncClient | None = None


def get_auth0_http_client() -> httpx.AsyncClient:
    """Process-scoped client for Auth0 calls. Credentials stay per request."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_MGMT_HTTP_TIMEOUT_SECONDS)
    return _http_client


async def close_auth0_http_client() -> None:
    """Release the pooled transport. Called once from application shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _reset_management_state() -> None:
    """Clear cached token/refresh/client state. Test-only affordance.

    Mirrors ``ratelimit._reset_rate_limit_state()`` and auth0.py's JWKS-cache
    reset: the module keeps process-scoped state, so a token, a failure cooldown
    or a client (with whatever transport a previous test installed) must not leak
    from one test to the next. The lock is replaced rather than cleared so it
    cannot stay bound to a closed event loop between tests.
    """
    global _http_client, _token_refresh_failed_until, _token_refresh_lock
    _http_client = None
    _token_refresh_failed_until = 0.0
    _token_refresh_lock = asyncio.Lock()
    _token_cache["access_token"] = None
    _token_cache["expires_at"] = 0.0


def _operation_deadline() -> float:
    """Start a monotonic whole-operation budget for one Management call."""
    return time.monotonic() + _MGMT_OPERATION_DEADLINE_SECONDS


def _validated_token_payload(payload: object) -> tuple[str, float]:
    """Extract (access_token, expires_in) or fail, before anything is cached.

    The previous implementation indexed ``payload["access_token"]`` and
    ``payload["expires_in"]`` directly, so a 200 with an unexpected body raised
    an unguarded ``KeyError`` -- an unhandled exception the caller could only
    report as a generic failure, and one that made "the provider answered" look
    like "the provider is down". Both fields are validated here so a malformed
    response can never partially populate the cache.
    """
    if not isinstance(payload, dict):
        raise ValueError("token response is not a JSON object")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("token response carries no access_token")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise ValueError("token response carries no usable expires_in")
    return access_token, float(expires_in)


async def _get_management_token(*, deadline: float) -> str:
    """A cached M2M token, or a bounded attempt to acquire one.

    ``deadline`` is a ``time.monotonic()`` instant that the *whole* operation
    (lock wait included) must fit inside; lock acquisition, the token POST and
    the caller's retry loop are all bounded by what remains of it. A failed
    attempt arms a short shared cooldown rather than letting every waiter retry.
    """
    global _token_refresh_failed_until
    settings = get_settings().auth0
    now = time.time()
    cached = _token_cache["access_token"]
    if cached is not None and now < float(_token_cache["expires_at"]):
        return str(cached)
    if now < _token_refresh_failed_until:
        # A refresh failed moments ago. Share that result instead of queueing
        # another serialized token POST behind the lock.
        raise Auth0ManagementUnavailable("Auth0 token refresh is in a failure cooldown")

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Auth0ManagementUnavailable("Auth0 token refresh budget exhausted")
    try:
        # Cancellation while waiting must not leave the lock held; wait_for
        # cancels the acquire() before it ever succeeds, and the finally below
        # only releases what this coroutine actually acquired.
        await asyncio.wait_for(_token_refresh_lock.acquire(), timeout=remaining)
    except TimeoutError as exc:
        raise Auth0ManagementUnavailable("Timed out waiting for an Auth0 token refresh") from exc
    try:
        # Re-check after acquiring the lock -- another concurrent caller may have
        # already refreshed the token (or armed the cooldown) while we waited.
        now = time.time()
        cached = _token_cache["access_token"]
        if cached is not None and now < float(_token_cache["expires_at"]):
            return str(cached)
        if now < _token_refresh_failed_until:
            raise Auth0ManagementUnavailable("Auth0 token refresh is in a failure cooldown")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Auth0ManagementUnavailable("Auth0 token refresh budget exhausted")
        try:
            client = get_auth0_http_client()
            resp = await asyncio.wait_for(
                client.post(
                    f"https://{settings.domain}/oauth/token",
                    json={
                        "client_id": settings.management_client_id,
                        "client_secret": settings.management_client_secret,
                        "audience": f"https://{settings.domain}/api/v2/",
                        "grant_type": "client_credentials",
                    },
                    timeout=_MGMT_HTTP_TIMEOUT_SECONDS,
                ),
                timeout=remaining,
            )
            resp.raise_for_status()
            access_token, expires_in = _validated_token_payload(resp.json())
        except httpx.HTTPStatusError as exc:
            _token_refresh_failed_until = time.time() + _MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS
            if exc.response.status_code == 429:
                # Same distinction the resource calls make: Auth0 throttling is a
                # 503-with-Retry-After the caller can act on, not a 502.
                logger.warning(
                    "Auth0 token endpoint rate-limited; sharing the failure for %ds",
                    int(_MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS),
                )
                raise Auth0ManagementRateLimited(_MGMT_EXHAUSTED_RETRY_AFTER_SECONDS) from exc
            logger.warning(
                "Auth0 Management token acquisition failed (%s); sharing the failure for %ds",
                type(exc).__name__, int(_MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS),
            )
            raise Auth0ManagementUnavailable("Auth0 token acquisition failed") from exc
        except Exception as exc:
            _token_refresh_failed_until = time.time() + _MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS
            logger.warning(
                "Auth0 Management token acquisition failed (%s); sharing the failure for %ds",
                type(exc).__name__, int(_MGMT_REFRESH_FAILURE_COOLDOWN_SECONDS),
            )
            raise Auth0ManagementUnavailable("Auth0 token acquisition failed") from exc
        # Only a fully validated response replaces the cache.
        _token_cache["access_token"] = access_token
        _token_cache["expires_at"] = time.time() + expires_in - _EXPIRY_SAFETY_MARGIN_SECONDS
        _token_refresh_failed_until = 0.0
        return access_token
    finally:
        _token_refresh_lock.release()


async def _auth_headers(*, deadline: float) -> dict[str, str]:
    token = await _get_management_token(deadline=deadline)
    return {"Authorization": f"Bearer {token}"}


class Auth0ManagementError(Exception):
    """A Management API call failed after the retry budget was exhausted."""


class Auth0ManagementRateLimited(Auth0ManagementError):
    """Auth0 kept returning 429 through every retry. Carries a Retry-After hint
    (seconds) so the caller can surface a 503 the client can act on."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Auth0 Management API rate limit did not clear")
        self.retry_after = retry_after


class Auth0ManagementUnavailable(Auth0ManagementError):
    """A 5xx or transport failure persisted through every retry."""


# Retry policy for the Management API resource calls (#23 / decision D-8).
# Deliberately small: PATCH/DELETE /api/v1/me are interactive, so a caller is
# blocked on the response -- a long backoff is worse than a prompt 502/503 they
# can retry. The token cache/lock above is intentionally NOT wrapped (EH-11).
_MGMT_MAX_ATTEMPTS = 3  # 1 initial try + 2 retries
_MGMT_BASE_DELAY_SECONDS = 0.5
_MGMT_BACKOFF_MULTIPLIER = 2.0  # full-jitter over 0.5s, then 1.0s
_MGMT_TOTAL_DEADLINE_SECONDS = 15.0
_MGMT_RETRY_AFTER_CEILING_SECONDS = 30  # never honour a 429 wait longer than this
_MGMT_EXHAUSTED_RETRY_AFTER_SECONDS = 10  # advertised when a 429 never clears


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Auth0's numeric ``Retry-After`` in seconds, clamped to a ceiling. None if
    absent or malformed -- Auth0's limiter emits the numeric form, not HTTP-date."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, float(_MGMT_RETRY_AFTER_CEILING_SECONDS))


async def _send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]], *, op: str, deadline: float
) -> httpx.Response:
    """Send a Management API request with bounded exponential backoff.

    Retries only 429 and 5xx responses, transport errors, and per-attempt
    timeouts; any other 4xx is returned unretried so the caller's
    ``raise_for_status()`` surfaces it. Raises ``Auth0ManagementRateLimited``
    when a 429 never clears (distinct from a 5xx, which raises
    ``Auth0ManagementUnavailable``) so the two map to different HTTP statuses.
    Logs only the operation name, HTTP status, attempt number, and exception
    type -- never the bearer token, the secret, or a response body.

    ``deadline`` is the operation's monotonic budget, which already includes any
    refresh-lock wait and token acquisition. It bounds the loop three ways: the
    loop can never outlive the smaller of itself and
    ``_MGMT_TOTAL_DEADLINE_SECONDS``, an attempt is not started once the budget
    is gone, and every attempt is wrapped in ``asyncio.wait_for`` against what
    remains -- a check before sleeping alone left an in-flight request (a slow or
    stalled body stream) able to run past the nominal deadline.
    """
    limit = min(deadline, time.monotonic() + _MGMT_TOTAL_DEADLINE_SECONDS)
    last_status: int | None = None
    exhausted_retry_after = _MGMT_EXHAUSTED_RETRY_AFTER_SECONDS
    for attempt in range(1, _MGMT_MAX_ATTEMPTS + 1):
        retry_after: float | None = None
        remaining = limit - time.monotonic()
        if remaining <= 0:
            # Budget exhausted before this attempt: sending now could only push
            # the response past the bound the caller was promised.
            break
        try:
            resp = await asyncio.wait_for(send(), timeout=remaining)
        except (httpx.TransportError, TimeoutError) as exc:
            last_status = None
            logger.warning(
                "Auth0 Management %s transport error (attempt %d/%d): %s",
                op, attempt, _MGMT_MAX_ATTEMPTS, type(exc).__name__,
            )
            if attempt == _MGMT_MAX_ATTEMPTS:
                raise Auth0ManagementUnavailable(
                    f"Auth0 Management {op} failed after {_MGMT_MAX_ATTEMPTS} attempts"
                ) from exc
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code != 429 and resp.status_code < 500:
                return resp  # non-retryable client error; caller raises
            last_status = resp.status_code
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None:
                    exhausted_retry_after = max(1, int(retry_after))
            logger.warning(
                "Auth0 Management %s upstream %d (attempt %d/%d)",
                op, resp.status_code, attempt, _MGMT_MAX_ATTEMPTS,
            )
            if attempt == _MGMT_MAX_ATTEMPTS:
                break
        # Honour a 429 Retry-After when present, else full-jitter exponential.
        # Stop early if the wait would run past the deadline.
        if retry_after is not None:
            sleep_for = retry_after
        else:
            ceiling = _MGMT_BASE_DELAY_SECONDS * (_MGMT_BACKOFF_MULTIPLIER ** (attempt - 1))
            sleep_for = random.uniform(0, ceiling)
        if time.monotonic() + sleep_for >= limit:
            break
        await asyncio.sleep(sleep_for)

    if last_status == 429:
        raise Auth0ManagementRateLimited(exhausted_retry_after)
    raise Auth0ManagementUnavailable(
        f"Auth0 Management {op} failed after {_MGMT_MAX_ATTEMPTS} attempts"
    )


async def get_auth0_user(user_id: str) -> dict[str, Any]:
    settings = get_settings().auth0
    deadline = _operation_deadline()
    headers = await _auth_headers(deadline=deadline)
    client = get_auth0_http_client()
    resp = await _send_with_retry(
        lambda: client.get(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=headers,
            timeout=_MGMT_HTTP_TIMEOUT_SECONDS,
        ),
        op="GET user",
        deadline=deadline,
    )
    resp.raise_for_status()
    return dict(resp.json())


async def update_auth0_user(user_id: str, *, name: str | None = None) -> dict[str, Any]:
    settings = get_settings().auth0
    fields = {"name": name} if name is not None else {}
    deadline = _operation_deadline()
    headers = await _auth_headers(deadline=deadline)
    client = get_auth0_http_client()
    resp = await _send_with_retry(
        lambda: client.patch(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=headers,
            json=fields,
            timeout=_MGMT_HTTP_TIMEOUT_SECONDS,
        ),
        op="PATCH user",
        deadline=deadline,
    )
    resp.raise_for_status()
    return dict(resp.json())


async def delete_auth0_user(user_id: str) -> None:
    settings = get_settings().auth0
    deadline = _operation_deadline()
    headers = await _auth_headers(deadline=deadline)
    client = get_auth0_http_client()
    resp = await _send_with_retry(
        lambda: client.delete(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=headers,
            timeout=_MGMT_HTTP_TIMEOUT_SECONDS,
        ),
        op="DELETE user",
        deadline=deadline,
    )
    resp.raise_for_status()


async def create_password_change_ticket(user_id: str) -> str:
    """Issue a short-lived bearer capability. Never retry uncertain issuance."""
    settings = get_settings().auth0
    deadline = _operation_deadline()
    headers = await _auth_headers(deadline=deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Auth0ManagementUnavailable(
            "Auth0 password reset budget exhausted before the ticket request"
        )
    client = get_auth0_http_client()
    try:
        resp = await asyncio.wait_for(
            client.post(
                f"https://{settings.domain}/api/v2/tickets/password-change",
                headers=headers,
                json={
                    "user_id": user_id,
                    "client_id": settings.password_reset_client_id,
                    "ttl_sec": 600,
                    "mark_email_as_verified": False,
                    "includeEmailInRedirect": False,
                },
                timeout=_MGMT_HTTP_TIMEOUT_SECONDS,
            ),
            timeout=remaining,
        )
    except TimeoutError as exc:
        # Cancelled, never re-sent: an unanswered POST may already have minted a
        # ticket, so a second attempt would be a second capability.
        raise Auth0ManagementUnavailable("Auth0 password reset request timed out") from exc
    if resp.status_code == 429:
        raise Auth0ManagementRateLimited(10)
    resp.raise_for_status()
    payload = resp.json()
    ticket = payload.get("ticket") if isinstance(payload, dict) else None
    if not isinstance(ticket, str) or not ticket or any(
        c.isspace() or ord(c) < 32 or c == "\\" for c in ticket
    ):
        raise Auth0ManagementError("Invalid password reset response")
    parsed = urlsplit(ticket)
    if (
        parsed.scheme != "https"
        or parsed.netloc != settings.domain
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise Auth0ManagementError("Invalid password reset response")
    return ticket
