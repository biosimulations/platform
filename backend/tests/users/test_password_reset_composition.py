"""Mounted composition test: real JWT verification -> issuer-bound route -> ticket.

The router tests in ``test_password_reset.py`` override ``get_current_user``, and
the management tests mock the HTTP client; neither exercises the seam between
them. This module runs the *mounted* route with the real verification path --
local RSA keys, a real JWKS document served over a mocked transport, a real
``jwt.decode``, the trusted-issuer map -- and then asserts the exact downstream
ticket request. A defect that only appears when the pieces are composed (a wrong
subject, a foreign issuer reaching the tenant, a retry after an uncertain
issuance) is caught here and nowhere else.

Hermetic: local keys, ``httpx.MockTransport``, no container, no network, no live
Auth0, no email.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth.auth0 import JwksCache
from biosim_server.common.ratelimit import _reset_rate_limit_state
from biosim_server.config import get_settings
from tests.fixtures.auth_seam import clear_auth_overrides, install_auth_seam, make_auth0_settings
from tests.fixtures.jwks_fixtures import jwks_document, make_key

TENANT_DOMAIN = "test-tenant.auth0.com"
TENANT_ISSUER = f"https://{TENANT_DOMAIN}/"
AUDIENCE = "https://api.example.com/"
OTHER_ISSUER = "https://other-tenant.auth0.com/"
OTHER_AUDIENCE = "https://api.other.example.com/"
TENANT_JWKS = f"https://{TENANT_DOMAIN}/.well-known/jwks.json"
OTHER_JWKS = "https://other-tenant.auth0.com/.well-known/jwks.json"
TICKET = f"https://{TENANT_DOMAIN}/u/reset-verify?ticket=abc123"
PATH = "/api/v1/me/password-reset"

TENANT_KEY = make_key("tenant-key")
OTHER_KEY = make_key("other-key")


class _Auth0:
    """One scripted Auth0: two JWKS documents, a token endpoint, a ticket endpoint."""

    def __init__(self) -> None:
        self.token_posts = 0
        self.ticket_posts = 0
        self.ticket_requests: list[dict[str, Any]] = []
        self.ticket_failure: Exception | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TENANT_JWKS:
            return httpx.Response(200, json=jwks_document(TENANT_KEY))
        if url == OTHER_JWKS:
            return httpx.Response(200, json=jwks_document(OTHER_KEY))
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            return httpx.Response(200, json={"access_token": "m2m-token", "expires_in": 3600})
        if url == f"https://{TENANT_DOMAIN}/api/v2/tickets/password-change":
            self.ticket_posts += 1
            body = json.loads(request.content)
            self.ticket_requests.append(body)
            if self.ticket_failure is not None:
                raise self.ticket_failure
            return httpx.Response(201, json={"ticket": TICKET})
        raise AssertionError(f"unexpected request: {request.method} {url}")


@pytest.fixture
def auth0(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Auth0]:
    """Mounted app configured for two trusted issuers, with a scripted Auth0."""
    auth = _Auth0()
    tenant_config = {
        "domain": TENANT_DOMAIN,
        "issuer": "",
        "jwks_uri": "",
        "audience": "",
        "trusted_issuers_json": json.dumps(
            {
                TENANT_ISSUER: {"audiences": [AUDIENCE], "jwks_uri": TENANT_JWKS},
                OTHER_ISSUER: {"audiences": [OTHER_AUDIENCE], "jwks_uri": OTHER_JWKS},
            }
        ),
        "password_reset_client_id": "spa-id",
        "management_client_id": "m2m-id",
        "management_client_secret": "m2m-secret",
        "password_reset_require_recent_auth": False,
    }
    settings = make_auth0_settings(**tenant_config)
    install_auth_seam(monkeypatch, settings=settings, cache=JwksCache(), app=app)
    # Token verification goes through the injected seam settings, but the route's
    # config gate and the Management client read the process settings. Point both
    # at the same tenant so the composition under test is the real one.
    process_settings = get_settings().auth0
    for field, value in tenant_config.items():
        monkeypatch.setattr(process_settings, field, value)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(auth)

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    _reset_rate_limit_state()
    try:
        yield auth
    finally:
        clear_auth_overrides(app)
        _reset_rate_limit_state()


def _post(token: str) -> httpx.Response:
    return TestClient(app).post(PATH, headers={"Authorization": f"Bearer {token}"})


def test_tenant_issued_token_reaches_a_ticket(auth0: _Auth0) -> None:
    """The whole boundary, no dependency overrides: signed token -> one ticket."""
    token = TENANT_KEY.token(sub="auth0|composition", issuer=TENANT_ISSUER, audience=AUDIENCE)
    response = _post(token)

    assert response.status_code == 200
    assert response.json() == {"url": TICKET}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert auth0.token_posts == 1


def test_ticket_request_uses_the_token_subject_and_configured_spa(auth0: _Auth0) -> None:
    token = TENANT_KEY.token(sub="auth0|composition", issuer=TENANT_ISSUER, audience=AUDIENCE)
    assert _post(token).status_code == 200

    assert auth0.token_posts == 1
    assert auth0.ticket_posts == 1
    assert auth0.ticket_requests == [
        {
            "user_id": "auth0|composition",
            "client_id": "spa-id",
            "ttl_sec": 600,
            "mark_email_as_verified": False,
            "includeEmailInRedirect": False,
        }
    ]


def test_callers_cannot_select_the_target_account(auth0: _Auth0) -> None:
    """Body/query fields must not influence the Management payload."""
    token = TENANT_KEY.token(sub="auth0|composition", issuer=TENANT_ISSUER, audience=AUDIENCE)
    response = TestClient(app).post(
        PATH + "?user_id=auth0|victim&result_url=https://evil.test",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": "auth0|victim", "email": "victim@example.test"},
    )
    assert response.status_code == 200
    assert auth0.ticket_requests[0]["user_id"] == "auth0|composition"


def test_a_second_trusted_issuer_with_the_same_subject_cannot_mint_a_ticket(auth0: _Auth0) -> None:
    """The cross-issuer boundary, end to end.

    The token is genuinely valid for this API -- signature, issuer, audience and
    expiry all check out -- but its subject belongs to a different identity
    domain and must not be handed to the configured tenant's Management API.
    """
    token = OTHER_KEY.token(sub="auth0|composition", issuer=OTHER_ISSUER, audience=OTHER_AUDIENCE)
    response = _post(token)

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert auth0.ticket_posts == 0
    assert auth0.token_posts == 0


@pytest.mark.parametrize(
    "overrides",
    [{"expires_in": -120}, {"audience": "https://wrong.example/"}, {"issuer": OTHER_ISSUER}],
    ids=["expired", "wrong-audience", "untrusted-issuer"],
)
def test_tokens_outside_the_contract_issue_nothing(auth0: _Auth0, overrides: dict[str, Any]) -> None:
    claims: dict[str, Any] = {
        "sub": "auth0|composition",
        "issuer": TENANT_ISSUER,
        "audience": AUDIENCE,
        **overrides,
    }
    response = _post(TENANT_KEY.token(**claims))

    assert response.status_code == 401
    # AUTH-MIN-001: even the dependency-generated 401 is uncacheable.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["www-authenticate"].startswith('Bearer realm="api"')
    assert auth0.ticket_posts == 0


def test_an_uncertain_ticket_failure_is_never_retried(auth0: _Auth0) -> None:
    """A transport failure on the ticket POST is a 502 with exactly one attempt."""
    auth0.ticket_failure = httpx.ConnectError("simulated failure")
    token = TENANT_KEY.token(sub="auth0|composition", issuer=TENANT_ISSUER, audience=AUDIENCE)
    response = _post(token)

    assert response.status_code == 502
    assert auth0.ticket_posts == 1
    # Generic by design: no upstream body, URL or ticket material.
    assert response.json() == {"detail": "Unable to start password reset"}
