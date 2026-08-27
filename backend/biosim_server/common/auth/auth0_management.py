"""Thin async client for the Auth0 Management API (api/v2/users/*).

Used by PATCH/DELETE /api/v1/me to actually mutate the Auth0 user record --
Auth0 is the single source of truth for identity (no local password storage),
so profile writes have to go through it rather than a local shadow table.

Requires a Machine-to-Machine Auth0 application authorized for the Management
API with `update:users` / `delete:users` scopes (AUTH0_MANAGEMENT_CLIENT_ID /
AUTH0_MANAGEMENT_CLIENT_SECRET). Callers should check `management_api_configured()`
first and surface a 503 when it's false, rather than let these raise.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
# Refresh a little before actual expiry to avoid racing a request against the
# token dying mid-flight.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60
_token_refresh_lock = asyncio.Lock()


def management_api_configured() -> bool:
    settings = get_settings().auth0
    return bool(settings.management_client_id and settings.management_client_secret)


async def _get_management_token() -> str:
    settings = get_settings().auth0
    now = time.time()
    if _token_cache["access_token"] is not None and now < _token_cache["expires_at"]:
        access_token: str = _token_cache["access_token"]
        return access_token

    async with _token_refresh_lock:
        # Re-check after acquiring the lock -- another concurrent caller may have
        # already refreshed the token while we were waiting on it.
        now = time.time()
        if _token_cache["access_token"] is None or now >= _token_cache["expires_at"]:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://{settings.domain}/oauth/token",
                    json={
                        "client_id": settings.management_client_id,
                        "client_secret": settings.management_client_secret,
                        "audience": f"https://{settings.domain}/api/v2/",
                        "grant_type": "client_credentials",
                    },
                    timeout=10.0,
                )
                resp.raise_for_status()
                payload = resp.json()
                _token_cache["access_token"] = payload["access_token"]
                _token_cache["expires_at"] = now + payload["expires_in"] - _EXPIRY_SAFETY_MARGIN_SECONDS
        access_token = _token_cache["access_token"]
        return access_token


async def _auth_headers() -> dict[str, str]:
    token = await _get_management_token()
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
    send: Callable[[], Awaitable[httpx.Response]], *, op: str
) -> httpx.Response:
    """Send a Management API request with bounded exponential backoff.

    Retries only 429 and 5xx responses and transport errors; any other 4xx is
    returned unretried so the caller's ``raise_for_status()`` surfaces it. Raises
    ``Auth0ManagementRateLimited`` when a 429 never clears (distinct from a 5xx,
    which raises ``Auth0ManagementUnavailable``) so the two map to different HTTP
    statuses. Logs only the operation name, HTTP status, attempt number, and
    exception type -- never the bearer token, the secret, or a response body.
    """
    deadline = time.monotonic() + _MGMT_TOTAL_DEADLINE_SECONDS
    last_status: int | None = None
    exhausted_retry_after = _MGMT_EXHAUSTED_RETRY_AFTER_SECONDS
    for attempt in range(1, _MGMT_MAX_ATTEMPTS + 1):
        retry_after: float | None = None
        try:
            resp = await send()
        except httpx.TransportError as exc:
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
        # Stop early if the wait would run past the total deadline.
        if retry_after is not None:
            sleep_for = retry_after
        else:
            ceiling = _MGMT_BASE_DELAY_SECONDS * (_MGMT_BACKOFF_MULTIPLIER ** (attempt - 1))
            sleep_for = random.uniform(0, ceiling)
        if time.monotonic() + sleep_for >= deadline:
            break
        await asyncio.sleep(sleep_for)

    if last_status == 429:
        raise Auth0ManagementRateLimited(exhausted_retry_after)
    raise Auth0ManagementUnavailable(
        f"Auth0 Management {op} failed after {_MGMT_MAX_ATTEMPTS} attempts"
    )


async def get_auth0_user(user_id: str) -> dict[str, Any]:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        headers = await _auth_headers()
        resp = await _send_with_retry(
            lambda: client.get(
                f"https://{settings.domain}/api/v2/users/{user_id}",
                headers=headers,
                timeout=10.0,
            ),
            op="GET user",
        )
        resp.raise_for_status()
        return dict(resp.json())


async def update_auth0_user(user_id: str, *, name: str | None = None) -> dict[str, Any]:
    settings = get_settings().auth0
    fields = {"name": name} if name is not None else {}
    async with httpx.AsyncClient() as client:
        headers = await _auth_headers()
        resp = await _send_with_retry(
            lambda: client.patch(
                f"https://{settings.domain}/api/v2/users/{user_id}",
                headers=headers,
                json=fields,
                timeout=10.0,
            ),
            op="PATCH user",
        )
        resp.raise_for_status()
        return dict(resp.json())


async def delete_auth0_user(user_id: str) -> None:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        headers = await _auth_headers()
        resp = await _send_with_retry(
            lambda: client.delete(
                f"https://{settings.domain}/api/v2/users/{user_id}",
                headers=headers,
                timeout=10.0,
            ),
            op="DELETE user",
        )
        resp.raise_for_status()
