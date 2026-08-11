"""End-to-end RBAC tests against a real Keycloak dev container -- no mocked auth.

Unlike tests/api/test_main.py's demo tests and tests/rbac_demo/test_router.py
(which bypass verification via app.dependency_overrides[get_current_user]),
these exercise the actual JWT-verification path in common/auth/auth0.py --
JWKS fetch, RS256 signature check, issuer/audience check, and roles-claim
extraction -- against real tokens issued by a live Keycloak realm
(tests/fixtures/keycloak/realm.json) for three real users: Alice (admin),
Bob (publisher), Charlie (user, no role).
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration_local


@pytest.mark.asyncio
async def test_public_endpoint_needs_no_token(keycloak_async_client: AsyncClient) -> None:
    """GET /public against the live app with no Authorization header at all.

    Expected: 200 -- confirms the endpoint has no auth dependency and is
    reachable without a token, even against the real JWT-verification path.
    """
    response = await keycloak_async_client.get("/api/v1/demo/public")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_private_me_rejects_missing_token(keycloak_async_client: AsyncClient) -> None:
    """GET /private/me with no Authorization header.

    Expected: 401 -- confirms the real `get_current_user` dependency
    rejects requests with no bearer token before the handler runs.
    """
    response = await keycloak_async_client.get("/api/v1/demo/private/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_private_me_returns_alice(keycloak_async_client: AsyncClient, alice_token: str) -> None:
    """GET /private/me with a real Keycloak-issued token for Alice (admin).

    Expected: 200, with `name` equal to Alice's email -- confirms a real
    JWT is verified end-to-end (JWKS fetch, RS256 signature, issuer/audience
    checks) and its email claim is surfaced correctly.
    """
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/me", headers={"Authorization": f"Bearer {alice_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"name": "alice@example.com"}


@pytest.mark.asyncio
async def test_private_me_returns_bob(keycloak_async_client: AsyncClient, bob_token: str) -> None:
    """GET /private/me with a real Keycloak-issued token for Bob (publisher).

    Expected: 200, with `name` equal to Bob's email -- confirms real-token
    verification and email-claim extraction for a second, differently-roled
    user.
    """
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/me", headers={"Authorization": f"Bearer {bob_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"name": "bob@example.com"}


@pytest.mark.asyncio
async def test_animal_endpoint_rejects_missing_token(keycloak_async_client: AsyncClient) -> None:
    """GET /private/animal with no Authorization header.

    Expected: 401 -- same as /private/me, the real auth dependency blocks
    the request before any role check happens.
    """
    response = await keycloak_async_client.get("/api/v1/demo/private/animal")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_animal_endpoint_returns_zebra_for_alice(keycloak_async_client: AsyncClient, alice_token: str) -> None:
    """GET /private/animal with Alice's real token; Alice holds the "admin" role in Keycloak.

    Expected: 200, with role "admin" and animal "Zebra" -- confirms the
    roles claim extracted from a real, verified JWT is honored by
    `require_roles` and mapped to the correct response.
    """
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/animal", headers={"Authorization": f"Bearer {alice_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"role": "admin", "animal": "Zebra"}


@pytest.mark.asyncio
async def test_animal_endpoint_returns_giraffe_for_bob(keycloak_async_client: AsyncClient, bob_token: str) -> None:
    """GET /private/animal with Bob's real token; Bob holds the "publisher" role in Keycloak.

    Expected: 200, with role "publisher" and animal "Giraffe" -- confirms
    correct role extraction/mapping for the publisher role via a real token.
    """
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/animal", headers={"Authorization": f"Bearer {bob_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"role": "publisher", "animal": "Giraffe"}


@pytest.mark.asyncio
async def test_animal_endpoint_returns_tiger_for_charlie(
    keycloak_async_client: AsyncClient, charlie_token: str
) -> None:
    """GET /private/animal with Charlie's real token; Charlie has no elevated role in Keycloak.

    Expected: 200, with role "user" and animal "Tiger" -- confirms a real
    token with no admin/publisher role still authenticates successfully and
    falls back to the default "user" role/response.
    """
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/animal", headers={"Authorization": f"Bearer {charlie_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"role": "user", "animal": "Tiger"}
