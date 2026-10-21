"""Session-scoped Keycloak dev container, pre-loaded with tests/fixtures/keycloak/realm.json.

The realm defines three real users -- Alice (admin), Bob (publisher), Charlie
(user) -- against a public, direct-grant-enabled client. Tests fetch real
access tokens for them from Keycloak's token endpoint (see tokens.py) and
exercise the actual JWT-verification path in common/auth/auth0.py, not a
mocked one -- see client.py for how Auth0Settings gets pointed at this
container instead of Auth0.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest
import requests
from testcontainers.keycloak import KeycloakContainer  # type: ignore[import-untyped]

# Pinned rather than testcontainers' default ":latest", so the server CI starts
# cannot change underneath it. Must stay >= 25: the readiness probe below relies
# on /health/* being served on the management port.
KEYCLOAK_IMAGE = "quay.io/keycloak/keycloak:26.7.5"
# A cold CI runner pulls the image and imports realm.json before reporting ready.
READINESS_TIMEOUT_SECONDS = 180.0

REALM_NAME = "biosim-test"
CLIENT_ID = "biosim-test-client"
# Same audience/roles mappers as CLIENT_ID, but its own email-claim shape --
# both a hardcoded namespaced "https://api.biosimulations.org/email" claim and
# a plain "email" claim -- so tests can prove the namespaced claim wins.
NAMESPACED_EMAIL_CLIENT_ID = "biosim-test-client-namespaced-email"
# Same audience/roles mappers as CLIENT_ID, but no email mapper at all -- so
# tests can prove a token with neither email claim degrades to None.
NO_EMAIL_CLIENT_ID = "biosim-test-client-no-email"
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


class _ManagementPortKeycloakContainer(KeycloakContainer):  # type: ignore[misc]
    """KeycloakContainer whose readiness probe only polls the management port.

    The upstream probe (testcontainers 4.14) falls back to the HTTP port's
    /health/ready whenever the management port refuses a connection. On
    Keycloak >= 25 that path is a 404, and the resulting HTTPError is not one of
    the errors its retry decorator catches -- so a container that was merely
    still booting failed the whole session (backend-ci run 38454571680: 13 setup
    errors, all "404 ... /health/ready").
    """

    def _readiness_probe(self) -> None:
        url = f"{self.get_management_url()}/health/ready"
        deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
        while True:
            try:
                requests.get(url, timeout=2).raise_for_status()
                return
            except requests.RequestException as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Keycloak was not ready within {READINESS_TIMEOUT_SECONDS:.0f}s: {exc}"
                    ) from exc
                time.sleep(1)


@pytest.fixture(scope="session")
def keycloak_container() -> Iterator[KeycloakContainer]:
    """Starts a single Keycloak dev container for the whole test session, pre-loaded with realm.json.

    Session-scoped because spinning up Keycloak is slow; every test that
    needs a real token shares this one running container instead of paying
    the startup cost per test.
    """
    with _ManagementPortKeycloakContainer(KEYCLOAK_IMAGE).with_realm_import_file(REALM_IMPORT_FILE) as container:
        yield container


@pytest.fixture(scope="session")
def keycloak_realm(keycloak_container: KeycloakContainer) -> KeycloakTestRealm:
    """Derives the running container's issuer/JWKS URLs into a KeycloakTestRealm.

    Session-scoped alongside `keycloak_container` since these URLs are only
    valid for as long as that container is up.
    """
    issuer = f"{keycloak_container.get_url()}/realms/{REALM_NAME}"
    return KeycloakTestRealm(issuer=issuer, jwks_uri=f"{issuer}/protocol/openid-connect/certs")
