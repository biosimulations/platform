"""Retry / backoff behaviour for the Auth0 Management API client (#23, D-8).

The three resource calls (`get`/`update`/`delete_auth0_user`) retry 429, 5xx,
and transport errors with bounded exponential backoff, and distinguish an
exhausted 429 (``Auth0ManagementRateLimited`` → HTTP 503 + Retry-After) from an
exhausted 5xx/transport failure (``Auth0ManagementUnavailable`` → HTTP 502).

Tests drive the client through ``httpx.MockTransport`` (built into httpx -- no
new dependency, no network) and stub the token fetch and ``asyncio.sleep`` so no
real credential is used and no real delay is incurred. Attempt counts are
asserted from the scripted handler, never from wall-clock timing.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common.auth.auth0_management import (
    Auth0ManagementRateLimited,
    Auth0ManagementUnavailable,
    delete_auth0_user,
    get_auth0_user,
    update_auth0_user,
)

# A recognizable token/secret so the leak-guard test has concrete needles.
_MGMT_TOKEN = "super-secret-management-token-should-never-be-logged"
_MGMT_SECRET = "mgmt-secret-should-never-be-logged"
_TRANSPORT_ERROR = "__transport_error__"


class _Script:
    """Serves a fixed sequence of responses in order, counting invocations.

    Each item is either an ``httpx.Response`` or the ``_TRANSPORT_ERROR``
    sentinel (raised as an ``httpx.ConnectError``). The last item repeats if the
    helper somehow calls more times than scripted -- which would itself be a bug
    the ``calls`` assertion catches.
    """

    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        item = self._items[min(self.calls, len(self._items) - 1)]
        self.calls += 1
        if item == _TRANSPORT_ERROR:
            raise httpx.ConnectError("simulated transport failure", request=request)
        assert isinstance(item, httpx.Response)
        return item


def _response(status: int, body: dict[str, object] | None = None,
              headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, headers=headers or {})


@pytest.fixture
def patch_mgmt(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate the Management client: stub settings + token, no-op sleep, and a
    scripted MockTransport installed via ``.install(script)``."""

    stub_auth0 = SimpleNamespace(
        domain="test-tenant.us.auth0.com",
        management_client_id="mgmt-id",
        management_client_secret=_MGMT_SECRET,
    )
    monkeypatch.setattr(mgmt, "get_settings", lambda: SimpleNamespace(auth0=stub_auth0))
    monkeypatch.setattr(mgmt, "_get_management_token", AsyncMock(return_value=_MGMT_TOKEN))
    sleep_mock = AsyncMock()
    # Patch the real module objects the helper references (`asyncio.sleep`,
    # `httpx.AsyncClient`) rather than reaching through the re-exports on `mgmt`.
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    real_client = httpx.AsyncClient

    def _install(script: _Script) -> None:
        transport = httpx.MockTransport(script)

        def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

    return SimpleNamespace(install=_install, sleep=sleep_mock)


@pytest.mark.asyncio
async def test_succeeds_first_try_without_retry(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([_response(200, {"name": "Jane"})])
    patch_mgmt.install(script)
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert script.calls == 1
    patch_mgmt.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_retries_5xx_then_succeeds(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([_response(500), _response(200, {"name": "Jane"})])
    patch_mgmt.install(script)
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert script.calls == 2
    assert patch_mgmt.sleep.await_count == 1


@pytest.mark.asyncio
async def test_exhausted_5xx_raises_unavailable(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([_response(503), _response(502), _response(500)])
    patch_mgmt.install(script)
    with pytest.raises(Auth0ManagementUnavailable):
        await get_auth0_user("auth0|abc")
    assert script.calls == 3  # exactly max attempts, no more


@pytest.mark.asyncio
async def test_exhausted_429_raises_rate_limited_with_retry_after(
    patch_mgmt: SimpleNamespace,
) -> None:
    script = _Script([_response(429, headers={"Retry-After": "2"})] * 3)
    patch_mgmt.install(script)
    with pytest.raises(Auth0ManagementRateLimited) as exc_info:
        await update_auth0_user("auth0|abc", name="Jane")
    assert exc_info.value.retry_after == 2
    assert script.calls == 3


@pytest.mark.asyncio
async def test_non_retryable_4xx_is_not_retried(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([_response(404, {"error": "not found"})])
    patch_mgmt.install(script)
    with pytest.raises(httpx.HTTPStatusError):
        await get_auth0_user("auth0|missing")
    assert script.calls == 1
    patch_mgmt.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_honours_429_retry_after_before_retrying(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([
        _response(429, headers={"Retry-After": "1"}),
        _response(200, {"name": "Jane"}),
    ])
    patch_mgmt.install(script)
    assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    assert patch_mgmt.sleep.await_count == 1
    # Retry-After is honoured verbatim (1s), not replaced by jitter.
    assert patch_mgmt.sleep.await_args_list[0].args[0] == 1.0


@pytest.mark.asyncio
async def test_transport_error_retried_then_unavailable(patch_mgmt: SimpleNamespace) -> None:
    script = _Script([_TRANSPORT_ERROR, _TRANSPORT_ERROR, _TRANSPORT_ERROR])
    patch_mgmt.install(script)
    with pytest.raises(Auth0ManagementUnavailable):
        await delete_auth0_user("auth0|abc")
    assert script.calls == 3


@pytest.mark.asyncio
async def test_retry_logs_never_leak_token_or_secret_and_name_the_status(
    patch_mgmt: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    script = _Script([
        _response(500),
        _response(429, headers={"Retry-After": "1"}),
        _response(200, {"name": "Jane"}),
    ])
    patch_mgmt.install(script)
    with caplog.at_level(logging.WARNING, logger="biosim_server.common.auth.auth0_management"):
        assert await get_auth0_user("auth0|abc") == {"name": "Jane"}
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert _MGMT_TOKEN not in text
    assert _MGMT_SECRET not in text
    # 429 and 5xx are distinguishable in the logs for triage.
    assert "upstream 500" in text
    assert "upstream 429" in text
