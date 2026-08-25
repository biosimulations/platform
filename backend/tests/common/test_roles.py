"""Ownership helpers in common/auth/roles.py."""

from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from biosim_server.common.auth.auth0 import AuthenticatedUser
from biosim_server.common.auth.roles import is_owner, is_ownerless, require_owner_or_admin
from tests.fixtures.auth_fixtures import make_authenticated_user


@dataclass
class _Record:
    owner_sub: str | None
    email: str | None


def test_is_owner_matches_owner_sub() -> None:
    user = make_authenticated_user()
    assert is_owner(user, _Record(owner_sub=user.sub, email="other@example.com"))


def test_is_owner_rejects_owner_sub_mismatch() -> None:
    user = make_authenticated_user()
    assert not is_owner(user, _Record(owner_sub="auth0|someone-else", email=user.email))


def test_is_owner_email_fallback_requires_verified_email() -> None:
    verified = AuthenticatedUser(sub="auth0|u", email="user@example.com", email_verified=True)
    unverified = AuthenticatedUser(sub="auth0|u", email="user@example.com", email_verified=False)
    legacy = _Record(owner_sub=None, email="user@example.com")
    assert is_owner(verified, legacy)
    assert not is_owner(unverified, legacy)


def test_require_owner_or_admin_allows_owner() -> None:
    user = make_authenticated_user()
    require_owner_or_admin(user, [_Record(owner_sub=user.sub, email=user.email)], action="cancel")


def test_require_owner_or_admin_allows_admin_non_owner() -> None:
    admin = make_authenticated_user(roles=["admin"])
    require_owner_or_admin(
        admin, [_Record(owner_sub="auth0|someone-else", email="other@example.com")], action="delete"
    )


def test_require_owner_or_admin_rejects_non_owner(caplog: pytest.LogCaptureFixture) -> None:
    user = make_authenticated_user()
    with caplog.at_level("INFO", logger="biosim_server.common.auth.roles"):
        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(
                user, [_Record(owner_sub="auth0|someone-else", email="other@example.com")], action="cancel"
            )
    assert exc_info.value.status_code == 403
    assert any(
        rec.__dict__.get("auth_outcome") == "forbidden" for rec in caplog.records
    )


def test_require_owner_or_admin_rejects_empty_record_set() -> None:
    user = make_authenticated_user()
    with pytest.raises(HTTPException) as exc_info:
        require_owner_or_admin(user, [], action="view results for")
    assert exc_info.value.status_code == 403


def test_ownerless_record_has_no_identity_attached() -> None:
    assert is_ownerless(_Record(owner_sub=None, email=None))
    assert not is_ownerless(_Record(owner_sub="auth0|u", email=None))
    assert not is_ownerless(_Record(owner_sub=None, email="user@example.com"))
