import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.common.auth.auth0_management import (
    Auth0ManagementRateLimited,
    create_password_change_ticket,
    delete_auth0_user,
    get_auth0_user,
    management_api_configured,
    update_auth0_user,
)
from biosim_server.common.ratelimit import password_reset_rate_limit
from biosim_server.config import get_settings
from biosim_server.users.models import PasswordResetResponse, UpdateUserProfileRequest, UserProfile

logger = logging.getLogger(__name__)

# Every password-reset response is per-principal, and the 200 carries a bearer
# capability -- no intermediary should retain any of them, errors included.
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

router = APIRouter(prefix="/api/v1", tags=["Users"])


def _provider_from_sub(sub: str) -> str:
    # Auth0 `sub` claims are "<connection>|<user id>", e.g. "auth0|abc123" or
    # "google-oauth2|10987654321". No '|' (shouldn't happen for Auth0-issued
    # tokens) falls back to the whole sub rather than raising.
    return sub.split("|", 1)[0]


async def _build_profile(user: AuthenticatedUser) -> UserProfile:
    profile = UserProfile(id=user.sub, email=user.email, provider=_provider_from_sub(user.sub))
    if management_api_configured():
        try:
            auth0_user = await get_auth0_user(user.sub)
            profile.name = auth0_user.get("name")
            profile.email_verified = auth0_user.get("email_verified")
        except Exception:
            # Best-effort enrichment -- a flaky Management API call shouldn't
            # break reading your own JWT-derived identity.
            logger.warning("Failed to enrich profile from Auth0 Management API")
    return profile


def _require_management_api() -> None:
    if not management_api_configured():
        raise HTTPException(
            status_code=503,
            detail="Auth0 Management API not configured (AUTH0_MANAGEMENT_CLIENT_ID/SECRET unset)",
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
    if not request.model_fields_set:
        return await _build_profile(user)
    try:
        auth0_user = await update_auth0_user(user.sub, name=request.name)
    except Auth0ManagementRateLimited as exc:
        logger.warning("Auth0 Management API rate-limited a profile update")
        raise HTTPException(
            status_code=503,
            detail="Auth0 is rate limiting profile updates; retry shortly",
            headers={"Retry-After": str(exc.retry_after)},
        )
    except Exception:
        logger.exception("Failed to update profile through Auth0 Management API")
        raise HTTPException(status_code=502, detail="Failed to update profile via Auth0 Management API")
    return UserProfile(
        id=user.sub,
        email=user.email,
        provider=_provider_from_sub(user.sub),
        name=auth0_user.get("name"),
        email_verified=auth0_user.get("email_verified"),
    )


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
    except Auth0ManagementRateLimited as exc:
        logger.warning("Auth0 Management API rate-limited an account deletion")
        raise HTTPException(
            status_code=503,
            detail="Auth0 is rate limiting account deletion; retry shortly",
            headers={"Retry-After": str(exc.retry_after)},
        )
    except Exception:
        logger.exception("Failed to delete account through Auth0 Management API")
        raise HTTPException(status_code=502, detail="Failed to delete account via Auth0 Management API")
    return Response(status_code=204)


@router.post(
    "/me/password-reset",
    response_model=PasswordResetResponse,
    operation_id="create-current-user-password-reset",
    summary="Create an Auth0-hosted password reset URL for the authenticated user",
)
async def reset_my_password(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
) -> PasswordResetResponse:
    settings = get_settings().auth0
    if not settings.domain or not settings.password_reset_client_id or not management_api_configured():
        raise HTTPException(status_code=503, detail="Password reset is unavailable", headers=_NO_STORE)
    # Quota is charged before the identity check so an ineligible principal cannot
    # hammer the 403 branch unbounded; the 503 above is config-only and free.
    try:
        password_reset_rate_limit(request, user)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **_NO_STORE}
        raise
    if (
        user.issuer != f"https://{settings.domain}/"
        or not user.sub.startswith("auth0|")
        or not user.sub.removeprefix("auth0|")
        or user.sub.count("|") != 1
        or user.sub.endswith("@clients")
    ):
        raise HTTPException(
            status_code=403, detail="Password reset is unavailable for this account", headers=_NO_STORE
        )
    try:
        url = await create_password_change_ticket(user.sub)
    except Auth0ManagementRateLimited:
        raise HTTPException(
            status_code=503, detail="Password reset is temporarily unavailable",
            headers={**_NO_STORE, "Retry-After": "10"},
        ) from None
    except Exception:
        # Do not log exceptions: URLs/bodies may contain ticket or credential material.
        logger.warning("Auth0 password reset request failed")
        raise HTTPException(
            status_code=502, detail="Unable to start password reset", headers=_NO_STORE
        ) from None
    response.headers.update(_NO_STORE)
    return PasswordResetResponse(url=url)
