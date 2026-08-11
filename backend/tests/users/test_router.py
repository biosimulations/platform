"""Tests for GET/PATCH/DELETE /api/v1/me.

Auth is bypassed via the shared `authenticated_user` fixture (dependency
override); the Auth0 Management API is mocked at the router's import site,
matching the repo's `@patch("biosim_server.<module>.router.get_x")` convention.
"""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser

client = TestClient(app)


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


def test_patch_me_requires_authentication() -> None:
    resp = client.patch("/api/v1/me", json={"name": "New Name"})
    assert resp.status_code == 401


@patch("biosim_server.users.router.management_api_configured", return_value=False)
def test_patch_me_503_when_management_api_unconfigured(
    _mock_configured: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.patch("/api/v1/me", json={"name": "New Name"})
    assert resp.status_code == 503


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
def test_delete_me_deletes_account(
    _mock_configured: AsyncMock, mock_delete: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.delete("/api/v1/me")
    assert resp.status_code == 204
    mock_delete.assert_awaited_once_with(authenticated_user.sub)
