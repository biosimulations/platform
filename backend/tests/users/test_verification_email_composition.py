"""Signed-token composition for verification resend and profile updates.

Router tests override ``get_current_user``. This module does not. A real RS256
token is checked by ``get_current_user`` against a mocked JWKS document, and the
mounted route then calls the real Management helpers over ``httpx.MockTransport``.
"""

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt  # type: ignore[import-untyped]

from biosim_server.api.main import app
from biosim_server.common.auth.auth0 import JwksCache, get_current_user
from biosim_server.config import get_settings
from tests.fixtures.auth_seam import (
    clear_auth_overrides,
    install_auth_seam,
    make_auth0_settings,
)
from tests.fixtures.jwks_fixtures import jwks_document, make_key

DOMAIN = "tenant.auth0.com"
DOMAIN_ISSUER = f"https://{DOMAIN}/"
CUSTOM_ISSUER = "https://login.example.com/"
FOREIGN_ISSUER = "https://other.example.com/"
AUDIENCE = "https://api.example.test/"
RESEND = "/api/v1/me/resend-verification"
PROFILE = "/api/v1/me"

CUSTOM_KEY = make_key("custom")
DOMAIN_KEY = make_key("domain")
FOREIGN_KEY = make_key("foreign")
KEYS = {
    "login.example.com": CUSTOM_KEY,
    DOMAIN: DOMAIN_KEY,
    "other.example.com": FOREIGN_KEY,
}


class _Auth0:
    def __init__(self) -> None:
        self.token_posts = 0
        self.lookups = 0
        self.patches: list[dict[str, Any]] = []
        self.jobs: list[dict[str, Any]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json=jwks_document(KEYS[host]))
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            return httpx.Response(
                200, json={"access_token": "m2m-token", "expires_in": 3600}
            )
        if request.method == "GET" and "/api/v2/users/" in request.url.path:
            self.lookups += 1
            user_id = unquote(request.url.path.rsplit("/", 1)[-1])
            provider = user_id.split("|", 1)[0]
            return httpx.Response(
                200,
                json={
                    "user_id": user_id,
                    "email": "person@example.test",
                    "email_verified": False,
                    "name": "Ada",
                    "identities": [{"provider": provider, "connection": "database"}],
                },
            )
        if request.method == "PATCH" and "/api/v2/users/" in request.url.path:
            self.patches.append(json.loads(request.content))
            return httpx.Response(200, json=json.loads(request.content))
        if request.url.path == "/api/v2/jobs/verification-email":
            body = json.loads(request.content)
            self.jobs.append(body)
            return httpx.Response(
                201,
                json={"id": "job-1", "type": "verification_email", "status": "pending"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    @property
    def idle(self) -> bool:
        return (
            self.token_posts == self.lookups == len(self.patches) == len(self.jobs) == 0
        )


@pytest.fixture
def auth0(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Auth0]:
    """Two configured-tenant issuers plus one trusted foreign issuer. No user override."""
    auth = _Auth0()
    trusted = {
        DOMAIN_ISSUER: {
            "audiences": [AUDIENCE],
            "jwks_uri": DOMAIN_ISSUER + ".well-known/jwks.json",
        },
        CUSTOM_ISSUER: {
            "audiences": [AUDIENCE],
            "jwks_uri": CUSTOM_ISSUER + ".well-known/jwks.json",
        },
        FOREIGN_ISSUER: {
            "audiences": [AUDIENCE],
            "jwks_uri": FOREIGN_ISSUER + ".well-known/jwks.json",
        },
    }
    config = {
        "domain": DOMAIN,
        "issuer": CUSTOM_ISSUER,
        "audience": "",
        "jwks_uri": "",
        "trusted_issuers_json": json.dumps(trusted),
        "management_client_id": "m2m-id",
        "management_client_secret": "m2m-secret",
    }
    install_auth_seam(
        monkeypatch,
        settings=make_auth0_settings(**config),
        cache=JwksCache(),
        app=app,
    )
    for field, value in config.items():
        monkeypatch.setattr(get_settings().auth0, field, value)
    app.dependency_overrides.pop(get_current_user, None)

    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(auth)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    try:
        yield auth
    finally:
        clear_auth_overrides(app)


def _token(
    key: Any,
    *,
    sub: str = "auth0|database-user",
    issuer: str = CUSTOM_ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 3600,
) -> str:
    return str(
        key.token(sub=sub, issuer=issuer, audience=audience, expires_in=expires_in)
    )


def _resend(token: str | None, *, header: str | None = None) -> httpx.Response:
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return TestClient(app).post(RESEND, headers=headers)


def test_custom_domain_database_token_reaches_one_job(auth0: _Auth0) -> None:
    token = _token(CUSTOM_KEY)
    response = _resend(token)

    assert response.status_code == 202
    assert response.json()["outcome"] == "accepted"
    assert response.headers["cache-control"] == "no-store"
    assert auth0.token_posts == 1
    assert auth0.lookups == 1
    assert auth0.jobs == [{"user_id": "auth0|database-user"}]


def test_domain_convention_issuer_is_also_the_configured_tenant(auth0: _Auth0) -> None:
    token = _token(DOMAIN_KEY, issuer=DOMAIN_ISSUER)
    response = _resend(token)

    assert response.status_code == 202
    assert auth0.jobs == [{"user_id": "auth0|database-user"}]


def test_foreign_signed_token_with_the_same_subject_does_no_management_work(
    auth0: _Auth0,
) -> None:
    token = _token(FOREIGN_KEY, issuer=FOREIGN_ISSUER)
    response = _resend(token)

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert auth0.idle

    profile = TestClient(app).patch(
        PROFILE,
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "other@example.com"},
    )
    assert profile.status_code == 403
    assert auth0.idle


@pytest.mark.parametrize(
    "sub",
    [
        "auth0|",
        "auth0|a|b",
        "auth0|a@clients",
        "auth0|a b",
        "google-oauth2|a",
        "github|a",
        "samlp|a",
        "email|a",
    ],
)
def test_malformed_or_non_database_subjects_send_no_job(
    auth0: _Auth0, sub: str
) -> None:
    response = _resend(_token(CUSTOM_KEY, sub=sub))

    assert response.status_code == 400
    assert auth0.idle


def test_custom_domain_social_name_update_does_not_send_email(auth0: _Auth0) -> None:
    token = _token(CUSTOM_KEY, sub="google-oauth2|social")
    response = TestClient(app).patch(
        PROFILE, headers={"Authorization": f"Bearer {token}"}, json={"name": "New Name"}
    )

    assert response.status_code == 200
    assert auth0.patches == [{"name": "New Name"}]
    assert auth0.jobs == []


def test_custom_domain_email_patch_is_one_server_owned_write(auth0: _Auth0) -> None:
    token = _token(CUSTOM_KEY)
    response = TestClient(app).patch(
        PROFILE,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Ada", "email": "new@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
    assert response.json()["emailVerified"] is False
    assert auth0.patches == [
        {
            "name": "Ada",
            "email": "new@example.com",
            "email_verified": False,
            "verify_email": True,
        }
    ]
    assert auth0.token_posts == 1


@pytest.mark.parametrize(
    ("case", "header"),
    [
        ("wrong-audience", None),
        ("expired", None),
        ("missing-exp", None),
        ("bad-signature", None),
        ("missing-iss", None),
        ("malformed-bearer", "Bearer"),
        ("not-a-jwt", "Bearer not-a-jwt"),
    ],
)
def test_rejected_credentials_do_not_reach_management(
    auth0: _Auth0, case: str, header: str | None
) -> None:
    key = FOREIGN_KEY if case == "bad-signature" else CUSTOM_KEY
    issuer = CUSTOM_ISSUER
    audience = "https://wrong.example/" if case == "wrong-audience" else AUDIENCE
    expires_in = -120 if case == "expired" else 3600
    token = _token(key, issuer=issuer, audience=audience, expires_in=expires_in)
    if case == "missing-exp":
        payload = jwt.get_unverified_claims(token)
        payload.pop("exp")
        token = jwt.encode(
            payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid}
        )
    if case == "missing-iss":
        payload = jwt.get_unverified_claims(token)
        payload.pop("iss")
        token = jwt.encode(
            payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid}
        )
    response = _resend(None if header else token, header=header or f"Bearer {token}")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert auth0.idle


def test_unsigned_request_is_401(auth0: _Auth0) -> None:
    response = _resend(None)
    assert response.status_code == 401
    assert auth0.idle
