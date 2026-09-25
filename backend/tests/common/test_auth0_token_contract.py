"""
The access-token contract enforced by get_current_user (AUTH-MAJ-002).

Two defects this pins, both confirmed against the installed python-jose:

  * ``exp`` was optional. ``jose.jwt._validate_exp`` returns immediately when
    the claim is absent, so a correctly signed token with no expiry was accepted
    forever -- in every configuration, including the single-issuer ones actually
    deployed. python-jose exposes **no** ``require_exp`` option (it is not part
    of ``_validate_claims``' contract), so the presence check is explicit.
  * ``sub`` was normalized. ``AuthenticatedUser`` strips whitespace, so a padded
    subject was silently rewritten into a *different* identity key and a
    whitespace-only one raised inside the model (a 500, not a 401).

Hermetic: local RSA keys, a fake JWKS endpoint, a fake clock -- no container, no
network, no live Auth0.
"""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt  # type: ignore[import-untyped]

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from tests.fixtures.auth_seam import install_auth_seam
from tests.fixtures.jwks_fixtures import FakeClock, FakeJwksEndpoint, TestKey, jwks_document, make_key

KEY = make_key("contract-key")


@pytest.fixture(autouse=True)
def _auth_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh Auth0 settings + JWKS cache for every test (#24)."""
    install_auth_seam(monkeypatch)


@pytest.fixture
def auth_settings(_auth_seam: None) -> None:
    return None


def _install(monkeypatch: pytest.MonkeyPatch, endpoint: FakeJwksEndpoint) -> None:
    monkeypatch.setattr(
        auth0_module.httpx,  # type: ignore[attr-defined]
        "AsyncClient",
        endpoint.client_factory(),
    )
    monkeypatch.setattr(auth0_module, "time", FakeClock())


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _endpoint() -> FakeJwksEndpoint:
    return FakeJwksEndpoint(responses=[lambda: jwks_document(KEY)])


def _token_without_exp(key: TestKey) -> str:
    """A correctly signed token whose claim set never included `exp`."""
    claims = dict(jose_jwt.get_unverified_claims(key.token()))
    del claims["exp"]
    return str(
        jose_jwt.encode(claims, key.private_pem, algorithm="RS256", headers={"kid": key.kid})
    )


@pytest.mark.asyncio
async def test_a_token_without_exp_is_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing case: a signed token with no bounded lifetime is unusable."""
    endpoint = _endpoint()
    _install(monkeypatch, endpoint)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(_token_without_exp(KEY)))
    assert exc_info.value.status_code == 401
    assert getattr(exc_info.value, "auth_reason", None) == "missing_exp"
    # The token is refused before any identity-provider work.
    assert endpoint.call_count == 0


@pytest.mark.asyncio
async def test_a_null_exp_is_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`exp: null` used to reach ``int(None)`` inside python-jose (a bare TypeError)."""
    _install(monkeypatch, _endpoint())
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY.token(extra_claims={"exp": None})))
    assert exc_info.value.status_code == 401
    assert getattr(exc_info.value, "auth_reason", None) == "missing_exp"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["soon", True, [], {"n": 1}], ids=["str", "bool", "list", "dict"])
async def test_a_non_numeric_exp_is_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, value: object,
) -> None:
    """NumericDate semantics: only a number is an expiry."""
    _install(monkeypatch, _endpoint())
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY.token(extra_claims={"exp": value})))
    assert exc_info.value.status_code == 401
    assert getattr(exc_info.value, "auth_reason", None) in {"invalid_exp", "invalid_claims", "expired"}


@pytest.mark.asyncio
async def test_a_valid_expiry_still_validates(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _endpoint())
    assert (await get_current_user(_creds(KEY.token()))).sub == "auth0|test-user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sub, expected_reason",
    [("auth0|abc ", "invalid_sub"), (" auth0|abc", "invalid_sub"), ("   ", "invalid_sub"), ("", "missing_sub")],
    ids=["trailing-space", "leading-space", "whitespace-only", "empty"],
)
async def test_subjects_that_would_be_rewritten_are_rejected(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, sub: str, expected_reason: str,
) -> None:
    """Rejected, not normalized: rewriting `sub` would collide two identities."""
    _install(monkeypatch, _endpoint())
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY.token(sub=sub)))
    assert exc_info.value.status_code == 401
    assert getattr(exc_info.value, "auth_reason", None) == expected_reason


@pytest.mark.asyncio
async def test_accepted_subjects_are_preserved_byte_for_byte(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal whitespace/case is part of the identity, not decoration."""
    _install(monkeypatch, _endpoint())
    sub = "auth0|MiXeD Case|with space"
    assert (await get_current_user(_creds(KEY.token(sub=sub)))).sub == sub


@pytest.mark.asyncio
async def test_no_success_event_is_emitted_for_a_rejected_subject(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The success event used to fire *before* construction, so a malformed
    subject produced an authentication-success log line plus a 500."""
    import logging

    _install(monkeypatch, _endpoint())
    with caplog.at_level(logging.INFO, logger="biosim_server.common.auth.auth0"):
        with pytest.raises(HTTPException):
            await get_current_user(_creds(KEY.token(sub="   ")))
    text = "\n".join(record.getMessage() for record in caplog.records)
    outcomes = [getattr(record, "auth_outcome", None) for record in caplog.records]
    assert "success" not in outcomes
    assert "Authentication outcome" in text


@pytest.mark.asyncio
async def test_malformed_auth_time_never_satisfies_a_step_up_check(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present-but-malformed `auth_time` is parsed as absent, not as a value."""
    _install(monkeypatch, _endpoint())
    malformed: tuple[object, ...] = ("not-a-number", None, True, [], {})
    for value in malformed:
        user = await get_current_user(_creds(KEY.token(extra_claims={"auth_time": value})))
        assert user.auth_time is None, value


@pytest.mark.asyncio
async def test_a_numeric_auth_time_is_carried_on_the_principal(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _endpoint())
    user = await get_current_user(_creds(KEY.token(extra_claims={"auth_time": 1_700_000_000})))
    assert user.auth_time == 1_700_000_000
