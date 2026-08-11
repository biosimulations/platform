"""Session-scoped Keycloak dev container, pre-loaded with tests/fixtures/keycloak/realm.json.

The realm defines three real users -- Alice (admin), Bob (publisher), Charlie
(user) -- against a public, direct-grant-enabled client. Tests fetch real
access tokens for them from Keycloak's token endpoint (see tokens.py) and
exercise the actual JWT-verification path in common/auth/auth0.py, not a
mocked one -- see client.py for how Auth0Settings gets pointed at this
container instead of Auth0.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest
from testcontainers.keycloak import KeycloakContainer  # type: ignore[import-untyped]

REALM_NAME = "biosim-test"
CLIENT_ID = "biosim-test-client"
AUDIENCE = "biosim-test-api"
# Matches the "flat-realm-roles" mapper's claim.name in realm.json -- a plain
# top-level claim, not Keycloak's default nested `realm_access.roles`, so it
# reads the same way Auth0's namespaced custom claim does in auth0.py.
ROLES_CLAIM = "roles"

REALM_IMPORT_FILE = str(Path(__file__).parent / "realm.json")


@dataclass(frozen=True)
class KeycloakTestRealm:
    """Coordinates needed to point the app's JWT verification and token-fetching at this realm."""

    issuer: str
    jwks_uri: str
    client_id: str = CLIENT_ID
    audience: str = AUDIENCE
    roles_claim: str = ROLES_CLAIM


@pytest.fixture(scope="session")
def keycloak_container() -> Iterator[KeycloakContainer]:
    """Starts a single Keycloak dev container for the whole test session, pre-loaded with realm.json.

    Session-scoped because spinning up Keycloak is slow; every test that
    needs a real token shares this one running container instead of paying
    the startup cost per test.
    """
    with KeycloakContainer().with_realm_import_file(REALM_IMPORT_FILE) as container:
        yield container


@pytest.fixture(scope="session")
def keycloak_realm(keycloak_container: KeycloakContainer) -> KeycloakTestRealm:
    """Derives the running container's issuer/JWKS URLs into a KeycloakTestRealm.

    Session-scoped alongside `keycloak_container` since these URLs are only
    valid for as long as that container is up.
    """
    issuer = f"{keycloak_container.get_url()}/realms/{REALM_NAME}"
    return KeycloakTestRealm(issuer=issuer, jwks_uri=f"{issuer}/protocol/openid-connect/certs")
