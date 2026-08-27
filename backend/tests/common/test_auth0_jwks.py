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
import base64
import json
import time
import pytest
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user, get_optional_user
from biosim_server.common.auth.roles import is_owner
from biosim_server.config import Auth0Settings
from tests.fixtures.auth_seam import install_auth_seam
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
def _auth_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh Auth0 settings + JWKS cache for every test (#24)."""
    install_auth_seam(monkeypatch)


@pytest.fixture
def auth_settings(_auth_seam: None) -> None:
    """Kept so existing tests can request isolation without mutating globals."""
    return None


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
@pytest.mark.parametrize(
    ("sub", "detail"),
    [(None, "Invalid token"), ("", "Invalid token"), (42, "Invalid claims")],
    ids=["missing", "empty", "non-string"],
)
async def test_missing_or_invalid_subject_is_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, sub: str | int | None, detail: str
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_A.token(sub=sub)))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expires_in", "expected_status"), [(-30, 200), (-120, 401)],
    ids=["within-leeway", "past-leeway"],
)
async def test_expiration_clock_skew_boundary(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, expires_in: int, expected_status: int
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    if expected_status == 200:
        assert (await get_current_user(_creds(KEY_A.token(expires_in=expires_in)))).sub
    else:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_creds(KEY_A.token(expires_in=expires_in)))
        assert exc_info.value.status_code == expected_status
        assert exc_info.value.detail == "Token expired"


@pytest.mark.asyncio
async def test_not_before_within_clock_skew_is_accepted(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(KEY_A.token(extra_claims={"nbf": int(time.time()) + 30}))
    )
    assert user.sub == "auth0|test-user"


EMAIL_VERIFIED_CLAIM = "https://api.biosimulations.org/email_verified"


@pytest.mark.asyncio
async def test_namespaced_email_verified_claim_populates_the_user(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(KEY_A.token(extra_claims={EMAIL_VERIFIED_CLAIM: True}))
    )
    assert user.email_verified is True


@pytest.mark.asyncio
async def test_absent_email_verified_claim_defaults_false(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(_creds(KEY_A.token()))
    assert user.email_verified is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["true", "false", 1, 0, [True], {"verified": True}, None],
    ids=["str-true", "str-false", "one", "zero", "list", "object", "null"],
)
async def test_malformed_namespaced_email_verified_values_are_unverified(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    """Only the JSON boolean ``true`` verifies. Truthy-but-non-boolean values
    ("true", 1, [true], {...}) stamped by a broken Action must fail closed."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(KEY_A.token(extra_claims={EMAIL_VERIFIED_CLAIM: value}))
    )
    assert user.email_verified is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["true", 1, [True], {"verified": True}],
    ids=["str-true", "one", "list", "object"],
)
async def test_malformed_plain_email_verified_values_are_unverified(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    """The plain OIDC ``email_verified`` fallback is held to the same strict
    boolean rule as the namespaced claim."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(KEY_A.token(extra_claims={"email_verified": value}))
    )
    assert user.email_verified is False


@pytest.mark.asyncio
async def test_namespaced_email_verified_null_does_not_fall_through(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespaced claim that is PRESENT with a null value must not fall
    through to a plain email_verified: true on the same token."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(
            KEY_A.token(
                extra_claims={EMAIL_VERIFIED_CLAIM: None, "email_verified": True}
            )
        )
    )
    assert user.email_verified is False


@pytest.mark.asyncio
async def test_malformed_email_verified_claim_cannot_satisfy_ownership(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a signed token whose email_verified claim is the STRING
    "true" yields a user whose verified-email fallback can never own a legacy
    record, even with a matching email."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(
            KEY_A.token(
                extra_claims={
                    EMAIL_VERIFIED_CLAIM: "true",
                    "https://api.biosimulations.org/email": "victim@example.com",
                }
            )
        )
    )
    assert user.email == "victim@example.com"
    assert user.email_verified is False

    legacy = SimpleNamespace(owner_sub=None, email="victim@example.com")
    assert not is_owner(user, legacy)


@pytest.mark.asyncio
async def test_namespaced_email_verified_false_does_not_fall_through(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespaced claim present-and-False must not fall through to a plain
    email_verified: true on the same token."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    user = await get_current_user(
        _creds(
            KEY_A.token(
                extra_claims={EMAIL_VERIFIED_CLAIM: False, "email_verified": True}
            )
        )
    )
    assert user.email_verified is False

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
    assert auth0_module.get_jwks_cache().keys is None

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
async def test_optional_user_propagates_503_and_does_not_touch_idp_without_token(
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
async def test_optional_user_rejects_a_bad_token(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present-but-invalid token is 401, never anonymous."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    with pytest.raises(HTTPException) as exc_info:
        await get_optional_user(_creds("not-a-jwt"))
    assert exc_info.value.status_code == 401
    assert getattr(exc_info.value, "auth_reason", None) == "malformed"


@pytest.mark.asyncio
async def test_optional_user_supplied_but_unparseable_header_is_401() -> None:
    """A supplied Authorization header from which HTTPBearer could extract no
    Bearer credential (unsupported scheme, empty Bearer, bare scheme) is a
    malformed authentication attempt -- 401, never anonymous."""
    with pytest.raises(HTTPException) as exc_info:
        await get_optional_user(None, authorization="Basic dXNlcjpwYXNz")

    assert exc_info.value.status_code == 401
    assert (
        getattr(exc_info.value, "auth_reason", None) == "invalid_authorization_header"
    )
    challenge = (exc_info.value.headers or {})["WWW-Authenticate"]
    assert 'error="invalid_request"' in challenge


@pytest.mark.asyncio
async def test_optional_user_absent_header_is_anonymous() -> None:
    """Only a completely absent Authorization header is anonymous."""
    assert await get_optional_user(None, authorization=None) is None

# --------------------------------------------------------------------------
# P1 #14 -- the algorithm allowlist is a constant, not environment-overridable
# --------------------------------------------------------------------------

def test_auth0settings_has_no_algorithms_field() -> None:
    """
    The allowlist must not be a Settings field at all -- there must be
    nothing for an ALGORITHMS environment variable to bind to.
    """
    assert "algorithms" not in Auth0Settings.model_fields

def test_algorithms_env_var_has_no_effect_on_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A stray ALGORITHMS env var -- the exact scenario TODO #14 flags -- must be
    inert. Before this change, pydantic-settings' case-insensitive, unaliased
    field binding would have let this silently override the allowlist; there
    is now no field for it to bind to, so the constant is the only source of
    truth regardless of what the environment says.
    """
    monkeypatch.setenv("ALGORITHMS", '["none", "HS256"]')
    settings = Auth0Settings(
        _env_file=None, AUTH0_DOMAIN="tenant.us.auth0.com", AUTH0_AUDIENCE="aud"
    )  # type: ignore[call-arg]
    assert not hasattr(settings, "algorithms")
    assert auth0_module._ALLOWED_ALGORITHMS == ("RS256",)

def _unsigned_none_token(claims: dict[str, Any]) -> str:
    """
    Hand-build a classic alg:none forged token.

    python-jose's own jwt.encode() refuses to produce one -- confirmed against
    this repo's installed python-jose (3.5.0): `jwt.encode({...}, key="",
    algorithm="none")` raises `jose.exceptions.JWSError: Algorithm none not
    supported`. That refusal is itself a small extra defence, but it means the
    forgery this test needs has to be assembled by hand, exactly as a real
    attacker constructing one from scratch would.
    """

    def _b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64(claims)
    return f"{header}.{payload}."

@pytest.mark.asyncio
async def test_alg_none_forged_token_is_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classic algorithm-confusion forgery: a claims payload that would
    pass every downstream check, signed with nothing at all."""
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    forged = _unsigned_none_token(
        {
            "sub": "auth0|attacker",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": int(time.time()) + 3600,

        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(forged))
    assert exc_info.value.status_code == 401

@pytest.mark.asyncio
async def test_hs256_signed_token_is_rejected_even_with_valid_claims(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Cross-algorithm confusion: an HS256 token, correctly self-consistent
    (signed with SOME secret, valid claims), still fails because RS256 is the
    only algorithm jwt.decode is told to accept.

    Confirmed against this repo's python-jose: decoding an HS256 token while
    passing algorithms=["RS256"] raises `jose.exceptions.JWTError: The
    specified alg value is not allowed`, which get_current_user's blanket
    `except Exception` (auth0.py:313-314) maps to 401 "Invalid token".
    """

    from jose import jwt as jose_jwt  # type: ignore[import-untyped]
    
    endpoint = FakeJwksEndpoint(responses=[_ok(KEY_A)])
    _install(monkeypatch, endpoint, FakeClock())

    hs256_token = jose_jwt.encode(
        {
            "sub": "auth0|attacker",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": int(time.time()) +3600,
        },
        key="whatever-an-attacker-guesses-or-finds-public",
        algorithm="HS256",
        headers={"kid": KEY_A.kid},
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(hs256_token))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"
