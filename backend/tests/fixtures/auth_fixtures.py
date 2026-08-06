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
) -> AuthenticatedUser:
    return AuthenticatedUser(sub=sub, email=email, roles=roles)


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
