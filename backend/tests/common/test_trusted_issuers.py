"""P3 #27: explicit issuer → audience mapping, not a cross-product allowlist."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt  # type: ignore[import-untyped]

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from biosim_server.config import Auth0Settings, parse_trusted_issuers_json
from tests.fixtures.auth_seam import install_auth_seam, make_auth0_settings
from tests.fixtures.jwks_fixtures import (
    FakeClock,
    FakeJwksResponse,
    jwks_document,
    make_key,
)

ISSUER_A = "https://tenant-a.auth0.com/"
ISSUER_B = "https://tenant-b.auth0.com/"
AUD_A = "https://api.a.example/"
AUD_B = "https://api.b.example/"
JWKS_A = "https://tenant-a.auth0.com/.well-known/jwks.json"
JWKS_B = "https://tenant-b.auth0.com/.well-known/jwks.json"

KEY_A = make_key("kid-a")
KEY_B = make_key("kid-b")


def _trusted_json(**extra: Any) -> str:
    mapping: dict[str, Any] = {
        ISSUER_A: {"audiences": [AUD_A], "jwks_uri": JWKS_A},
        ISSUER_B: {"audiences": [AUD_B], "jwks_uri": JWKS_B},
    }
    mapping.update(extra)
    return json.dumps(mapping)


class UrlMappedJwks:
    """JWKS endpoint that returns a different document per URL."""

    def __init__(self, mapping: dict[str, dict[str, Any]]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def client_factory(self) -> Any:
        def _factory(*_args: Any, **_kwargs: Any) -> "UrlMappedJwks":
            return self

        return _factory

    async def __aenter__(self) -> "UrlMappedJwks":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str, timeout: float | None = None) -> FakeJwksResponse:
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"JWKS fetch for unconfigured URL: {url}")
        return FakeJwksResponse(self.mapping[url])


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _install_multi(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trusted: str | None = None,
    endpoint: UrlMappedJwks | None = None,
) -> UrlMappedJwks:
    settings = make_auth0_settings(
        trusted_issuers_json=trusted if trusted is not None else _trusted_json(),
        audience="",
        issuer="",
        jwks_uri="",
        domain="",
    )
    install_auth_seam(monkeypatch, settings=settings)
    if endpoint is None:
        endpoint = UrlMappedJwks(
            {
                JWKS_A: jwks_document(KEY_A),
                JWKS_B: jwks_document(KEY_B),
            }
        )
    monkeypatch.setattr(auth0_module.httpx, "AsyncClient", endpoint.client_factory())  # type: ignore[attr-defined]
    monkeypatch.setattr(auth0_module, "time", FakeClock())
    return endpoint


# --------------------------------------------------------------------------
# Configuration parsing
# --------------------------------------------------------------------------


def test_blank_trusted_issuers_is_single_issuer_mode() -> None:
    mapping, errors = parse_trusted_issuers_json("")
    assert mapping == {}
    assert errors == []


def test_malformed_json_is_reported() -> None:
    mapping, errors = parse_trusted_issuers_json("{not json")
    assert mapping == {}
    assert any("not valid JSON" in e for e in errors)


def test_empty_object_is_rejected() -> None:
    mapping, errors = parse_trusted_issuers_json("{}")
    assert mapping == {}
    assert any("empty object" in e for e in errors)


def test_missing_jwks_uri_is_rejected() -> None:
    raw = json.dumps({ISSUER_A: {"audiences": [AUD_A]}})
    mapping, errors = parse_trusted_issuers_json(raw)
    assert mapping == {}
    assert any("jwks_uri" in e for e in errors)


def test_empty_audiences_are_rejected() -> None:
    raw = json.dumps({ISSUER_A: {"audiences": [], "jwks_uri": JWKS_A}})
    mapping, errors = parse_trusted_issuers_json(raw)
    assert mapping == {}
    assert any("audiences" in e for e in errors)


def test_non_url_issuer_is_rejected() -> None:
    raw = json.dumps({"tenant-a": {"audiences": [AUD_A], "jwks_uri": JWKS_A}})
    mapping, errors = parse_trusted_issuers_json(raw)
    assert mapping == {}
    assert any("absolute http(s) URL" in e for e in errors)


def test_valid_mapping_parses() -> None:
    mapping, errors = parse_trusted_issuers_json(_trusted_json())
    assert errors == []
    assert set(mapping) == {ISSUER_A, ISSUER_B}
    assert mapping[ISSUER_A].audiences == (AUD_A,)
    assert mapping[ISSUER_A].jwks_uri == JWKS_A


def test_configuration_errors_accept_trusted_issuers_without_audience() -> None:
    settings = Auth0Settings(
        _env_file=None,  # type: ignore[call-arg]
        AUTH0_DOMAIN="",
        AUTH0_AUDIENCE="",
        AUTH0_TRUSTED_ISSUERS=_trusted_json(),
    )
    assert settings.configuration_errors() == []
    assert settings.has_explicit_trusted_issuers() is True


def test_configuration_errors_report_malformed_trusted_issuers() -> None:
    settings = Auth0Settings(
        _env_file=None,  # type: ignore[call-arg]
        AUTH0_DOMAIN="tenant.us.auth0.com",
        AUTH0_AUDIENCE="https://api.example.test",
        AUTH0_TRUSTED_ISSUERS="{nope",
    )
    errors = settings.configuration_errors()
    assert any("AUTH0_TRUSTED_ISSUERS" in e for e in errors)


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_issuer_and_audience_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    user = await get_current_user(
        _creds(KEY_A.token(issuer=ISSUER_A, audience=AUD_A, sub="auth0|alice"))
    )
    assert user.sub == "auth0|alice"
    assert endpoint.calls == [JWKS_A]


@pytest.mark.asyncio
async def test_valid_issuer_invalid_audience_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(KEY_A.token(issuer=ISSUER_A, audience="https://wrong.example/"))
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid claims"
    assert endpoint.calls == []


@pytest.mark.asyncio
async def test_invalid_issuer_valid_audience_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(KEY_A.token(issuer="https://evil.example.com/", audience=AUD_A))
        )
    assert exc_info.value.status_code == 401
    assert endpoint.calls == []


@pytest.mark.asyncio
async def test_audience_belonging_to_another_issuer_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issuer A presenting issuer B's audience must not pass."""
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(KEY_A.token(issuer=ISSUER_A, audience=AUD_B)))
    assert exc_info.value.status_code == 401
    assert endpoint.calls == []


@pytest.mark.asyncio
async def test_multiple_valid_issuer_audience_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    user_a = await get_current_user(
        _creds(KEY_A.token(issuer=ISSUER_A, audience=AUD_A, sub="auth0|a"))
    )
    user_b = await get_current_user(
        _creds(KEY_B.token(issuer=ISSUER_B, audience=AUD_B, sub="auth0|b"))
    )
    assert user_a.sub == "auth0|a"
    assert user_b.sub == "auth0|b"
    assert endpoint.calls == [JWKS_A, JWKS_B]


@pytest.mark.asyncio
async def test_unknown_issuer_does_not_fetch_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(KEY_A.token(issuer="https://unknown.auth0.com/", audience=AUD_A))
        )
    assert exc_info.value.status_code == 401
    assert endpoint.calls == []


@pytest.mark.asyncio
async def test_issuer_cannot_use_another_issuers_signing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token whose iss is A but is signed with B's key must fail, even if
    both issuers are trusted. Keys are not shared across JWKS URLs."""
    _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(KEY_B.token(issuer=ISSUER_A, audience=AUD_A, sub="auth0|cross"))
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_single_issuer_configuration_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH0_TRUSTED_ISSUERS unset: existing AUTH0_ISSUER/AUDIENCE/JWKS_URI path."""
    from tests.fixtures.jwks_fixtures import AUDIENCE, ISSUER, FakeJwksEndpoint

    settings = make_auth0_settings(trusted_issuers_json="")
    install_auth_seam(monkeypatch, settings=settings)
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(KEY_A)])
    monkeypatch.setattr(auth0_module.httpx, "AsyncClient", endpoint.client_factory())  # type: ignore[attr-defined]
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    user = await get_current_user(_creds(KEY_A.token(issuer=ISSUER, audience=AUDIENCE)))
    assert user.sub == "auth0|test-user"
    assert endpoint.call_count == 1


@pytest.mark.asyncio
async def test_missing_aud_is_rejected_in_single_issuer_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fixtures.jwks_fixtures import ISSUER, FakeJwksEndpoint

    settings = make_auth0_settings(trusted_issuers_json="")
    install_auth_seam(monkeypatch, settings=settings)
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(KEY_A)])
    monkeypatch.setattr(auth0_module.httpx, "AsyncClient", endpoint.client_factory())  # type: ignore[attr-defined]
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    claims = {
        "iss": ISSUER,
        "sub": "auth0|no-aud",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = jose_jwt.encode(claims, KEY_A.private_pem, algorithm="RS256", headers={"kid": KEY_A.kid})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(token))
    assert exc_info.value.status_code == 401
    assert endpoint.call_count == 0


@pytest.mark.asyncio
async def test_hs256_rejected_under_multi_issuer_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _install_multi(monkeypatch)
    hs256_token = jose_jwt.encode(
        {
            "sub": "auth0|attacker",
            "iss": ISSUER_A,
            "aud": AUD_A,
            "exp": int(time.time()) + 3600,
        },
        key="not-an-rsa-key",
        algorithm="HS256",
        headers={"kid": KEY_A.kid},
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(hs256_token))
    assert exc_info.value.status_code == 401
    # Audience/issuer were trusted, so a JWKS fetch is allowed; the algorithm is not.
    assert JWKS_A in endpoint.calls


@pytest.mark.asyncio
async def test_expired_token_rejected_under_multi_issuer_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validly-signed token from a trusted issuer/audience whose ``exp`` is past
    the 60s leeway is a 401 'Token expired'. Expiry is enforced on the multi-issuer
    path too -- and only after issuer A's JWKS has been consulted, unlike the
    issuer/audience rejections that fail before any fetch."""
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(
                KEY_A.token(
                    issuer=ISSUER_A, audience=AUD_A, sub="auth0|expired", expires_in=-120
                )
            )
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token expired"
    assert endpoint.calls == [JWKS_A]


@pytest.mark.asyncio
async def test_id_token_audience_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OIDC ID token's aud is the SPA client id, not the API identifier."""
    endpoint = _install_multi(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            _creds(
                KEY_A.token(
                    issuer=ISSUER_A,
                    audience="spa-client-id",
                    extra_claims={"nonce": "abc", "email": "user@example.com"},
                )
            )
        )
    assert exc_info.value.status_code == 401
    assert endpoint.calls == []


@pytest.mark.asyncio
async def test_issuer_with_two_audiences_accepts_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = json.dumps(
        {
            ISSUER_A: {"audiences": [AUD_A, "https://api.a-alt.example/"], "jwks_uri": JWKS_A},
        }
    )
    endpoint = _install_multi(monkeypatch, trusted=trusted)
    user = await get_current_user(
        _creds(
            KEY_A.token(issuer=ISSUER_A, audience="https://api.a-alt.example/", sub="auth0|alt")
        )
    )
    assert user.sub == "auth0|alt"
    assert endpoint.calls == [JWKS_A]
