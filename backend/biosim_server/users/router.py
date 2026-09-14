import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.common.auth.auth0_management import (
    delete_auth0_user,
    get_auth0_user,
    management_api_configured,
    resend_auth0_verification_email,
    update_auth0_user,
)
from biosim_server.users.models import UpdateUserProfileRequest, UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Users"])

# `sub` prefix Auth0 gives every database-connection (email/password) user.
_DATABASE_PROVIDER = "auth0"


def _provider_from_sub(sub: str) -> str:
    # Auth0 `sub` claims are "<connection>|<user id>", e.g. "auth0|abc123" or
    # "google-oauth2|10987654321". No '|' (shouldn't happen for Auth0-issued
    # tokens) falls back to the whole sub rather than raising.
    return sub.split("|", 1)[0]


def _apply_auth0_user(profile: UserProfile, auth0_user: dict[str, Any]) -> UserProfile:
    # Auth0's record wins over the token for email: after PATCH /me changes it,
    # the caller's token keeps the old email claim until it is reissued. Keep the
    # token's email only if Auth0 has none on file.
    profile.email = auth0_user.get("email") or profile.email
    profile.name = auth0_user.get("name")
    profile.email_verified = auth0_user.get("email_verified")
    return profile


async def _build_profile(user: AuthenticatedUser) -> UserProfile:
    profile = UserProfile(id=user.sub, email=user.email, provider=_provider_from_sub(user.sub))
    if management_api_configured():
        try:
            _apply_auth0_user(profile, await get_auth0_user(user.sub))
        except Exception as e:
            # Best-effort enrichment -- a flaky Management API call shouldn't
            # break reading your own JWT-derived identity.
            logger.warning(f"Failed to enrich profile for {user.sub} from Auth0 Management API: {e}")
    return profile


def _require_management_api() -> None:
    if not management_api_configured():
        raise HTTPException(
            status_code=503,
            detail="Auth0 Management API not configured (AUTH0_MANAGEMENT_CLIENT_ID/SECRET unset)",
        )


def _require_database_account(user: AuthenticatedUser, action: str) -> None:
    # The verified `sub` is the Auth0 user_id, whose prefix names the user's
    # primary identity -- the one Auth0's email update and verification-email job
    # act on. Social, enterprise and passwordless identities take their email and
    # its verified status from the upstream provider (Auth0 overwrites root email
    # changes on their next login), so refuse rather than run the database flow.
    provider = _provider_from_sub(user.sub)
    if provider != _DATABASE_PROVIDER:
        raise HTTPException(
            status_code=400,
            detail=f"{action} is only available for email/password accounts, not '{provider}' sign-ins",
        )


@router.get(
    "/me",
    response_model=UserProfile,
    operation_id="get-current-user",
    summary="Get the authenticated user's profile",
)
async def get_me(user: AuthenticatedUser = Depends(get_current_user)) -> UserProfile:
    return await _build_profile(user)


@router.patch(
    "/me",
    response_model=UserProfile,
    operation_id="update-current-user",
    summary="Update the authenticated user's profile",
)
async def update_me(
    request: UpdateUserProfileRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfile:
    _require_management_api()
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        return await _build_profile(user)
    if "email" in updates:
        _require_database_account(user, "Changing your email")
        if updates["email"]:
            updates["verify_email"] = True
    try:
        auth0_user = await update_auth0_user(user.sub, **updates)
    except Exception as e:
        logger.error(f"Failed to update Auth0 user {user.sub}: {e}", exc_info=e)
        raise HTTPException(status_code=502, detail="Failed to update profile via Auth0 Management API")
    return _apply_auth0_user(
        UserProfile(id=user.sub, email=user.email, provider=_provider_from_sub(user.sub)),
        auth0_user,
    )


@router.post(
    "/me/resend-verification",
    operation_id="resend-verification-email",
    summary="Resend verification email to the authenticated user",
)
async def resend_verification(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str]:
    _require_management_api()
    _require_database_account(user, "Resending the verification email")
    try:
        await resend_auth0_verification_email(user.sub)
    except Exception as e:
        logger.error(f"Failed to resend verification email for {user.sub}: {e}", exc_info=e)
        raise HTTPException(status_code=502, detail="Failed to resend verification email via Auth0 Management API")
    return {"message": "Verification email resent successfully"}


@router.delete(
    "/me",
    status_code=204,
    operation_id="delete-current-user",
    summary="Delete the authenticated user's account",
)
async def delete_me(user: AuthenticatedUser = Depends(get_current_user)) -> Response:
    _require_management_api()
    try:
        await delete_auth0_user(user.sub)
    except Exception as e:
        logger.error(f"Failed to delete Auth0 user {user.sub}: {e}", exc_info=e)
        raise HTTPException(status_code=502, detail="Failed to delete account via Auth0 Management API")
    return Response(status_code=204)
