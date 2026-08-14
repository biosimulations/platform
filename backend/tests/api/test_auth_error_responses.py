"""
The authentication error taxonomy, asserted at the HTTP layer.

The unit tests in tests/common/test_auth0_jwks.py assert what get_current_user
raises. These assert what a client actually receives: the status line, the
Retry-After header, and the response body -- through the real FastAPI app, its
exception handlers, and its middleware stack.

Both layers are needed. A dependency can raise a perfectly good
HTTPException(503, headers=...) and still have the header dropped by a
middleware or an exception handler; only a through-the-app test catches that.
"""

from typing import Any, Callable, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0 as auth0_module

from biosim_server.config import get_settings
from tests.fixtures.jwks_fixtures import (
    AUDIENCE,
    ISSUER,
    FakeClock,
    FakeJwksEndpoint,
    connect_error,
    jwks_document,
    make_key,
)

# /api/v1/demo/private/me sits behind get_current_user with no role
# requirement, which makes it the narrowest probe of the authentication path
# available (rbac_demo/router.py:41-48). If the rbac_demo router is ever gated
# or removed (TODO #20, P2), repoint these at /api/v1/me -- the auth
# dependency is identical.

PROTECTED_URL = "/api/v1/demo/private/me"

KEY = make_key("kid-http")

@pytest.fixture(autouse=True)
def _clean_jwks_cache() -> Iterator[None]:
    auth0_module._reset_jwks_cache()
    yield
    auth0_module._reset_jwks_cache()

@pytest.fixture
def auth_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "issuer", ISSUER)
    monkeypatch.setattr(settings, "jwks_uri", "https://idp.invalid/.well-known/jwks.json")
    monkeypatch.setattr(settings, "audience", AUDIENCE)

def _install(
    monkeypatch: pytest.MonkeyPatch, endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:

    monkeypatch.setattr(
        auth0_module.httpx,  # type: ignore[attr-defined]
        "AsyncClient",
        endpoint.client_factory(),
    )
    monkeypatch.setattr(auth0_module, "time", clock)

# --------------------------------------------------------------------------
# 503: infrastructure failure
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idp_outage_returns_503_with_retry_after_header(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The P0 #2 headline assertion, from a client's point of view."""

    _install(monkeypatch, FakeJwksEndpoint(responses=[connect_error]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL, headers={"Authorization": f"Bearer {KEY.token()}"}
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert response.json() == {"detail": "Authentication temporarily unavailable"}

@pytest.mark.asyncio
async def test_503_body_leaks_nothing(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error body must not name the JWKS URL, the exception, or the token."""

    token = KEY.token()
    _install(monkeypatch, FakeJwksEndpoint(responses=[connect_error]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL, headers={"Authorization": f"Bearer {token}"}
        )

    body = response.text
    assert "idp.invalid" not in body
    assert "ConnectError" not in body
    assert "jwks" not in body.lower()
    assert token[:20] not in body

@pytest.mark.asyncio
async def test_optional_auth_endpoint_also_returns_503(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An IdP outage must not silently downgrade a token-bearing caller to anonymous.

    POST /simulations/run uses get_optional_user (simulations/router.py:45-47).
    Before the fix, a token-bearing caller during an outage would have been
    treated as anonymous and their run recorded without an owner.
    """
    _install(monkeypatch, FakeJwksEndpoint(responses=[connect_error]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/simulations/run",
            json={"omex_id": "does-not-matter", "simulators": []},
            headers={"Authorization": f"Bearer {KEY.token()}"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"


# --------------------------------------------------------------------------
# 401: token faults -- must NOT gain a Retry-After
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_token_is_401_without_retry_after() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(PROTECTED_URL)

    assert response.status_code == 401
    assert "retry-after" not in response.headers

@pytest.mark.asyncio
async def test_malformed_token_is_401_without_retry_after(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage token is rejected before any JWKS fetch is attempted."""
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(KEY)])
    _install(monkeypatch, endpoint, FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL, headers={"Authorization": "Bearer not-a-jwt"}
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Malformed token"}
    assert "retry-after" not in response.headers
    assert endpoint.call_count == 0

@pytest.mark.asyncio
async def test_expired_token_is_401_not_503(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client fault must never be reported as infrastructure fault."""
    document = jwks_document(KEY)
    _install(monkeypatch, FakeJwksEndpoint(responses=[lambda: document]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL,
            headers={"Authorization": f"Bearer {KEY.token(expires_in=-60)}"},
        )
    
    assert response.status_code == 401
    assert response.json() == {"detail": "Token expired"}

@pytest.mark.asyncio
async def test_wrong_audience_is_401(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = jwks_document(KEY)
    _install(monkeypatch, FakeJwksEndpoint(responses=[lambda: document]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL,
            headers={
                "Authorization": f"Bearer {KEY.token(audience='https://somewhere.else')}"
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid claims"}


@pytest.mark.asyncio
async def test_wrong_issuer_is_401(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = jwks_document(KEY)
    _install(monkeypatch, FakeJwksEndpoint(responses=[lambda: document]), FakeClock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL,
            headers={
                "Authorization": f"Bearer {KEY.token(issuer='https://evil.example.com/')}"
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid claims"}

@pytest.mark.asyncio
async def test_unknown_signing_key_is_401(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token signed by a key the tenant does not publish, even after a refresh."""
    other = make_key("kid-not-published")
    document = jwks_document(KEY)
    _install(monkeypatch, FakeJwksEndpoint(responses=[lambda:document]), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL,
            headers={"Authorization": f"Bearer {other.token()}"}
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unknown signing key"}

# --------------------------------------------------------------------------
# The negative assertion the whole P0 is about
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        [connect_error],
        [lambda: {"error": "not a jwks"}],
        [lambda: {"keys": "not-a-list"}],
        [lambda: {"keys": [{"kid": "kid-http"}]}],
        [lambda: {"keys": []}],
    ],
    ids=["unreachable", "not-a-jwks", "keys-not-a-list", "incomplete-key", "empty-keys"],
)

async def test_no_jwks_failure_mode_produces_a_500(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, responses: list[Callable[[], Any]]
) -> None:
    """
    Five ways the JWKS endpoint can misbehave; none may produce a 500.

    Each of these was a 500 before the P0 #2 change. The first three are
    infrastructure faults (503); the last two yield a key set that simply does
    not contain the token's kid, which is a 401.
    """
    _install(monkeypatch, FakeJwksEndpoint(responses=responses), FakeClock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(
            PROTECTED_URL,
            headers={"Authorization": f"Bearer {KEY.token()}"}
        )

    assert response.status_code != 500
    assert response.status_code in (401, 503)