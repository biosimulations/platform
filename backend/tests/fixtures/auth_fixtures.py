"""Shared auth override for tests that hit endpoints behind get_current_user.

FastAPI's dependency_overrides lets tests bypass real JWT verification while
still exercising the actual endpoint code -- no live Auth0 token needed.
"""

from typing import Iterator

import pytest

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser, get_current_user


def make_authenticated_user(
    sub: str = "auth0|test-user-id",
    email: str | None = "user@example.com",
    roles: list[str] | None = None,
    email_verified: bool = False,
    permissions: list[str] | None = None,
    issuer: str | None = None,
    auth_time: int | None = None,
) -> AuthenticatedUser:
    """A principal for dependency-override tests.

    ``issuer``/``auth_time`` are off by default (the historical fixture shape),
    but endpoints that reason about *which* tenant a subject belongs to -- the
    Auth0 Management guard and the password-reset step-up gate -- must be driven
    with them explicitly rather than relying on a browser-shaped default.
    """
    return AuthenticatedUser(
        sub=sub,
        email=email,
        roles=roles or [],
        email_verified=email_verified,
        permissions=permissions or [],
        issuer=issuer,
        auth_time=auth_time,
    )


@pytest.fixture
def authenticated_user() -> Iterator[AuthenticatedUser]:
    """Overrides get_current_user on the app with a fixed test identity.

    Yields the AuthenticatedUser so tests can assert against its sub/email.
    """
    user = make_authenticated_user()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_current_user, None)
