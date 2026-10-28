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

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt  # type: ignore[import-untyped]

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser, get_current_user
from biosim_server.common.auth.auth0_management import ResendOutcome
from biosim_server.config import Auth0Settings, get_settings
from tests.fixtures.auth_fixtures import make_authenticated_user

client = TestClient(app)

TENANT_DOMAIN = "tenant.auth0.com"
TENANT_ISSUER = f"https://{TENANT_DOMAIN}/"
FOREIGN_ISSUER = "https://other-tenant.auth0.com/"


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
def test_patch_me_only_forwards_reviewed_profile_fields(
    _mock_configured: AsyncMock,
    mock_update: AsyncMock,
    mock_get: AsyncMock,
    authenticated_user: AuthenticatedUser,
) -> None:
    # Name and email are reviewed profile fields; roles and verification status
    # remain server-controlled and must never be forwarded from request JSON.
    mock_update.return_value = {"name": "New Name"}
    mock_get.return_value = {"name": "New Name", "email_verified": True}
    resp = client.patch(
        "/api/v1/me",
        json={"name": "New Name", "email": "new@example.com", "email_verified": True, "roles": ["admin"]},
    )
    assert resp.status_code == 422
    mock_update.assert_not_awaited()
    mock_get.assert_not_awaited()


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
    authenticated_user = make_authenticated_user(issuer=TENANT_ISSUER)
    settings = Auth0Settings(
        AUTH0_DOMAIN=TENANT_DOMAIN,
        AUTH0_AUDIENCE="https://test-api.example",
        AUTH0_ISSUER=TENANT_ISSUER,
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
        patch("biosim_server.common.auth.auth0._jwks_cache.get", AsyncMock(return_value={"keys": [public_key]})),
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
    user = make_authenticated_user(sub=sub, issuer=TENANT_ISSUER)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.patch("/api/v1/me", json={"name": "Jane Doe", "email": "new@example.com"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 400
    assert "email/password accounts" in resp.json()["detail"]
    # Rejected outright -- no partial write of the name either.
    mock_update.assert_not_awaited()


@patch("biosim_server.users.router.update_auth0_user")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_patch_me_name_change_still_allowed_for_social_accounts(
    _mock_configured: AsyncMock, mock_update: AsyncMock
) -> None:
    user = make_authenticated_user(sub="google-oauth2|109876543210987654321", issuer=TENANT_ISSUER)
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
    mock_resend.return_value = ResendOutcome.ACCEPTED
    resp = client.post("/api/v1/me/resend-verification")
    assert resp.status_code == 202
    mock_resend.assert_awaited_once_with(authenticated_user.sub)
    assert resp.json()["message"] == "Verification email request accepted. Check your inbox."


@pytest.mark.parametrize("sub", NON_DATABASE_SUBS)
@patch("biosim_server.users.router.resend_auth0_verification_email")
@patch("biosim_server.users.router.management_api_configured", return_value=True)
def test_resend_verification_rejects_non_database_accounts(
    _mock_configured: AsyncMock, mock_resend: AsyncMock, sub: str
) -> None:
    user = make_authenticated_user(sub=sub, issuer=TENANT_ISSUER)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.post("/api/v1/me/resend-verification")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 400
    assert "email/password accounts" in resp.json()["detail"]
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
