from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.config import get_settings
from tests.fixtures.keycloak.container import KeycloakTestRealm
from tests.fixtures.keycloak.tokens import fetch_keycloak_token


@pytest.fixture
def keycloak_auth_settings(keycloak_realm: KeycloakTestRealm, monkeypatch: pytest.MonkeyPatch) -> KeycloakTestRealm:
    """Points get_current_user's JWT verification at the running Keycloak container instead of Auth0.

    get_settings() is lru_cache'd, so there's no dependency-injection seam to
    swap in test config -- this mutates the live Auth0Settings singleton's
    fields in place; monkeypatch reverts each one automatically at teardown.
    Also resets the module-level JWKS cache in auth0.py so a previously
    fetched (Auth0 or prior-test-Keycloak) key set can't leak into this test.
    """
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "issuer", keycloak_realm.issuer)
    monkeypatch.setattr(settings, "jwks_uri", keycloak_realm.jwks_uri)
    monkeypatch.setattr(settings, "audience", keycloak_realm.audience)
    monkeypatch.setattr(settings, "roles_claim", keycloak_realm.roles_claim)
    auth0_module._jwks_cache["keys"] = None
    auth0_module._jwks_cache["fetched_at"] = 0.0
    return keycloak_realm


@pytest_asyncio.fixture
async def keycloak_async_client(keycloak_auth_settings: KeycloakTestRealm) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient wired directly to the FastAPI app via ASGITransport (no real socket/port).

    Depends on `keycloak_auth_settings` so that, by the time any test uses
    this client, Auth0Settings is already pointed at the Keycloak container
    and the JWKS cache has been reset -- every request made through this
    client is verified against the real Keycloak realm.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client


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
