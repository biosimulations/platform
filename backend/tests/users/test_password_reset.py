"""Hosted reset contract; no live Auth0 requests."""
import json
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.common.auth import auth0_management as management
from biosim_server.common.ratelimit import _reset_rate_limit_state
from biosim_server.config import get_settings
from biosim_server.users import router

URL = "https://tenant.auth0.com/lo/reset?ticket=sensitive"
PATH = "/api/v1/me/password-reset"


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "tenant.auth0.com")
    monkeypatch.setattr(settings, "password_reset_client_id", "spa-id")
    monkeypatch.setattr(settings, "management_client_id", "m2m-id")
    monkeypatch.setattr(settings, "management_client_secret", "private-secret")
    # Pinned so a developer's local .env cannot change what these tests assert:
    # the step-up gate is opt-in, and the reset quota has its own ceiling/window.
    monkeypatch.setattr(settings, "password_reset_require_recent_auth", False)
    monkeypatch.setattr(settings, "password_reset_max_auth_age_seconds", 300)
    monkeypatch.setattr(management, "_token_cache", {"access_token": None, "expires_at": 0.0})
    _reset_rate_limit_state()
    yield
    app.dependency_overrides.pop(get_current_user, None)
    _reset_rate_limit_state()


def authorize(
    sub: str = "auth0|mine",
    issuer: str | None = "https://tenant.auth0.com/",
    auth_time: int | None = None,
) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        sub=sub, issuer=issuer, auth_time=auth_time
    )


def assert_no_store(response: object) -> None:
    """Every response on this route -- success or error -- must be uncacheable."""
    headers = response.headers  # type: ignore[attr-defined]
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"


def test_authentication_required() -> None:
    response = TestClient(app).post(PATH)
    assert response.status_code == 401
    # AUTH-MIN-001: the privacy policy is a property of the route, so it applies
    # to the dependency-generated 401 too -- without dropping the challenge.
    assert_no_store(response)
    assert response.headers["www-authenticate"].startswith("Bearer realm=\"api\"")


def test_dependency_failure_keeps_its_own_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dependency error is uncacheable *and* keeps Retry-After/WWW-Authenticate."""

    def raise_unavailable() -> AuthenticatedUser:
        raise HTTPException(
            status_code=503,
            detail="Authentication temporarily unavailable",
            headers={"Retry-After": "10"},
        )

    app.dependency_overrides[get_current_user] = raise_unavailable
    response = TestClient(app).post(PATH)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert_no_store(response)


@pytest.mark.parametrize(
    "auth_time,expected_status",
    [
        (None, 403),                     # no evidence at all
        (int(time.time()) - 10, 200),    # inside the window
        (int(time.time()) - 301, 403),   # older than password_reset_max_auth_age_seconds
        (int(time.time()) + 3600, 403),  # future-dated, past the skew allowance
    ],
    ids=["missing", "fresh", "stale", "future"],
)
def test_step_up_gate_when_enabled(
    auth_time: int | None, expected_status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTH-MAJ-004: with the gate on, only a fresh IdP-asserted sign-in mints a ticket."""
    monkeypatch.setattr(get_settings().auth0, "password_reset_require_recent_auth", True)
    authorize(auth_time=auth_time)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    response = TestClient(app).post(PATH)
    assert response.status_code == expected_status
    assert_no_store(response)
    if expected_status == 200:
        assert response.json() == {"url": URL}
        mock.assert_awaited_once_with("auth0|mine")
    else:
        mock.assert_not_awaited()


def test_step_up_window_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().auth0, "password_reset_require_recent_auth", True)
    monkeypatch.setattr(get_settings().auth0, "password_reset_max_auth_age_seconds", 60)
    authorize(auth_time=int(time.time()) - 120)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    response = TestClient(app).post(PATH)
    assert response.status_code == 403
    mock.assert_not_awaited()


def test_step_up_gate_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary token with no `auth_time` claim still works while the gate is off."""
    authorize(auth_time=None)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    assert TestClient(app).post(PATH).status_code == 200
    mock.assert_awaited_once_with("auth0|mine")


def test_foreign_issuer_does_not_consume_the_local_reset_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH-MIN-002: the quota key is (issuer, subject), so a same-sub token from
    another trusted issuer is charged to its own bucket before its 403."""
    monkeypatch.setattr(get_settings().ratelimit, "password_reset_per_window", 1)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    client = TestClient(app)

    authorize(issuer="https://other.auth0.com/")
    assert client.post(PATH).status_code == 403

    authorize()
    assert client.post(PATH).status_code == 200
    # ... and the local principal's own ceiling still binds on its next request.
    assert client.post(PATH).status_code == 429


@pytest.mark.parametrize("sub,issuer", [
    ("auth0|mine", None), ("auth0|mine", "https://other.auth0.com/"),
    ("google-oauth2|mine", "https://tenant.auth0.com/"),
    ("auth0|", "https://tenant.auth0.com/"),
    ("auth0|a|b", "https://tenant.auth0.com/"),
    ("m2m@clients", "https://tenant.auth0.com/"),
])
def test_identity_denied(sub: str, issuer: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    authorize(sub, issuer)
    mock = AsyncMock()
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    response = TestClient(app).post(PATH)
    assert response.status_code == 403
    assert_no_store(response)
    mock.assert_not_awaited()


def test_success_ignores_target_injection_and_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    authorize()
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    response = TestClient(app).post(PATH + "?result_url=https://evil.test", json={"email": "other@test", "user_id": "other"})
    assert response.status_code == 200
    assert response.json() == {"url": URL}
    assert_no_store(response)
    mock.assert_awaited_once_with("auth0|mine")


@pytest.mark.parametrize("field", ["domain", "password_reset_client_id", "management_client_id", "management_client_secret"])
def test_unconfigured(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    authorize()
    monkeypatch.setattr(get_settings().auth0, field, "")
    response = TestClient(app).post(PATH)
    assert response.status_code == 503
    assert_no_store(response)


@pytest.mark.parametrize("error,status", [(RuntimeError("private-secret sensitive"), 502), (management.Auth0ManagementRateLimited(20), 503)])
def test_errors_are_generic(error: Exception, status: int, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    authorize()
    monkeypatch.setattr(router, "create_password_change_ticket", AsyncMock(side_effect=error))
    response = TestClient(app).post(PATH)
    assert response.status_code == status
    assert "private-secret" not in response.text + caplog.text
    assert "sensitive" not in response.text + caplog.text
    assert_no_store(response)
    if status == 503:
        assert response.headers["retry-after"] == "10"


def test_local_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    authorize()
    monkeypatch.setattr(get_settings().ratelimit, "enabled", True)
    monkeypatch.setattr(get_settings().ratelimit, "password_reset_per_window", 1)
    monkeypatch.setattr(get_settings().ratelimit, "password_reset_window_seconds", 300)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    client = TestClient(app)
    assert client.post(PATH).status_code == 200
    response = client.post(PATH)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert_no_store(response)
    mock.assert_awaited_once()


def test_reset_quota_does_not_follow_the_workflow_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH-MIN-002: the reset ceiling is its own policy, not the workflow one."""
    authorize()
    monkeypatch.setattr(get_settings().ratelimit, "password_reset_per_window", 1)
    monkeypatch.setattr(get_settings().ratelimit, "authenticated_per_window", 1000)
    monkeypatch.setattr(router, "create_password_change_ticket", AsyncMock(return_value=URL))
    client = TestClient(app)
    assert client.post(PATH).status_code == 200
    assert client.post(PATH).status_code == 429


def test_ineligible_identity_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quota is charged before eligibility, so a denied principal cannot hammer 403."""
    authorize("google-oauth2|mine")
    monkeypatch.setattr(get_settings().ratelimit, "enabled", True)
    monkeypatch.setattr(get_settings().ratelimit, "password_reset_per_window", 1)
    client = TestClient(app)
    assert client.post(PATH).status_code == 403
    response = client.post(PATH)
    assert response.status_code == 429
    assert_no_store(response)


def install_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Route every AsyncClient -- pooled or one-shot -- through this transport."""
    original = httpx.AsyncClient

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_http_contract_and_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        if request.url.path == "/oauth/token":
            assert body == {"client_id": "m2m-id", "client_secret": "private-secret", "audience": "https://tenant.auth0.com/api/v2/", "grant_type": "client_credentials"}
            return httpx.Response(200, json={"access_token": "private-token", "expires_in": 3600})
        assert str(request.url) == "https://tenant.auth0.com/api/v2/tickets/password-change"
        assert request.headers["authorization"] == "Bearer private-token"
        assert request.extensions["timeout"]["read"] == 10.0
        assert body == {"user_id": "auth0|mine", "client_id": "spa-id", "ttl_sec": 600, "mark_email_as_verified": False, "includeEmailInRedirect": False}
        return httpx.Response(201, json={"ticket": URL})

    install_transport(monkeypatch, httpx.MockTransport(handler))
    assert await management.create_password_change_ticket("auth0|mine") == URL
    assert await management.create_password_change_ticket("auth0|mine") == URL
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503, 0])
async def test_upstream_failure_no_retry(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "_auth_headers", AsyncMock(return_value={"Authorization": "Bearer private"}))
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if status == 0:
            raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(status, json={"message": "secret"})

    install_transport(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises((httpx.HTTPError, management.Auth0ManagementRateLimited)):
        await management.create_password_change_ticket("auth0|mine")
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", [None, 1, "", "http://tenant.auth0.com/reset", "https://evil.test/reset", "https://tenant.auth0.com.evil.test/reset", "https://user@tenant.auth0.com/reset", "https://tenant.auth0.com:443/reset", "https://tenant.auth0.com/reset#secret", "https://tenant.auth0.com/\\evil", "https://tenant.auth0.com/\nreset"])
async def test_unsafe_response(ticket: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "_auth_headers", AsyncMock(return_value={}))
    install_transport(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(201, json={"ticket": ticket})))
    with pytest.raises(management.Auth0ManagementError):
        await management.create_password_change_ticket("auth0|mine")


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", [
    # The shape Auth0 actually returns: New Universal Login appends a bare '#'.
    # urlsplit reports that as an empty fragment, so it is accepted while
    # '...#secret' above is not. Pinned because tightening the fragment check to
    # a substring test would reject every real ticket with the suite still green.
    "https://tenant.auth0.com/u/reset-verify?ticket=abc123#",
    "https://tenant.auth0.com/u/reset-verify?ticket=abc123",
])
async def test_real_world_ticket_is_accepted(ticket: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "_auth_headers", AsyncMock(return_value={}))
    install_transport(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(201, json={"ticket": ticket})))
    assert await management.create_password_change_ticket("auth0|mine") == ticket


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not-json", b"[]", b"{}"])
async def test_malformed_payload(content: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "_auth_headers", AsyncMock(return_value={}))
    install_transport(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(201, content=content)))
    with pytest.raises((ValueError, management.Auth0ManagementError)):
        await management.create_password_change_ticket("auth0|mine")
