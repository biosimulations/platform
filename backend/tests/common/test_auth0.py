"""Authentication contract for common/auth/auth0.py.

Three layers:
  * the optional-auth contract (no credentials -> anonymous; present-but-bad
    credentials -> 401; IdP down -> 503), asserted through a real route,
  * JWT verification negatives against a locally-generated RS256 key, which is
    the only way to produce an expired / wrong-key / wrong-algorithm token,
  * JWKS cache behavior: TTL, single-flight refresh, rotation on unknown kid.

Real-provider verification (a live IdP's JWKS and genuinely-issued tokens) is
covered by tests/rbac_demo/test_keycloak_integration.py and by the Keycloak
section at the bottom of this file.
"""

import asyncio
from datetime import timedelta
from typing import Any, AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from biosim_server.config import get_settings
from tests.fixtures.jwt_fixtures import TEST_AUDIENCE, TEST_ISSUER, SigningKey
from tests.fixtures.keycloak.container import KeycloakTestRealm

# A route with optional auth, so the "no credentials vs bad credentials" split
# is observable end to end rather than only at the dependency.
OPTIONAL_AUTH_ROUTE = "/simulations/runs"
LIST_BODY: dict[str, Any] = {"type": "all"}


@pytest.fixture
def reset_jwks_cache() -> Iterator[None]:
    """The JWKS cache is module-level state; isolate every test from its neighbours."""
    def _clear() -> None:
        auth0_module._jwks_cache["keys"] = None
        auth0_module._jwks_cache["fetched_at"] = 0.0
        auth0_module._jwks_cache["last_forced_at"] = 0.0

    _clear()
    yield
    _clear()


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def local_idp(
    signing_key: SigningKey, reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> SigningKey:
    """Point verification at the locally generated key and its issuer/audience."""
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "issuer", TEST_ISSUER)
    monkeypatch.setattr(settings, "jwks_uri", "https://test-idp.example.com/.well-known/jwks.json")
    monkeypatch.setattr(settings, "audience", TEST_AUDIENCE)

    async def _fake_fetch() -> dict[str, Any]:
        return signing_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _fake_fetch)
    return signing_key


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# --------------------------- optional-auth contract ---------------------------


@pytest.mark.asyncio
async def test_no_authorization_header_is_anonymous(async_client: AsyncClient, local_idp: SigningKey) -> None:
    response = await async_client.post(OPTIONAL_AUTH_ROUTE, json=LIST_BODY)
    # Anonymous is allowed through auth; whether the runs DB is wired up is not
    # this test's business -- what matters is that it is not a 401.
    assert response.status_code != 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        "Bearer",                       # scheme with no credentials
        "Bearer ",                      # empty credentials
        "Basic dXNlcjpwYXNz",           # unsupported scheme
        "token abc123",                 # nonsense scheme
        "Bearer not-a-jwt",             # malformed JWT
        "Bearer a.b.c",                 # three segments, still garbage
    ],
)
async def test_present_but_invalid_credentials_are_401_not_anonymous(
    async_client: AsyncClient, local_idp: SigningKey, header: str
) -> None:
    """`no credentials != invalid credentials` -- a bad token must never buy anonymous access."""
    response = await async_client.post(
        OPTIONAL_AUTH_ROUTE, json=LIST_BODY, headers={"Authorization": header}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_authenticates_on_optional_auth_route(
    async_client: AsyncClient, local_idp: SigningKey
) -> None:
    token = local_idp.token(sub="auth0|alice")
    response = await async_client.post(
        OPTIONAL_AUTH_ROUTE,
        json={"type": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # type=user requires authentication: reaching past the 401 proves the token verified.
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_idp_unavailable_is_503_not_401(
    async_client: AsyncClient, local_idp: SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A JWKS outage must not be reported as an authentication failure."""

    async def _boom() -> dict[str, Any]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _boom)
    token = local_idp.token()
    response = await async_client.post(
        OPTIONAL_AUTH_ROUTE, json=LIST_BODY, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "30"


# --------------------------- JWT verification negatives ---------------------------


async def _authenticate(token: str) -> Any:
    from fastapi.security import HTTPAuthorizationCredentials

    return await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.mark.asyncio
async def test_valid_token_yields_sub_and_roles(local_idp: SigningKey) -> None:
    settings = get_settings().auth0
    token = local_idp.token(
        sub="auth0|alice",
        extra_claims={settings.roles_claim: ["admin"], "email": "Alice@Example.COM "},
    )
    user = await _authenticate(token)
    assert user.sub == "auth0|alice"
    assert user.roles == ["admin"]
    assert user.email == "alice@example.com"


@pytest.mark.asyncio
async def test_expired_token_is_401(local_idp: SigningKey) -> None:
    token = local_idp.token(expires_in=timedelta(minutes=-5))
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token)
    assert exc.value.status_code == 401
    assert "expired" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_wrong_audience_is_401(local_idp: SigningKey) -> None:
    token = local_idp.token(audience="https://someone-elses-api.example.com")
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_is_401(local_idp: SigningKey) -> None:
    token = local_idp.token(issuer="https://evil-idp.example.com/")
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_token_signed_by_another_key_is_401(local_idp: SigningKey) -> None:
    """Same advertised kid, different private key -- signature verification must fail."""
    impostor = SigningKey.generate(kid=local_idp.kid)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(impostor.token())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_hs256_token_is_rejected_by_the_algorithm_allowlist(local_idp: SigningKey) -> None:
    """Algorithm-confusion guard: only RS256 is accepted, whatever the header says."""
    from jose import jwt as jose_jwt  # type: ignore[import-untyped]

    from tests.fixtures.jwt_fixtures import TEST_AUDIENCE as aud, TEST_ISSUER as iss

    forged = jose_jwt.encode(
        {"sub": "auth0|attacker", "aud": aud, "iss": iss, "exp": 9999999999},
        "public-key-as-hmac-secret",
        algorithm="HS256",
        headers={"kid": local_idp.kid},
    )
    with pytest.raises(HTTPException) as exc:
        await _authenticate(forged)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_kid_is_401(local_idp: SigningKey) -> None:
    token = local_idp.token(kid="a-kid-the-idp-never-published")
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token)
    assert exc.value.status_code == 401
    assert "Unknown signing key" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_token_without_sub_is_401(local_idp: SigningKey) -> None:
    """`sub` is the authorization identity; a token without one owns nothing."""
    token = local_idp.token(sub=None)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(token)
    assert exc.value.status_code == 401
    assert "subject" in str(exc.value.detail).lower()


# --------------------------- JWKS cache behavior ---------------------------


@pytest.mark.asyncio
async def test_jwks_is_fetched_once_under_concurrency(
    signing_key: SigningKey, reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the lock, a cold cache means one JWKS fetch per concurrent request."""
    calls = 0

    async def _counting_fetch() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # widen the window a racing caller would slip through
        return signing_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _counting_fetch)
    await asyncio.gather(*(auth0_module._get_jwks() for _ in range(10)))
    assert calls == 1


@pytest.mark.asyncio
async def test_jwks_served_from_cache_within_ttl(
    signing_key: SigningKey, reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def _counting_fetch() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return signing_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _counting_fetch)
    await auth0_module._get_jwks()
    await auth0_module._get_jwks()
    assert calls == 1


@pytest.mark.asyncio
async def test_unknown_kid_triggers_a_rotation_refresh(
    reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kid missing from the cached set means the IdP probably rotated: refetch once."""
    old_key = SigningKey.generate(kid="old-kid")
    new_key = SigningKey.generate(kid="new-kid")
    served = [old_key.jwks(), new_key.jwks()]

    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "issuer", TEST_ISSUER)
    monkeypatch.setattr(settings, "audience", TEST_AUDIENCE)

    async def _rotating_fetch() -> dict[str, Any]:
        return served.pop(0) if served else new_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _rotating_fetch)

    await auth0_module._get_jwks()          # caches the old key set
    user = await _authenticate(new_key.token(sub="auth0|rotated"))
    assert user.sub == "auth0|rotated"


@pytest.mark.asyncio
async def test_rotation_refresh_is_rate_limited(
    signing_key: SigningKey, reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flood of junk kids must not become a fetch storm against the IdP."""
    calls = 0

    async def _counting_fetch() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return signing_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _counting_fetch)
    await auth0_module._get_jwks()
    for _ in range(5):
        await auth0_module._get_jwks(force_refresh=True)
    assert calls == 2  # the initial fetch plus exactly one forced refresh


@pytest.mark.asyncio
async def test_failed_rotation_refresh_keeps_serving_cached_keys(
    signing_key: SigningKey, reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rotation refresh degrades to "unknown kid" (401), not to 503."""

    async def _ok_fetch() -> dict[str, Any]:
        return signing_key.jwks()

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _ok_fetch)
    await auth0_module._get_jwks()

    async def _boom() -> dict[str, Any]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _boom)
    jwks = await auth0_module._get_jwks(force_refresh=True)
    assert jwks == signing_key.jwks()


@pytest.mark.asyncio
async def test_cold_cache_fetch_failure_is_503(
    reset_jwks_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom() -> dict[str, Any]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(auth0_module, "_fetch_jwks", _boom)
    with pytest.raises(HTTPException) as exc:
        await auth0_module._get_jwks()
    assert exc.value.status_code == 503


# --------------------------- real-provider verification ---------------------------


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_real_token_wrong_audience_is_401(
    keycloak_async_client: AsyncClient,
    keycloak_auth_settings: KeycloakTestRealm,
    alice_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely signed token whose `aud` is not ours must not be accepted."""
    monkeypatch.setattr(get_settings().auth0, "audience", "https://not-this-api.example.com")
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/me", headers={"Authorization": f"Bearer {alice_token}"}
    )
    assert response.status_code == 401


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_real_token_wrong_issuer_is_401(
    keycloak_async_client: AsyncClient,
    keycloak_auth_settings: KeycloakTestRealm,
    alice_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings().auth0, "issuer", "https://evil-idp.example.com/")
    response = await keycloak_async_client.get(
        "/api/v1/demo/private/me", headers={"Authorization": f"Bearer {alice_token}"}
    )
    assert response.status_code == 401


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_real_invalid_token_on_optional_auth_route_is_401(
    keycloak_async_client: AsyncClient, keycloak_auth_settings: KeycloakTestRealm
) -> None:
    """The optional-auth contract, against real JWKS-backed verification."""
    response = await keycloak_async_client.post(
        OPTIONAL_AUTH_ROUTE, json=LIST_BODY, headers={"Authorization": "Bearer garbage"}
    )
    assert response.status_code == 401


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_real_valid_token_is_accepted_on_optional_auth_route(
    keycloak_async_client: AsyncClient, keycloak_auth_settings: KeycloakTestRealm, alice_token: str
) -> None:
    response = await keycloak_async_client.post(
        OPTIONAL_AUTH_ROUTE, json={"type": "user"}, headers={"Authorization": f"Bearer {alice_token}"}
    )
    assert response.status_code != 401
