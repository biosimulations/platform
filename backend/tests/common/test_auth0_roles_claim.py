"""
The Post-Login Action runtime assertion (P0 #4).

The Auth0 Action that stamps the roles claim lives in auth0/actions/post-login.js
and is deployed by hand in the Auth0 dashboard. When it is missing, disabled,
or bound to no flow, every require_roles endpoint 403s silently. These tests
pin the WARNING that makes that state visible, and pin that it changes no
authorization outcome.

Hermetic: local RSA keys and a fake JWKS endpoint from tests/fixtures/jwks_fixtures.py,
no container, no network.
"""

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from tests.fixtures.auth_seam import install_auth_seam, make_auth0_settings
from tests.fixtures.jwks_fixtures import (
    FakeClock,
    FakeJwksEndpoint,
    jwks_document,
    make_key,
)

ROLES_CLAIM = "https://api.biosimulations.org/roles"
KEY = make_key("kid-roles")


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points token verification at a local key set and the default roles claim."""
    settings = make_auth0_settings(roles_claim=ROLES_CLAIM)
    install_auth_seam(monkeypatch, settings=settings)

    document = jwks_document(KEY)
    endpoint = FakeJwksEndpoint(responses=[lambda: document])
    monkeypatch.setattr("biosim_server.common.auth.auth0.httpx.AsyncClient", endpoint.client_factory())
    monkeypatch.setattr(auth0_module, "time", FakeClock())

def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=token
    )

@pytest.mark.asyncio
async def test_roles_claim_present_produces_no_warning(
    verifier: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The healthy case must be silent -- a warning that fires normally is noise."""

    token = KEY.token(extra_claims={ROLES_CLAIM: ["admin"]})

    with caplog.at_level("WARNING"):
        user = await get_current_user(_creds(token))

    assert user.roles == ["admin"]
    assert "roles claims" not in caplog.text.lower()

@pytest.mark.asyncio
async def test_missing_roles_claim_names_the_action(
    verifier: None, caplog: pytest.LogCaptureFixture
) -> None:
    """No claim at all == the Action is not deployed. Say so."""
    token = KEY.token()

    with caplog.at_level("WARNING"):
        user = await get_current_user(_creds(token))

    assert user.roles == []
    assert "Post-Login Action" in caplog.text
    assert ROLES_CLAIM in caplog.text
    assert user.sub not in caplog.text

@pytest.mark.asyncio
async def test_empty_roles_claim_names_role_assignment(
    verifier: None, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty list means the Action ran but the user has no roles -- a different fix."""

    token = KEY.token(extra_claims={ROLES_CLAIM: []})
    with caplog.at_level("WARNING"):
        user = await get_current_user(_creds(token))

    assert user.roles == []
    assert "no roles assigned" in caplog.text
    assert "Post-Login Action" not in caplog.text

@pytest.mark.asyncio
async def test_malformed_roles_claim_is_coerced_and_warning(
    verifier: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-list claim (a string, an object) must not crash and must not authorize."""
    token = KEY.token(extra_claims={ROLES_CLAIM: "admin"})

    with caplog.at_level("WARNING"):
        user = await get_current_user(_creds(token))

    assert user.roles == []
    assert "not a list" in caplog.text

@pytest.mark.asyncio
async def test_warning_is_rated_limited(
    verifier: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A tenant with no Action must produce a signal, not one line per request."""
    token = KEY.token()

    with caplog.at_level("WARNING"):
        for _ in range(20):
            await get_current_user(_creds(token))

    assert caplog.text.count("Post-Login Action") == 1

