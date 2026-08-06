"""Tests for the /api/v1/demo/* worked example of Auth0 role-based access control.

Auth is bypassed via FastAPI dependency_overrides (see tests/fixtures/auth_fixtures.py) --
these tests exercise the real endpoint + require_roles logic against a fake
AuthenticatedUser, not a live Auth0 token.
"""

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import get_current_user
from tests.fixtures.auth_fixtures import make_authenticated_user

client = TestClient(app)


@pytest.fixture
def authenticated_admin() -> Iterator[None]:
    user = make_authenticated_user(roles=["admin"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def authenticated_publisher() -> Iterator[None]:
    user = make_authenticated_user(roles=["publisher"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def authenticated_basic_user() -> Iterator[None]:
    user = make_authenticated_user(roles=["user"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_public_endpoint_requires_no_auth() -> None:
    """GET /public with no auth override (no token, no dependency override) at all.

    Expected: 200, with the fixed public-facing message -- confirms the
    endpoint has no auth dependency and is reachable by anyone.
    """
    resp = client.get("/api/v1/demo/public")
    assert resp.status_code == 200
    assert resp.json() == {"message": "This endpoint is public. Anyone can call it."}


def test_whoami_requires_authentication() -> None:
    """GET /private/me with no `get_current_user` override, i.e. unauthenticated.

    Expected: 401 -- the endpoint depends on `get_current_user`, which
    rejects the request before the handler runs.
    """
    resp = client.get("/api/v1/demo/private/me")
    assert resp.status_code == 401


def test_whoami_returns_caller_email() -> None:
    """GET /private/me for a user whose token has an email claim.

    Expected: 200, with `name` set to the user's email -- confirms the
    handler prefers `email` over `sub` when both are present.
    """
    user = make_authenticated_user(email="jane@example.com")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get("/api/v1/demo/private/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert resp.json() == {"name": "jane@example.com"}


def test_whoami_falls_back_to_sub_without_email() -> None:
    """GET /private/me for a user whose token has no email claim.

    Expected: 200, with `name` set to the user's `sub` -- confirms the
    handler falls back to `sub` when `email` is absent.
    """
    user = make_authenticated_user(email=None)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get("/api/v1/demo/private/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert resp.json() == {"name": user.sub}


def test_animal_requires_authentication() -> None:
    """GET /private/animal with no `get_current_user` override, i.e. unauthenticated.

    Expected: 401 -- same as /private/me, auth is required before role
    checks even run.
    """
    resp = client.get("/api/v1/demo/private/animal")
    assert resp.status_code == 401


def test_animal_rejects_authenticated_user_without_required_role() -> None:
    """GET /private/animal for an authenticated user holding an unrecognized role.

    Expected: 403 -- confirms `require_roles` rejects callers whose roles
    don't match any of the endpoint's allowed roles, even though they are
    authenticated.
    """
    user = make_authenticated_user(roles=["some-other-role"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get("/api/v1/demo/private/animal")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 403


def test_animal_returns_zebra_for_admin(authenticated_admin: None) -> None:
    """GET /private/animal for a user with the "admin" role.

    Expected: 200, with role "admin" and animal "Zebra" -- confirms the
    admin role maps to the correct response payload.
    """
    resp = client.get("/api/v1/demo/private/animal")
    assert resp.status_code == 200
    assert resp.json() == {"role": "admin", "animal": "Zebra"}


def test_animal_returns_giraffe_for_publisher(authenticated_publisher: None) -> None:
    """GET /private/animal for a user with the "publisher" role.

    Expected: 200, with role "publisher" and animal "Giraffe" -- confirms
    the publisher role maps to the correct response payload.
    """
    resp = client.get("/api/v1/demo/private/animal")
    assert resp.status_code == 200
    assert resp.json() == {"role": "publisher", "animal": "Giraffe"}


def test_animal_returns_tiger_for_basic_user(authenticated_basic_user: None) -> None:
    """GET /private/animal for a user with the plain "user" role.

    Expected: 200, with role "user" and animal "Tiger" -- confirms the
    basic user role maps to the correct response payload.
    """
    resp = client.get("/api/v1/demo/private/animal")
    assert resp.status_code == 200
    assert resp.json() == {"role": "user", "animal": "Tiger"}


def test_animal_prefers_most_privileged_role_when_multiple_present() -> None:
    """GET /private/animal for a user holding both "user" and "admin" roles.

    Expected: 200, with role "admin" and animal "Zebra" -- confirms that
    when a caller has multiple roles, the endpoint resolves to the most
    privileged one rather than e.g. the first one listed.
    """
    user = make_authenticated_user(roles=["user", "admin"])
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get("/api/v1/demo/private/animal")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert resp.json() == {"role": "admin", "animal": "Zebra"}
