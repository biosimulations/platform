from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.common.auth.auth0 import JwksCache
from tests.fixtures.auth_seam import clear_auth_overrides, install_auth_seam, make_auth0_settings
from tests.fixtures.keycloak.container import (
    NAMESPACED_EMAIL_CLIENT_ID,
    NO_EMAIL_CLIENT_ID,
    KeycloakTestRealm,
)
from tests.fixtures.keycloak.tokens import fetch_keycloak_token


@pytest.fixture
def keycloak_auth_settings(
    keycloak_realm: KeycloakTestRealm, monkeypatch: pytest.MonkeyPatch
) -> KeycloakTestRealm:
    """Point JWT verification at the running Keycloak container via the #24 seam.

    Provides the realm's issuer/JWKS URL/audience/roles claim and a fresh JWKS
    cache through ``get_auth0_settings`` / ``get_jwks_cache`` (and FastAPI
    dependency overrides). Does not mutate the ``get_settings()`` singleton.
    """
    settings = make_auth0_settings(
        issuer=keycloak_realm.issuer,
        jwks_uri=keycloak_realm.jwks_uri,
        audience=keycloak_realm.audience,
        roles_claim=keycloak_realm.roles_claim,
    )
    install_auth_seam(monkeypatch, settings=settings, cache=JwksCache(), app=app)
    return keycloak_realm


@pytest_asyncio.fixture
async def keycloak_async_client(
    keycloak_auth_settings: KeycloakTestRealm,
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient wired directly to the FastAPI app via ASGITransport (no real socket/port).

    Depends on `keycloak_auth_settings` so that, by the time any test uses
    this client, Auth0 settings and a fresh JWKS cache already target the
    Keycloak container -- every request made through this client is verified
    against the real Keycloak realm.
    """
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            yield test_client
    finally:
        clear_auth_overrides(app)


@pytest_asyncio.fixture
async def alice_token(keycloak_auth_settings: KeycloakTestRealm) -> str:
    """Real Keycloak access token for Alice, the realm's admin user."""
    return await fetch_keycloak_token(
        issuer=keycloak_auth_settings.issuer,
        client_id=keycloak_auth_settings.client_id,
        username="alice",
        password="alice-password",
    )


@pytest_asyncio.fixture
async def bob_token(keycloak_auth_settings: KeycloakTestRealm) -> str:
    """Real Keycloak access token for Bob, the realm's publisher user."""
    return await fetch_keycloak_token(
        issuer=keycloak_auth_settings.issuer,
        client_id=keycloak_auth_settings.client_id,
        username="bob",
        password="bob-password",
    )


@pytest_asyncio.fixture
async def charlie_token(keycloak_auth_settings: KeycloakTestRealm) -> str:
    """Real Keycloak access token for Charlie, the realm's plain user (no elevated role)."""
    return await fetch_keycloak_token(
        issuer=keycloak_auth_settings.issuer,
        client_id=keycloak_auth_settings.client_id,
        username="charlie",
        password="charlie-password",
    )


@pytest_asyncio.fixture
async def alice_token_namespaced_email(keycloak_auth_settings: KeycloakTestRealm) -> str:
    """Real Keycloak access token for Alice from NAMESPACED_EMAIL_CLIENT_ID, whose
    protocol mappers stamp both the namespaced "https://api.biosimulations.org/email"
    claim (hardcoded to "Real@Example.com") and a plain "email" claim
    ("alice@example.com") -- lets tests prove get_current_user prefers the
    namespaced claim.
    """
    return await fetch_keycloak_token(
        issuer=keycloak_auth_settings.issuer,
        client_id=NAMESPACED_EMAIL_CLIENT_ID,
        username="alice",
        password="alice-password",
    )


@pytest_asyncio.fixture
async def alice_token_no_email_claim(keycloak_auth_settings: KeycloakTestRealm) -> str:
    """Real Keycloak access token for Alice from NO_EMAIL_CLIENT_ID, whose protocol
    mappers include neither the namespaced nor the plain "email" claim -- lets
    tests prove a missing email claim degrades to None instead of crashing.
    """
    return await fetch_keycloak_token(
        issuer=keycloak_auth_settings.issuer,
        client_id=NO_EMAIL_CLIENT_ID,
        username="alice",
        password="alice-password",
    )
