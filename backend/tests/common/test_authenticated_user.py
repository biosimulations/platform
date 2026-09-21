"""P3 #28: AuthenticatedUser is a Pydantic model with fail-closed validation."""

import pytest
from pydantic import ValidationError

from biosim_server.common.auth.auth0 import AuthenticatedUser
from tests.fixtures.auth_fixtures import make_authenticated_user


def test_valid_construction_defaults() -> None:
    user = AuthenticatedUser(sub="auth0|abc", email="user@example.com")
    assert user.sub == "auth0|abc"
    assert user.email == "user@example.com"
    assert user.roles == []
    assert user.permissions == []
    assert user.email_verified is False


def test_none_roles_and_permissions_become_empty_lists() -> None:
    user = AuthenticatedUser.model_validate(
        {"sub": "auth0|abc", "email": None, "roles": None, "permissions": None}
    )
    assert user.roles == []
    assert user.permissions == []
    assert user.email is None


def test_blank_email_becomes_none() -> None:
    user = AuthenticatedUser(sub="auth0|abc", email="   ")
    assert user.email is None


def test_optional_fields_round_trip() -> None:
    user = AuthenticatedUser(
        sub="auth0|abc",
        email="user@example.com",
        roles=["admin", "user"],
        email_verified=True,
        permissions=["read:runs", "write:runs"],
    )
    dumped = user.model_dump()
    assert dumped["roles"] == ["admin", "user"]
    assert dumped["permissions"] == ["read:runs", "write:runs"]
    assert dumped["email_verified"] is True
    restored = AuthenticatedUser.model_validate(dumped)
    assert restored == user


def test_sub_is_required() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedUser(email="user@example.com")  # type: ignore[call-arg]


def test_empty_sub_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedUser(sub="", email=None)


def test_non_string_role_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedUser(sub="auth0|abc", email=None, roles=["admin", 1])  # type: ignore[list-item]


def test_empty_string_permission_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedUser(sub="auth0|abc", email=None, permissions=[""])


def test_roles_as_string_is_rejected() -> None:
    """A string would make `\"admin\" in user.roles` true via substring search."""
    with pytest.raises(ValidationError):
        AuthenticatedUser(sub="auth0|abc", email=None, roles="admin")  # type: ignore[arg-type]


def test_frozen_model_rejects_reassignment() -> None:
    user = make_authenticated_user(roles=["user"])
    with pytest.raises(ValidationError):
        user.sub = "auth0|attacker"


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedUser(sub="auth0|abc", email=None, admin=True)  # type: ignore[call-arg]
