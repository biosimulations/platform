"""Tests for GET/PATCH/DELETE /api/v1/me.

Auth is bypassed via the shared `authenticated_user` fixture (dependency
override); the Auth0 Management API is mocked at the router's import site,
matching the repo's `@patch("biosim_server.<module>.router.get_x")` convention.
"""

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt  # type: ignore[import-untyped]

from biosim_server.config import Auth0Settings

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser, get_current_user
from tests.fixtures.auth_fixtures import make_authenticated_user

client = TestClient(app)

# Auth0 `sub`s whose primary identity is not an Auth0 database connection:
# social (the Google/GitHub logins the profile page recognizes), enterprise
# (SAML), and passwordless email. Their email address and its verification are
# owned upstream, so database-only Management API operations must be refused.
NON_DATABASE_SUBS = [
    "google-oauth2|109876543210987654321",
    "github|1234567",
    "samlp|acme-saml|jane@acme.example",
    "email|5f1c2d3e4b5a69788796a5b4",
]


class _FakeAuth0User:
    """Stateful stand-in for one Auth0 user record, shared by PATCH and GET."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    async def get(self, user_id: str) -> dict[str, Any]:
        assert user_id == self.record["user_id"]
        return dict(self.record)

    async def update(self, user_id: str, **fields: Any) -> dict[str, Any]:
        assert user_id == self.record["user_id"]
        # Auth0 doesn't persist `verify_email`; changing the email resets verification.
        fields.pop("verify_email", None)
        if "email" in fields:
            self.record["email_verified"] = False
        self.record.update(fields)
        return dict(self.record)


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
    # No email on the Auth0 record -> keep the token's email rather than null it out.
    assert body["email"] == authenticated_user.email
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
    assert body["email"] == authenticated_user.email
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


@patch("biosim_server.users.router.get_auth0_user")
@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_updates_email(
    _mock_configured: AsyncMock,
    mock_update: AsyncMock,
    mock_get: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    mock_update.return_value = {"email": "new@example.com", "email_verified": False}
    mock_get.return_value = {"name": "Test User", "email": "new@example.com", "email_verified": False}
    resp = client.patch("/api/v1/me", json={"email": "new@example.com"})
    assert resp.status_code == 200
    mock_update.assert_awaited_once_with(authenticated_user.sub, email="new@example.com", verify_email=True)
    assert resp.json()["email"] == "new@example.com"
    assert resp.json()["emailVerified"] is False


@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_get_me_after_email_change_returns_auth0_email_not_stale_token_email(_mock_configured: AsyncMock) -> None:
    authenticated_user = make_authenticated_user()
    settings = Auth0Settings(
        AUTH0_DOMAIN="tenant.example.auth0.com",
        AUTH0_AUDIENCE="https://test-api.example",
        AUTH0_ISSUER="https://tenant.example.auth0.com/",
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = jwk.construct(private_key.public_key(), "RS256").to_dict()
    public_key.update(kid="test-key", use="sig")
    token = jwt.encode(
        {
            "sub": authenticated_user.sub,
            settings.email_claim: authenticated_user.email,
            "iss": settings.issuer_url(),
            "aud": settings.audience,
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    stale_token_email = authenticated_user.email
    assert stale_token_email == "user@example.com"
    auth0_user = _FakeAuth0User(
        {
            "user_id": authenticated_user.sub,
            "email": stale_token_email,
            "email_verified": True,
            "name": "Test User",
            "identities": [
                {
                    "connection": "Username-Password-Authentication",
                    "provider": "auth0",
                    "user_id": "test-user-id",
                    "isSocial": False,
                }
            ],
        }
    )

    with (
        patch("biosim_server.common.auth.auth0.get_settings", return_value=SimpleNamespace(auth0=settings)),
        patch("biosim_server.common.auth.auth0._get_jwks", AsyncMock(return_value={"keys": [public_key]})),
        patch("biosim_server.users.router.update_auth0_user", AsyncMock(side_effect=auth0_user.update)) as mock_update,
        patch("biosim_server.users.router.get_auth0_user", AsyncMock(side_effect=auth0_user.get)) as mock_get,
    ):
        patch_resp = client.patch("/api/v1/me", json={"email": "new@example.com"}, headers=headers)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["email"] == "new@example.com"
        assert patch_resp.json()["emailVerified"] is False

        # Same token for the follow-up read: the identity decoded from it still
        # carries the pre-change email claim.
        assert authenticated_user.email == stale_token_email
        assert auth0_user.record["email"] == "new@example.com"
        assert jwt.get_unverified_claims(token)[settings.email_claim] == stale_token_email
        get_resp = client.get("/api/v1/me", headers=headers)

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["email"] == "new@example.com"
    assert body["email"] != stale_token_email
    assert body["emailVerified"] is False
    assert body["id"] == authenticated_user.sub
    assert body["name"] == "Test User"
    mock_update.assert_awaited_once_with(authenticated_user.sub, email="new@example.com", verify_email=True)
    mock_get.assert_awaited_once_with(authenticated_user.sub)


@pytest.mark.parametrize("sub", NON_DATABASE_SUBS)
@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_rejects_email_change_for_non_database_accounts(
    _mock_configured: AsyncMock, mock_update: AsyncMock, sub: str
) -> None:
    user = make_authenticated_user(sub=sub)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.patch("/api/v1/me", json={"name": "Jane Doe", "email": "new@example.com"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 400
    assert sub.split("|", 1)[0] in resp.json()["detail"]
    # Rejected outright -- no partial write of the name either.
    mock_update.assert_not_awaited()


@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_name_change_still_allowed_for_social_accounts(
    _mock_configured: AsyncMock, mock_update: AsyncMock
) -> None:
    user = make_authenticated_user(sub="google-oauth2|109876543210987654321")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        mock_update.return_value = {"name": "Jane Doe", "email": "user@example.com", "email_verified": True}
        resp = client.patch("/api/v1/me", json={"name": "Jane Doe"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    mock_update.assert_awaited_once_with(user.sub, name="Jane Doe")
    assert resp.json()["provider"] == "google-oauth2"
    assert resp.json()["name"] == "Jane Doe"


def test_resend_verification_requires_authentication() -> None:
    resp = client.post("/api/v1/me/resend-verification")
    assert resp.status_code == 401


@patch("biosim_server.users.router.management_api_configured", return_value=False)
def test_resend_verification_503_when_management_api_unconfigured(
    _mock_configured: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    resp = client.post("/api/v1/me/resend-verification")
    assert resp.status_code == 503


@patch("biosim_server.users.router.resend_auth0_verification_email")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_resend_verification_success(
    _mock_configured: AsyncMock,
    mock_resend: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    assert authenticated_user.sub.startswith("auth0|")
    mock_resend.return_value = {"type": "verification_email", "status": "pending", "id": "job_abc123"}
    resp = client.post("/api/v1/me/resend-verification")
    assert resp.status_code == 200
    mock_resend.assert_awaited_once_with(authenticated_user.sub)
    assert resp.json()["message"] == "Verification email resent successfully"


@pytest.mark.parametrize("sub", NON_DATABASE_SUBS)
@patch("biosim_server.users.router.resend_auth0_verification_email")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_resend_verification_rejects_non_database_accounts(
    _mock_configured: AsyncMock, mock_resend: AsyncMock, sub: str
) -> None:
    user = make_authenticated_user(sub=sub)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.post("/api/v1/me/resend-verification")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 400
    assert sub.split("|", 1)[0] in resp.json()["detail"]
    # Must not fall through to the database-identity job (no `identity` payload).
    mock_resend.assert_not_awaited()


@patch("biosim_server.users.router.resend_auth0_verification_email")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_resend_verification_502_on_management_api_failure(
    _mock_configured: AsyncMock, mock_resend: AsyncMock, authenticated_user: AuthenticatedUser
) -> None:
    mock_resend.side_effect = Exception("Auth0 is down")
    resp = client.post("/api/v1/me/resend-verification")
    assert resp.status_code == 502


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
