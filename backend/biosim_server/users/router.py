import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute

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
from biosim_server.config import Auth0Settings, get_settings
from biosim_server.users.models import PasswordResetResponse, UpdateUserProfileRequest, UserProfile

logger = logging.getLogger(__name__)

# Every password-reset response is per-principal, and the 200 carries a bearer
# capability -- no intermediary should retain any of them, errors included.
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

# Tolerated negative skew when judging an `auth_time` claim. The claim is stamped
# by the identity provider, so neither a client clock nor a token holder can set
# it; this only absorbs a marginally future-dated value on a trusting clock.
_AUTH_TIME_FUTURE_SKEW_SECONDS = 60

router = APIRouter(prefix="/api/v1", tags=["Users"])


class _PrivacyHeadersRoute(APIRoute):
    """Apply `_NO_STORE` to every response of a route, dependencies included.

    The handler applies it to the responses it builds itself, but a missing or
    unverifiable bearer token is refused by `get_current_user` *before* the
    handler runs -- so the 401 (and the JWKS 503) a client actually received
    carried neither header, contradicting the route's own documented contract.
    Merging the policy into the raised exception keeps the change scoped to this
    route (no global exception handler is replaced) and preserves the headers the
    original exception set (`WWW-Authenticate`, `Retry-After`) by construction.
    Router-level faults that never reach this route -- 405 for the wrong method
    on this path, 404 when the path is not mounted -- keep their default headers.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **_NO_STORE}
                raise

        return handler


def _provider_from_sub(sub: str) -> str:
    # Auth0 `sub` claims are "<connection>|<user id>", e.g. "auth0|abc123" or
    # "google-oauth2|10987654321". No '|' (shouldn't happen for Auth0-issued
    # tokens) falls back to the whole sub rather than raising.
    return sub.split("|", 1)[0]


def _configured_tenant_issuers(settings: Auth0Settings) -> set[str]:
    """Issuer(s) the configured Auth0 tenant owns.

    `AUTH0_DOMAIN` is where the Management API is reached, and its Auth0
    convention issuer is `https://{domain}/`. An explicit `AUTH0_ISSUER` is
    included because that is also how a tenant served under a custom domain is
    configured -- same tenant, different issuer string. Anything else (e.g. a
    second entry in `AUTH0_TRUSTED_ISSUERS`) is a *different* identity domain.

    `reset_my_password` deliberately keeps the stricter domain-only rule rather
    than reusing this helper: the M2M client it spends belongs to the tenant at
    `AUTH0_DOMAIN`, and that route is already disabled unless the domain and the
    SPA client id are configured. Do not "unify" the two without deciding what a
    custom-domain deployment should mean for ticket issuance.
    """
    issuers: set[str] = set()
    if settings.domain:
        issuers.add(f"https://{settings.domain}/")
    if settings.issuer:
        issuers.add(settings.issuer)
    return issuers


def _is_configured_tenant_principal(user: AuthenticatedUser, settings: Auth0Settings) -> bool:
    """True when the verified issuer is the configured Auth0 tenant's own.

    Signature validity inside one trust domain does not establish identity in
    another. With `AUTH0_TRUSTED_ISSUERS` set, a token issued by issuer A can
    carry the same `sub` string as a user of the tenant the Management API
    mutates -- addressable as that user by subject alone. Auth0's Management API
    is scoped to a single tenant's directory, so only a principal that tenant
    issued may be read from or written to it. A principal with no issuer is not
    proven to be the tenant's either, so it fails closed.
    """
    return user.issuer is not None and user.issuer in _configured_tenant_issuers(settings)


async def _build_profile(user: AuthenticatedUser) -> UserProfile:
    settings = get_settings().auth0
    profile = UserProfile(id=user.sub, email=user.email, provider=_provider_from_sub(user.sub))
    # JWT-only identity is returned to a principal from another trusted issuer:
    # its signature checked out for this API, but its subject must never be
    # resolved against -- or enriched from -- the configured tenant's directory.
    if management_api_configured() and _is_configured_tenant_principal(user, settings):
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


def _require_configured_tenant(user: AuthenticatedUser) -> None:
    """403 a principal the configured tenant cannot identify. Never a Management call.

    Checked after the unconfigured-503 so the response vocabulary stays
    distinguishable: "this deployment has no Management credentials" versus
    "this token is not from the tenant those credentials address".
    """
    if not _is_configured_tenant_principal(user, get_settings().auth0):
        logger.warning(
            "Refusing Auth0 Management call: token issuer is not the configured tenant",
            extra={"auth_outcome": "denied", "auth_reason": "foreign_issuer_management"},
        )
        raise HTTPException(
            status_code=403,
            detail="Account management is unavailable for this account",
        )


def _require_recent_authentication(user: AuthenticatedUser, settings: Auth0Settings) -> None:
    """Step-up gate for issuing a hosted password-change ticket (AUTH-MAJ-004).

    Possessing a valid access token is not evidence that the human behind it
    authenticated recently: refresh tokens and long-lived sessions decouple a
    token's freshness from the user's, and `iat` moves whenever a token is
    refreshed. The only accepted evidence is the identity provider's own
    interactive-authentication timestamp (`auth_time`, configured by
    `AUTH0_AUTH_TIME_CLAIM`), which a client cannot assert and a refresh cannot
    fake. Absent, malformed, future-dated, and stale evidence all refuse.
    """
    auth_time = user.auth_time
    now = int(time.time())
    reason: str | None = None
    if auth_time is None:
        reason = "missing_auth_time"
    elif auth_time > now + _AUTH_TIME_FUTURE_SKEW_SECONDS:
        reason = "future_auth_time"
    elif (now - auth_time) > settings.password_reset_max_auth_age_seconds:
        reason = "stale_auth_time"
    if reason is None:
        return
    # Bounded reason only -- no claim value, no threshold, no account detail.
    logger.warning(
        "Refusing password-reset ticket: no usable recent-authentication evidence",
        extra={"auth_outcome": "denied", "auth_reason": reason},
    )
    raise HTTPException(
        status_code=403,
        detail="Password reset requires a recent sign-in",
        headers=_NO_STORE,
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
    responses={
        403: {
            "description": (
                "The token's issuer is not the configured Auth0 tenant, so its "
                "subject must not be resolved against that tenant's directory. "
                "No Management API call is made."
            )
        },
    },
)
async def update_me(
    request: UpdateUserProfileRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfile:
    _require_management_api()
    _require_configured_tenant(user)
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
    responses={
        403: {
            "description": (
                "The token's issuer is not the configured Auth0 tenant, so its "
                "subject must not be resolved against that tenant's directory. "
                "No account is deleted."
            )
        },
    },
)
async def delete_me(user: AuthenticatedUser = Depends(get_current_user)) -> Response:
    _require_management_api()
    _require_configured_tenant(user)
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
        # Idempotent with _PrivacyHeadersRoute below, and kept explicit so the
        # handler's own 429 stays uncacheable even if it were ever mounted on a
        # plain APIRoute.
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
    if settings.password_reset_require_recent_auth:
        _require_recent_authentication(user, settings)
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


# Registered via add_api_route rather than the @router.post decorator: the
# decorator forwards a fixed keyword set and would drop
# `route_class_override`, which is what puts the privacy-header policy on the
# dependency-generated 401/503 responses too. Registration order is unchanged
# (this is still the router's last route), so the generated OpenAPI order holds.
router.add_api_route(
    "/me/password-reset",
    reset_my_password,
    methods=["POST"],
    response_model=PasswordResetResponse,
    operation_id="create-current-user-password-reset",
    summary="Create an Auth0-hosted password reset URL for the authenticated user",
    description=(
        "Issues a short-lived (600 s), single-use Auth0-hosted password-change URL "
        "for the authenticated primary database user. The account is selected from "
        "the verified bearer token only -- no email, user id, client id or return "
        "URL is accepted from the caller, and supplying them changes nothing. No "
        "email is sent: navigate the browser to the returned URL (it is a bearer "
        "capability, so it must never be logged, cached, or fetched by script). "
        "Issuance is a single attempt and is deliberately never retried, because "
        "an uncertain outcome may already have minted a ticket. Every response, "
        "errors included, is `Cache-Control: no-store` with `Referrer-Policy: "
        "no-referrer`. A 200 means a ticket was issued, not that the password was "
        "changed."
    ),
    route_class_override=_PrivacyHeadersRoute,
    responses={
        200: {
            "description": (
                "A hosted password-change URL. Sensitive: no-store/no-referrer, "
                "never persisted by the API, and not proof of a completed change."
            ),
            "headers": {
                "Cache-Control": {"description": "Always `no-store`.", "schema": {"type": "string"}},
                "Referrer-Policy": {"description": "Always `no-referrer`.", "schema": {"type": "string"}},
            },
        },
        401: {
            "description": (
                "Missing, malformed, expired, or otherwise unverifiable bearer "
                "token. An access token without an expiry is rejected here too."
            ),
            "headers": {
                "WWW-Authenticate": {
                    "description": "RFC 6750 bearer challenge naming the fault.",
                    "schema": {"type": "string"},
                },
            },
        },
        403: {
            "description": (
                "This principal may not change a password here: its issuer is not "
                "the configured tenant, its subject is not a primary `auth0|` "
                "database user, or -- when recent authentication is required -- "
                "the token carries no usable recent sign-in evidence."
            ),
            "headers": {
                "Cache-Control": {"description": "Always `no-store`.", "schema": {"type": "string"}},
            },
        },
        429: {
            "description": (
                "The caller exhausted the password-reset budget (metered "
                "separately from workflow starts, per issuer and subject). Retry "
                "after the indicated delay; no ticket was issued."
            ),
            "headers": {
                "Retry-After": {
                    "description": "Seconds until the current fixed window rolls over.",
                    "schema": {"type": "integer"},
                },
                "Cache-Control": {"description": "Always `no-store`.", "schema": {"type": "string"}},
            },
        },
        502: {
            "description": (
                "Auth0 rejected or failed the ticket request. Generic by design: "
                "it does not indicate whether the account exists."
            ),
            "headers": {
                "Cache-Control": {"description": "Always `no-store`.", "schema": {"type": "string"}},
            },
        },
        503: {
            "description": (
                "Password reset is not configured, authentication is temporarily "
                "unavailable (no usable JWKS), or Auth0 is rate-limiting ticket "
                "issuance."
            ),
            "headers": {
                "Retry-After": {
                    "description": "Present for the Auth0-throttled and JWKS cases.",
                    "schema": {"type": "integer"},
                },
                "Cache-Control": {"description": "Always `no-store`.", "schema": {"type": "string"}},
            },
        },
    },
)
