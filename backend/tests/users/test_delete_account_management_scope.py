"""DELETE /api/v1/me through the real Management client, against a scripted Auth0.

The router tests patch ``delete_auth0_user`` away; these run it for real over ``httpx.MockTransport`` (no network,
no credentials) to pin the contract the Auth0 grant must satisfy. Account deletion needs ``delete:users`` on the
Platform backend's Management client (auth0-pulumi biosim-platform/clientGrants.py:
biosim_management_api_m2m_management_grant). Without it Auth0 answers 403 insufficient_scope, which must surface as
an explicit 502 after exactly one attempt: never a 204, and never retried.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.config import get_settings
from tests.fixtures.auth_fixtures import make_authenticated_user

TENANT_DOMAIN = "tenant.auth0.com"
client = TestClient(app)


class _ScriptedAuth0:
    """Answers the M2M token request and records every Management API call."""

    def __init__(self, delete_response: httpx.Response) -> None:
        self.delete_response = delete_response
        self.deletes: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "mgmt-token", "expires_in": 3600})
        if request.method == "DELETE" and request.url.path.startswith("/api/v2/users/"):
            self.deletes.append(request)
            return self.delete_response
        return httpx.Response(404)


@pytest.fixture
def configured_management(monkeypatch: pytest.MonkeyPatch) -> None:
    auth0 = get_settings().auth0
    monkeypatch.setattr(auth0, "domain", TENANT_DOMAIN)
    monkeypatch.setattr(auth0, "issuer", "")
    monkeypatch.setattr(auth0, "management_client_id", "mgmt-client-id")
    monkeypatch.setattr(auth0, "management_client_secret", "mgmt-client-secret")


@pytest.fixture
def user() -> Iterator[AuthenticatedUser]:
    principal = make_authenticated_user(issuer=f"https://{TENANT_DOMAIN}/")
    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        yield principal
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _install(monkeypatch: pytest.MonkeyPatch, auth0: _ScriptedAuth0) -> None:
    # The autouse fixture in tests/conftest.py resets this pooled client after every test.
    monkeypatch.setattr(mgmt, "_http_client", httpx.AsyncClient(transport=httpx.MockTransport(auth0)))


@pytest.mark.usefixtures("configured_management")
def test_delete_me_deletes_the_callers_auth0_user(monkeypatch: pytest.MonkeyPatch, user: AuthenticatedUser) -> None:
    auth0 = _ScriptedAuth0(httpx.Response(204))
    _install(monkeypatch, auth0)

    response = client.delete("/api/v1/me")

    assert response.status_code == 204
    [request] = auth0.deletes
    assert request.url.host == TENANT_DOMAIN
    assert request.url.path == f"/api/v2/users/{user.sub}"
    assert request.headers["authorization"] == "Bearer mgmt-token"


@pytest.mark.usefixtures("configured_management")
def test_delete_without_delete_users_scope_is_an_explicit_502_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, user: AuthenticatedUser
) -> None:
    insufficient_scope = httpx.Response(
        403,
        json={
            "statusCode": 403,
            "error": "Forbidden",
            "message": "Insufficient scope, expected any of: delete:users",
            "errorCode": "insufficient_scope",
        },
    )
    auth0 = _ScriptedAuth0(insufficient_scope)
    _install(monkeypatch, auth0)

    response = client.delete("/api/v1/me")

    assert response.status_code == 502
    assert len(auth0.deletes) == 1  # a 4xx is never retried: deletion is not idempotent to observe
    assert "mgmt-token" not in response.text
    assert "mgmt-client-secret" not in response.text
