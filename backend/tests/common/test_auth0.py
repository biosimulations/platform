"""Unit tests for get_current_user's claim parsing (roles/email), independent of
real JWT verification -- JWKS fetch and jwt.decode are mocked so these run without
a live JWKS endpoint or a real signed token. End-to-end verification against a real
OIDC provider is covered separately by tests/rbac_demo/test_keycloak_integration.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth.auth0 import get_current_user


def _creds() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")


@pytest.mark.asyncio
@patch("biosim_server.common.auth.auth0._get_jwks", new_callable=AsyncMock)
@patch("biosim_server.common.auth.auth0.jwt")
async def test_get_current_user_reads_namespaced_email_claim(mock_jwt: object, mock_get_jwks: AsyncMock) -> None:
    """Real Auth0 access tokens don't carry a plain "email" claim -- only the
    namespaced claim a Post-Login Action stamps on. This must be preferred."""
    mock_jwt.get_unverified_header.return_value = {"kid": "key-1"}  # type: ignore[attr-defined]
    mock_get_jwks.return_value = {"keys": [{"kid": "key-1", "kty": "RSA", "use": "sig", "n": "n", "e": "e"}]}
    mock_jwt.decode.return_value = {  # type: ignore[attr-defined]
        "sub": "auth0|abc",
        "https://api.biosimulations.org/email": "Real@Example.com",
    }

    user = await get_current_user(_creds())
    assert user.email == "real@example.com"


@pytest.mark.asyncio
@patch("biosim_server.common.auth.auth0._get_jwks", new_callable=AsyncMock)
@patch("biosim_server.common.auth.auth0.jwt")
async def test_get_current_user_falls_back_to_plain_email_claim(mock_jwt: object, mock_get_jwks: AsyncMock) -> None:
    """OIDC providers that DO put email on the access token by default (e.g. the
    Keycloak realm used in integration tests) keep working via this fallback."""
    mock_jwt.get_unverified_header.return_value = {"kid": "key-1"}  # type: ignore[attr-defined]
    mock_get_jwks.return_value = {"keys": [{"kid": "key-1", "kty": "RSA", "use": "sig", "n": "n", "e": "e"}]}
    mock_jwt.decode.return_value = {  # type: ignore[attr-defined]
        "sub": "auth0|abc",
        "email": "fallback@example.com",
    }

    user = await get_current_user(_creds())
    assert user.email == "fallback@example.com"


@pytest.mark.asyncio
@patch("biosim_server.common.auth.auth0._get_jwks", new_callable=AsyncMock)
@patch("biosim_server.common.auth.auth0.jwt")
async def test_get_current_user_no_email_claim_at_all(mock_jwt: object, mock_get_jwks: AsyncMock) -> None:
    """Neither claim present (e.g. Action not deployed yet): email is None, not a
    crash -- callers must handle a missing email explicitly."""
    mock_jwt.get_unverified_header.return_value = {"kid": "key-1"}  # type: ignore[attr-defined]
    mock_get_jwks.return_value = {"keys": [{"kid": "key-1", "kty": "RSA", "use": "sig", "n": "n", "e": "e"}]}
    mock_jwt.decode.return_value = {"sub": "auth0|abc"}  # type: ignore[attr-defined]

    user = await get_current_user(_creds())
    assert user.email is None
