"""P3 #25: permission/scope authorization alongside roles, fail closed."""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from biosim_server.common.auth.roles import (
    require_all_permissions,
    require_all_roles,
    require_permissions,
    require_roles,
)
from tests.fixtures.auth_fixtures import make_authenticated_user
from tests.fixtures.auth_seam import install_auth_seam, make_auth0_settings
from tests.fixtures.jwks_fixtures import FakeClock, FakeJwksEndpoint, jwks_document, make_key

KEY = make_key("kid-perm")
ROLES_CLAIM = "https://api.biosimulations.org/roles"


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_auth0_settings(roles_claim=ROLES_CLAIM, permissions_claim="permissions")
    install_auth_seam(monkeypatch, settings=settings)
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(KEY)])
    monkeypatch.setattr(auth0_module.httpx, "AsyncClient", endpoint.client_factory())  # type: ignore[attr-defined]
    monkeypatch.setattr(auth0_module, "time", FakeClock())


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# --------------------------------------------------------------------------
# Token claim extraction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permissions_claim_is_extracted(verifier: None) -> None:
    token = KEY.token(extra_claims={"permissions": ["read:runs", "write:runs"]})
    user = await get_current_user(_creds(token))
    assert user.permissions == ["read:runs", "write:runs"]


@pytest.mark.asyncio
async def test_scope_claim_is_split_into_permissions(verifier: None) -> None:
    token = KEY.token(extra_claims={"scope": "openid profile read:runs"})
    user = await get_current_user(_creds(token))
    assert "read:runs" in user.permissions
    assert "openid" in user.permissions


@pytest.mark.asyncio
async def test_permissions_and_scope_are_merged_without_duplicates(verifier: None) -> None:
    token = KEY.token(
        extra_claims={"permissions": ["read:runs"], "scope": "read:runs write:runs"}
    )
    user = await get_current_user(_creds(token))
    assert user.permissions == ["read:runs", "write:runs"]


@pytest.mark.asyncio
async def test_missing_permissions_claim_is_empty(verifier: None) -> None:
    user = await get_current_user(_creds(KEY.token()))
    assert user.permissions == []


@pytest.mark.asyncio
async def test_malformed_permissions_claim_is_empty(verifier: None) -> None:
    token = KEY.token(extra_claims={"permissions": "read:runs"})
    user = await get_current_user(_creds(token))
    assert user.permissions == []


@pytest.mark.asyncio
async def test_permissions_list_with_non_strings_drops_invalid_entries(verifier: None) -> None:
    token = KEY.token(extra_claims={"permissions": ["read:runs", 1, "", None]})
    user = await get_current_user(_creds(token))
    assert user.permissions == ["read:runs"]


@pytest.mark.asyncio
async def test_roles_do_not_populate_permissions(verifier: None) -> None:
    token = KEY.token(extra_claims={ROLES_CLAIM: ["admin"]})
    user = await get_current_user(_creds(token))
    assert user.roles == ["admin"]
    assert user.permissions == []


# --------------------------------------------------------------------------
# Dependency factories
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_roles_allows_matching_role() -> None:
    user = make_authenticated_user(roles=["admin"])
    result = await require_roles("admin", "publisher")(user)
    assert result is user


@pytest.mark.asyncio
async def test_require_roles_rejects_unauthorized_role() -> None:
    user = make_authenticated_user(roles=["user"])
    with pytest.raises(HTTPException) as exc_info:
        await require_roles("admin")(user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_roles_empty_allowed_fails_closed() -> None:
    user = make_authenticated_user(roles=["admin"])
    with pytest.raises(HTTPException) as exc_info:
        await require_roles()(user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_all_roles_requires_every_role() -> None:
    both = make_authenticated_user(roles=["admin", "publisher"])
    await require_all_roles("admin", "publisher")(both)
    with pytest.raises(HTTPException) as exc_info:
        await require_all_roles("admin", "publisher")(make_authenticated_user(roles=["admin"]))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_all_roles_empty_fails_closed() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_all_roles()(make_authenticated_user(roles=["admin"]))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_permissions_allows_matching_permission() -> None:
    user = make_authenticated_user(permissions=["read:runs", "write:runs"])
    result = await require_permissions("write:runs")(user)
    assert result is user


@pytest.mark.asyncio
async def test_require_permissions_rejects_missing_permission() -> None:
    user = make_authenticated_user(permissions=["read:runs"])
    with pytest.raises(HTTPException) as exc_info:
        await require_permissions("write:runs")(user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_permissions_missing_claim_is_denied() -> None:
    user = make_authenticated_user(permissions=[])
    with pytest.raises(HTTPException) as exc_info:
        await require_permissions("read:runs")(user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_all_permissions_requires_every_permission() -> None:
    user = make_authenticated_user(permissions=["read:runs", "write:runs"])
    await require_all_permissions("read:runs", "write:runs")(user)
    with pytest.raises(HTTPException) as exc_info:
        await require_all_permissions("read:runs", "delete:runs")(user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_role_does_not_satisfy_permission_requirement() -> None:
    """Privilege-escalation guard: admin role is not a substitute for a permission."""
    admin = make_authenticated_user(roles=["admin"], permissions=[])
    with pytest.raises(HTTPException) as exc_info:
        await require_permissions("write:runs")(admin)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_permission_does_not_satisfy_role_requirement() -> None:
    privileged = make_authenticated_user(roles=[], permissions=["admin:all"])
    with pytest.raises(HTTPException) as exc_info:
        await require_roles("admin")(privileged)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_combined_role_and_permission_both_required() -> None:
    qualified = make_authenticated_user(roles=["publisher"], permissions=["delete:runs"])
    await require_roles("publisher")(qualified)
    await require_permissions("delete:runs")(qualified)

    role_only = make_authenticated_user(roles=["publisher"], permissions=[])
    with pytest.raises(HTTPException):
        await require_permissions("delete:runs")(role_only)
