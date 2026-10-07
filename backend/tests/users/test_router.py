"""Tests for GET/PATCH/DELETE /api/v1/me.

Auth is bypassed via a dependency override; the Auth0 Management API is mocked at
the router's import site, matching the repo's
`@patch("biosim_server.users.<module>.router.get_x")` convention.

The endpoints are guarded in two independent steps, and both are exercised here:
`management_api_configured()` decides whether this deployment has Management
credentials at all (503), and the verified token's *issuer* decides whether the
configured tenant can resolve the subject (403). The latter is what stops a token
issued by a second trusted issuer from reading or mutating the tenant's account
of a same-named subject.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser, get_current_user
from biosim_server.config import get_settings
from tests.fixtures.auth_fixtures import make_authenticated_user

client = TestClient(app)

TENANT_DOMAIN = "tenant.auth0.com"
TENANT_ISSUER = f"https://{TENANT_DOMAIN}/"
FOREIGN_ISSUER = "https://other-tenant.auth0.com/"


@pytest.fixture(autouse=True)
def _configured_tenant(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the configured tenant so the issuer guard is deterministic.

    The test environment has no AUTH0_DOMAIN (and a developer's local .env may
    have one), so both sides of the comparison are fixed explicitly rather than
    read from ambient configuration.
    """
    monkeypatch.setattr(get_settings().auth0, "domain", TENANT_DOMAIN)
    monkeypatch.setattr(get_settings().auth0, "issuer", "")
    yield


@pytest.fixture
def authenticated_user() -> Iterator[AuthenticatedUser]:
    """Overrides get_current_user with a principal from the configured tenant."""
    user = make_authenticated_user(issuer=TENANT_ISSUER)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def foreign_user() -> Iterator[AuthenticatedUser]:
    """A valid principal from a *different* trusted issuer, same subject string."""
    user = make_authenticated_user(issuer=FOREIGN_ISSUER)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def issuerless_user() -> Iterator[AuthenticatedUser]:
    """No verified issuer at all -- must fail closed, never be assumed local."""
    user = make_authenticated_user(issuer=None)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_get_me_requires_authentication() -> None:
    resp = client.get("/api/v1/me")
    assert resp.status_code == 401


@patch("biosim_server.users.router.management_api_configured", return_value=False)
def test_get_me_without_management_api(_mock_configured: AsyncMock, authenticated_user: AuthenticatedUser) -> None:
    resp = client.get("/api/v1/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == authenticated_user.sub
    assert body["email"] == authenticated_user.email
    assert body["provider"] == "auth0"
    assert body["name"] is None


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_enriches_from_management_api(
    _mock_configured: AsyncMock, mock_get_auth0_user: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    mock_get_auth0_user.return_value = {"name": "Jane Doe", "email_verified": True}
    resp = client.get("/api/v1/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Jane Doe"
    assert body["emailVerified"] is True
    mock_get_auth0_user.assert_awaited_once_with(authenticated_user.sub)


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_degrades_on_management_api_failure(
    _mock_configured: AsyncMock, mock_get_auth0_user: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    mock_get_auth0_user.side_effect = Exception("Auth0 is down")
    resp = client.get("/api/v1/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == authenticated_user.sub
    assert body["name"] is None


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_from_a_foreign_issuer_is_jwt_only_and_touches_no_tenant(
    _mock_configured: AsyncMock, mock_get_auth0_user: AsyncMock, foreign_user: AuthenticatedUser
) -> None:
    """A second trusted issuer must never reach the configured tenant's directory.

    The token is a valid credential for this API, but its subject is only
    meaningful inside its own issuer: resolving it here would read -- or later
    mutate -- the tenant account of a same-named subject. The caller still gets
    its own JWT-derived identity; it just gets no tenant enrichment.
    """
    resp = client.get("/api/v1/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == foreign_user.sub
    assert resp.json()["name"] is None
    mock_get_auth0_user.assert_not_awaited()


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_without_a_verified_issuer_gets_no_enrichment(
    _mock_configured: AsyncMock, mock_get_auth0_user: AsyncMock, issuerless_user: AuthenticatedUser
) -> None:
    assert client.get("/api/v1/me").status_code == 200
    mock_get_auth0_user.assert_not_awaited()


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_enriches_a_same_tenant_social_connection(
    _mock_configured: AsyncMock, mock_get_auth0_user: AsyncMock
) -> None:
    """The guard is issuer-based, not `auth0|`-prefix-based: a social connection
    inside the same tenant is a real user of that tenant and keeps enrichment."""
    mock_get_auth0_user.return_value = {"name": "Social User", "email_verified": True}
    user = make_authenticated_user(sub="google-oauth2|10987654321", issuer=TENANT_ISSUER)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get("/api/v1/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Social User"
    mock_get_auth0_user.assert_awaited_once_with("google-oauth2|10987654321")


def test_patch_me_requires_authentication() -> None:
    resp = client.patch("/api/v1/me", json={"name": "New Name"})
    assert resp.status_code == 401


def test_patch_me_rejects_empty_name(authenticated_user: AuthenticatedUser) -> None:
    resp = client.patch("/api/v1/me", json={"name": ""})
    assert resp.status_code == 422


@patch("biosim_server.users.router.management_api_configured", return_value=False)
def test_patch_me_503_when_management_api_unconfigured(
    _mock_configured: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.patch("/api/v1/me", json={"name": "New Name"})
    assert resp.status_code == 503


@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_from_a_foreign_issuer_is_403_with_no_management_call(
    _mock_configured: AsyncMock, mock_update: AsyncMock, foreign_user: AuthenticatedUser
) -> None:
    resp = client.patch("/api/v1/me", json={"name": "Attacker"})
    assert resp.status_code == 403
    mock_update.assert_not_awaited()


@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_without_a_verified_issuer_is_403(
    _mock_configured: AsyncMock, mock_update: AsyncMock, issuerless_user: AuthenticatedUser
) -> None:
    assert client.patch("/api/v1/me", json={"name": "New Name"}).status_code == 403
    mock_update.assert_not_awaited()


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_updates_name(
    _mock_configured: AsyncMock,
    mock_update: AsyncMock,
    mock_get: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    mock_update.return_value = {"name": "New Name"}
    mock_get.return_value = {"name": "New Name", "email_verified": True}
    resp = client.patch("/api/v1/me", json={"name": "New Name"})
    assert resp.status_code == 200
    mock_update.assert_awaited_once_with(authenticated_user.sub, name="New Name")
    assert resp.json()["name"] == "New Name"


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_with_no_fields_makes_no_auth0_update_call(
    _mock_configured: AsyncMock,
    mock_update: AsyncMock,
    mock_get: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    # #22c: a PATCH that sets no permitted field must not call the Management
    # API's update endpoint at all -- it returns the current profile unchanged.
    mock_get.return_value = {"name": "Existing", "email_verified": True}
    resp = client.patch("/api/v1/me", json={})
    assert resp.status_code == 200
    mock_update.assert_not_awaited()
    assert resp.json()["id"] == authenticated_user.sub


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_ignores_arbitrary_fields_and_only_forwards_name(
    _mock_configured: AsyncMock,
    mock_update: AsyncMock,
    mock_get: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    # #22c: an unreviewed field in the request body (here `email`, an
    # authentication-relevant Auth0 attribute) must never reach the Management
    # API. UpdateUserProfileRequest ignores unknown fields, and the router
    # forwards only the explicit `name` keyword, so the arbitrary field is
    # structurally incapable of mutating the Auth0 user record.
    mock_update.return_value = {"name": "New Name"}
    mock_get.return_value = {"name": "New Name", "email_verified": True}
    resp = client.patch(
        "/api/v1/me",
        json={"name": "New Name", "email": "attacker@evil.test", "email_verified": True, "roles": ["admin"]},
    )
    assert resp.status_code == 200
    # Only `name` is forwarded; no email/email_verified/roles keyword reaches Auth0.
    mock_update.assert_awaited_once_with(authenticated_user.sub, name="New Name")
    call = mock_update.await_args
    assert call is not None
    assert set(call.kwargs) == {"name"}


def test_delete_me_requires_authentication() -> None:
    resp = client.delete("/api/v1/me")
    assert resp.status_code == 401


@patch("biosim_server.users.router.management_api_configured", return_value=False)
def test_delete_me_503_when_management_api_unconfigured(
    _mock_configured: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.delete("/api/v1/me")
    assert resp.status_code == 503


@patch("biosim_server.users.router.delete_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_delete_me_from_a_foreign_issuer_is_403_and_deletes_nothing(
    _mock_configured: AsyncMock, mock_delete: AsyncMock, foreign_user: AuthenticatedUser
) -> None:
    """The destructive case the guard exists for: same subject, other issuer.

    Before the guard, a genuinely signed token from a second trusted issuer
    reached ``DELETE /api/v1/me`` and the Management client deleted the
    configured tenant's account for that subject string.
    """
    resp = client.delete("/api/v1/me")
    assert resp.status_code == 403
    mock_delete.assert_not_awaited()


@patch("biosim_server.users.router.delete_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_delete_me_deletes_account(
    _mock_configured: AsyncMock, mock_delete: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.delete("/api/v1/me")
    assert resp.status_code == 204
    mock_delete.assert_awaited_once_with(authenticated_user.sub)
