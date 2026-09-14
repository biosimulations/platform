"""Ownership helpers in common/auth/roles.py."""

from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from biosim_server.common.auth.auth0 import AuthenticatedUser
from biosim_server.common.auth.roles import (
    authorize_resource_access,
    creation_policy,
    is_owner,
    is_ownerless,
    require_owner_or_admin,
    resource_is_public,
)
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


@dataclass
class _VisRecord:
    owner_sub: str | None = None
    owner: str | None = None
    email: str | None = None
    visibility: str | None = None


def test_creation_policy_anonymous_is_public() -> None:
    owner, visibility = creation_policy(None)
    assert owner is None
    assert visibility == "public"


def test_creation_policy_authenticated_is_private() -> None:
    user = make_authenticated_user()
    owner, visibility = creation_policy(user)
    assert owner == user.sub
    assert visibility == "private"


def test_creation_policy_authenticated_honors_requested_visibility() -> None:
    user = make_authenticated_user()
    assert creation_policy(user, "public") == (user.sub, "public")
    assert creation_policy(user, "private") == (user.sub, "private")
    assert creation_policy(user, None) == (user.sub, "private")


def test_creation_policy_anonymous_requested_private_is_forced_public() -> None:
    """Anonymous execution can never be private: a requested private visibility
    is safely forced to public, and no owner is ever attached."""
    assert creation_policy(None, "private") == (None, "public")
    assert creation_policy(None, "public") == (None, "public")


def test_is_owner_email_fallback_is_legacy_only() -> None:
    """The verified-email fallback is available only for genuinely legacy rows
    (no owner_sub AND no explicit visibility). A newly created anonymous run --
    explicitly public with a client-supplied email -- must never be acquired
    later by a verified user whose email happens to match."""
    verified = AuthenticatedUser(
        sub="auth0|u", email="user@example.com", email_verified=True
    )
    new_anonymous = _VisRecord(owner_sub=None, email="user@example.com", visibility="public")
    assert not is_owner(verified, new_anonymous)
    private_row = _VisRecord(owner_sub=None, email="user@example.com", visibility="private")
    assert not is_owner(verified, private_row)
    legacy = _VisRecord(owner_sub=None, email="user@example.com", visibility=None)
    assert is_owner(verified, legacy)


def test_is_owner_verified_email_never_overrides_populated_owner_sub() -> None:
    verified = AuthenticatedUser(
        sub="auth0|not-the-owner", email="owner@example.com", email_verified=True
    )
    owned = _Record(owner_sub="auth0|the-owner", email="owner@example.com")
    assert not is_owner(verified, owned)


def test_resource_is_public_for_missing_null_and_public() -> None:
    assert resource_is_public(_VisRecord())
    assert resource_is_public(_VisRecord(visibility=None))
    assert resource_is_public(_VisRecord(visibility="public"))
    assert not resource_is_public(_VisRecord(visibility="private"))


def test_authorize_resource_access_allows_public_and_legacy() -> None:
    authorize_resource_access(None, [_VisRecord()], action="view")
    authorize_resource_access(None, [_VisRecord(visibility="public")], action="view")
    authorize_resource_access(
        make_authenticated_user(sub="auth0|other"),
        [_VisRecord(owner_sub="auth0|owner", visibility="public")],
        action="view",
    )


def test_authorize_resource_access_private_owner_allowed() -> None:
    user = make_authenticated_user(sub="auth0|owner")
    authorize_resource_access(
        user,
        [_VisRecord(owner_sub=user.sub, visibility="private")],
        action="view",
    )


def test_authorize_resource_access_private_anonymous_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        authorize_resource_access(
            None,
            [_VisRecord(owner_sub="auth0|owner", visibility="private")],
            action="view",
        )
    assert exc_info.value.status_code == 401
    assert "WWW-Authenticate" in (exc_info.value.headers or {})


def test_authorize_resource_access_private_other_user_403() -> None:
    user = make_authenticated_user(sub="auth0|other")
    with pytest.raises(HTTPException) as exc_info:
        authorize_resource_access(
            user,
            [_VisRecord(owner_sub="auth0|owner", visibility="private")],
            action="view",
        )
    assert exc_info.value.status_code == 403


def test_authorize_resource_access_private_admin_non_owner_denied() -> None:
    admin = make_authenticated_user(sub="auth0|admin", roles=["admin"])
    with pytest.raises(HTTPException) as exc_info:
        authorize_resource_access(
            admin,
            [_VisRecord(owner_sub="auth0|owner", visibility="private")],
            action="view",
        )
    assert exc_info.value.status_code == 403


def test_authorize_resource_access_empty_record_set_fails_closed() -> None:
    with pytest.raises(HTTPException) as exc_info:
        authorize_resource_access(make_authenticated_user(), [], action="view")
    assert exc_info.value.status_code == 403
