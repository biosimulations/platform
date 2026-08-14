"""
JWKS cache behaviour: freshness, negative caching, staleness bounds, and
rotation recovery in common/auth/auth0.py.

These are hermetic unit tests -- local RSA keys, a fake JWKS endpoint, and a
fake clock -- so they carry no marker and run in the default CI invocation
(`uv run python -m pytest`, .github/workflows/ci.yaml:26). The live-IdP
counterparts live in tests/common/test_auth0.py and
tests/rbac_demo/test_keycloak_integration.py.
"""

import asyncio
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user, get_optional_user
from biosim_server.config import get_settings
from tests.fixtures.jwks_fixtures import (
    AUDIENCE,
    ISSUER,
    FakeClock,
    FakeJwksEndpoint,
    connect_error,
    http_500,
    jwks_document,
    make_key,
)

KEY_A = make_key("key-a")
KEY_B = make_key("key-b")

@pytest.fixture(autouse=True)
def _clean_jwks_cache() -> Iterator[None]:
    """Every test starts with an empty cache, no armed backoff, no cooldown."""
    auth0_module._reset_jwks_cache()
    yield
    auth0_module._reset_jwks_cache()

@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Points the token verifier at the local test issuer/audience.

    Mirrors tests/fixtures/keycloak/client.py: get_settings() is lru_cache'd,
    so there is no injection seam and the singleton's fields are monkeypatched
    in place (reverted at teardown).
    """
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

def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

def _ok(*keys: Any) -> Any:
    document = jwks_document(*keys)
    return lambda: document

# --------------------------------------------------------------------------
# Happy path and caching
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_fetch_validates_token(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Baseline: a reachable JWKS endpoint verifies a correctly signed token."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(_creds(KEY_A.token(sub="auth0|alice")))

    assert user.sub == "auth0|alice"
    assert endpoint.call_count == 1

@pytest.mark.asyncio
async def test_cached_keys_are_reused_within_the_ttl(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated requests inside the TTL must not re-fetch."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    for _ in range(5):
        await get_current_user(_creds(KEY_A.token()))
        clock.advance(60)

    assert endpoint.call_count == 1

@pytest.mark.asyncio
async def test_concurrent_cold_start_makes_exactly_one_fetch(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    N concurrent first requests collapse to a single outbound JWKS fetch.

    This is the thundering-herd guard (TODO #12), which is why the refresh lock
    ships with this change rather than after it.
    """
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    tokens = [_creds(KEY_A.token()) for _ in range(25)]
    users = await asyncio.gather(*(get_current_user(c) for c in tokens))

    assert len(users) == 25
    assert endpoint.call_count == 1

# --------------------------------------------------------------------------
# Failure handling: 503, Retry-After, never 500
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [connect_error, http_500])
async def test_idp_failure_with_empty_cache_returns_503_with_retry_after(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    """Cold cache + unreachable IdP => 503 with Retry-After, never a 500.

    Parametrised over a transport error and an HTTP error status because the
    original code handled neither and both produced a 500.
    """
    endpoint = FakeJwksEndpoint(responses=[failure])
    _install(monkeypatch, endpoint, FakeClock())

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_A.token()))

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers is not None
    assert exc_info.value.headers["Retry-After"] == "10"
    assert exc_info.value.detail == "Authentication temporarily unavailable"

@pytest.mark.asyncio
async def test_malformed_jwks_body_is_a_failure_not_a_poisoned_cache(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 response that is not a JWKS document must never be cached."""
    endpoint = FakeJwksEndpoint(responses=[lambda: {"error": "not a jwks"}])
    _install(monkeypatch, endpoint, FakeClock())

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_A.token()))

    assert exc_info.value.status_code == 503
    assert auth0_module._jwks_cache["keys"] is None

@pytest.mark.asyncio
async def test_failing_idp_is_probed_at_most_once_per_backoff_window(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative cache: 50 requests during an outage produce 1 outbound call."""
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    for _ in range(50):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_creds(KEY_A.token()))
        assert exc_info.value.status_code == 503
        clock.advance(0.1)  # 5 s total, inside the 10 s window

    assert endpoint.call_count == 1

@pytest.mark.asyncio
async def test_backoff_expires_and_the_idp_recovers(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the backoff window the next request retries and recovery is immediate."""
    endpoint = FakeJwksEndpoint(responses=[connect_error, _ok(KEY_A)])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    with pytest.raises(HTTPException):
        await get_current_user(_creds(KEY_A.token()))
    assert endpoint.call_count == 1

    clock.advance(11)  # past _JWKS_FAILURE_BACKOFF_SECONDS
    user = await get_current_user(_creds(KEY_A.token()))

    assert user.sub == "auth0|test-user"
    assert endpoint.call_count == 2

# --------------------------------------------------------------------------
# Stale-while-revalidate
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_keys_are_served_when_refresh_fails(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A populated cache plus an unreachable IdP still validates tokens -- and warns."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A), connect_error])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(KEY_A.token()))     # populate
    clock.advance(3601)                               # expire the TTL

    with caplog.at_level("WARNING"):
        user = await get_current_user(_creds(KEY_A.token()))

    assert user.sub == "auth0|test-user"
    assert "Serving stale JWKS" in caplog.text

@pytest.mark.asyncio
async def test_keys_past_the_staleness_bound_are_refused(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staleness is bounded: a day-old key set is no longer served."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A), connect_error])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(KEY_A.token()))
    clock.advance(86_401)  # past _JWKS_STALE_MAX_AGE_SECONDS

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_A.token()))

    assert exc_info.value.status_code == 503

# --------------------------------------------------------------------------
# Rotation: unknown kid
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_kid_forces_one_refresh_and_recovers(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Key rotation self-heals without waiting out the 3600 s TTL.

    The cache is populated with KEY_A only; the token is signed with KEY_B,
    which the IdP publishes on the next fetch. The original implementation
    returned 401 here for up to an hour.
    """
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A), _ok(KEY_A, KEY_B)])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(KEY_A.token()))     # populate with KEY_A only
    user = await get_current_user(_creds(KEY_B.token(sub="auth0|rotated")))

    assert user.sub == "auth0|rotated"
    assert endpoint.call_count == 2

@pytest.mark.asyncio
async def test_unknown_kid_flood_is_cooldown_limited(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Tokens with bogus kids must not become an amplification vector.

    30 requests inside the 60 s cooldown produce exactly one forced refresh
    on top of the initial population fetch.
    """
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(KEY_A.token()))
    assert endpoint.call_count == 1

    for _ in range(30):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_creds(KEY_B.token()))
        assert exc_info.value.status_code == 401
        clock.advance(1)  # 30 s total, inside the 60 s cooldown

    assert endpoint.call_count == 2

@pytest.mark.asyncio
async def test_unknown_kid_still_unknown_after_refresh_is_401(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh that does not produce the kid keeps the original 401."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    await get_current_user(_creds(KEY_A.token()))
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_B.token()))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Unknown signing key"

@pytest.mark.asyncio
async def test_unknown_kid_during_an_outage_does_not_bypass_the_backoff(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The forced refresh respects the negative cache.

    Otherwise an unknown-kid flood during an IdP outage would defeat the very
    backoff that outage armed.
    """
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A), connect_error])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(KEY_A.token()))
    clock.advance(3601)
    with pytest.raises(HTTPException):          # TTL refresh fails, arms backoff
        await get_current_user(_creds(KEY_B.token()))
    calls_after_outage = endpoint.call_count

    for _ in range(10):
        with pytest.raises(HTTPException):
            await get_current_user(_creds(KEY_B.token()))
        clock.advance(0.1)

    assert endpoint.call_count == calls_after_outage

# --------------------------------------------------------------------------
# get_optional_user
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_optional_user_propagates_503_but_swallows_401(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IdP outage must not silently downgrade a token-bearing caller to anonymous."""
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    _install(monkeypatch, endpoint, FakeClock())

    with pytest.raises(HTTPException) as exc_info:
        await get_optional_user(_creds(KEY_A.token()))
    assert exc_info.value.status_code == 503

    # A caller with no token at all is unaffected -- and never touches the IdP.
    assert await get_optional_user(None) is None

@pytest.mark.asyncio
async def test_optional_user_returns_none_for_a_bad_token(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401-class faults keep degrading to anonymous, exactly as before."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    assert await get_optional_user(_creds("not-a-jwt")) is None
