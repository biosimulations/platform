"""Integration tests for get_current_user's email-claim parsing against a real
Keycloak dev container -- no mocked JWKS/jwt.decode, no mocked payload. These
exercise the actual JWT-verification path in common/auth/auth0.py (JWKS fetch,
RS256 signature check, issuer/audience check) via real tokens issued by three
purpose-built clients in the test realm (tests/fixtures/keycloak/realm.json):

- "biosim-test-client": stamps a plain "email" claim only -- the fallback path.
- "biosim-test-client-namespaced-email": stamps both a hardcoded namespaced
  "https://api.biosimulations.org/email" claim and a plain "email" claim, to
  prove the namespaced claim wins (mirrors real Auth0 tokens, where a
  Post-Login Action stamps the namespaced claim).
- "biosim-test-client-no-email": stamps neither claim, to prove a missing
  email degrades to None rather than crashing.

End-to-end RBAC coverage (roles, endpoint access) lives separately in
tests/rbac_demo/test_keycloak_integration.py; this file is scoped to the
email-claim precedence/fallback logic in get_current_user.
"""

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth.auth0 import get_current_user

pytestmark = pytest.mark.integration_local


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_get_current_user_reads_namespaced_email_claim(alice_token_namespaced_email: str) -> None:
    """A real token carrying both the namespaced claim and a plain "email" claim
    -- the namespaced claim must be preferred."""
    user = await get_current_user(_creds(alice_token_namespaced_email))
    assert user.email == "real@example.com"


@pytest.mark.asyncio
async def test_get_current_user_falls_back_to_plain_email_claim(alice_token: str) -> None:
    """A real token with only a plain "email" claim (e.g. the Keycloak realm's
    default client, same shape used by tests/rbac_demo/test_keycloak_integration.py)
    keeps working via the fallback."""
    user = await get_current_user(_creds(alice_token))
    assert user.email == "alice@example.com"


@pytest.mark.asyncio
async def test_get_current_user_no_email_claim_at_all(alice_token_no_email_claim: str) -> None:
    """A real token with neither the namespaced nor the plain "email" claim
    (e.g. Auth0 before the Post-Login Action is deployed): email is None, not
    a crash -- callers must handle a missing email explicitly."""
    user = await get_current_user(_creds(alice_token_no_email_claim))
    assert user.email is None
