"""Hosted reset contract; no live Auth0 requests."""
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
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
    monkeypatch.setattr(management, "_token_cache", {"access_token": None, "expires_at": 0.0})
    _reset_rate_limit_state()
    yield
    app.dependency_overrides.pop(get_current_user, None)
    _reset_rate_limit_state()


def authorize(sub: str = "auth0|mine", issuer: str | None = "https://tenant.auth0.com/") -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(sub=sub, issuer=issuer)


def assert_no_store(response: object) -> None:
    """Every response on this route -- success or error -- must be uncacheable."""
    headers = response.headers  # type: ignore[attr-defined]
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "no-referrer"


def test_authentication_required() -> None:
    assert TestClient(app).post(PATH).status_code == 401


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
    monkeypatch.setattr(get_settings().ratelimit, "authenticated_per_window", 1)
    mock = AsyncMock(return_value=URL)
    monkeypatch.setattr(router, "create_password_change_ticket", mock)
    client = TestClient(app)
    assert client.post(PATH).status_code == 200
    response = client.post(PATH)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert_no_store(response)
    mock.assert_awaited_once()


def test_ineligible_identity_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quota is charged before eligibility, so a denied principal cannot hammer 403."""
    authorize("google-oauth2|mine")
    monkeypatch.setattr(get_settings().ratelimit, "enabled", True)
    monkeypatch.setattr(get_settings().ratelimit, "authenticated_per_window", 1)
    client = TestClient(app)
    assert client.post(PATH).status_code == 403
    response = client.post(PATH)
    assert response.status_code == 429
    assert_no_store(response)


def install_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: original(transport=transport))


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
