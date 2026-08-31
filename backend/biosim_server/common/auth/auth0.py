import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt  # type: ignore[import-untyped]

from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0, "last_forced_at": 0.0}
_JWKS_TTL_SECONDS = 3600
# Floor between unknown-kid forced refreshes, so a flood of tokens signed with
# junk `kid`s can't turn into a fetch storm against the IdP. Measured from the
# last *forced* refresh, not from the last fetch: a genuine key rotation right
# after a normal cache fill still gets picked up immediately.
_JWKS_MIN_REFRESH_INTERVAL_SECONDS = 60
# Serializes refreshes: without it, N concurrent requests arriving on a cold or
# just-expired cache each fire their own JWKS fetch.
_jwks_lock = asyncio.Lock()


def _idp_unavailable() -> HTTPException:
    """503 (not 401): the token may well be valid, we just can't verify it yet."""
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Authentication provider unavailable",
        headers={"Retry-After": "30"},
    )


async def _fetch_jwks() -> dict[str, Any]:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.jwks_url(), timeout=5.0)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    return payload


async def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return the IdP's JWKS, cached for ``_JWKS_TTL_SECONDS``.

    ``force_refresh`` is used when a token carries a ``kid`` that isn't in the
    cached set (i.e. the IdP rotated signing keys mid-TTL). That refresh is
    rate-limited and best-effort: if it fails we keep serving the cached keys
    and the caller falls through to a 401 "Unknown signing key" rather than
    turning a bad `kid` into a 503.

    A fetch failure with no usable cache raises 503 -- never 401 -- so an IdP
    outage is never reported to the client as an authentication failure.
    """
    def _usable_cache() -> dict[str, Any] | None:
        cached: dict[str, Any] | None = _jwks_cache["keys"]
        if cached is None:
            return None
        now = time.time()
        if force_refresh:
            if now - _jwks_cache["last_forced_at"] < _JWKS_MIN_REFRESH_INTERVAL_SECONDS:
                return cached
            return None
        return cached if now - _jwks_cache["fetched_at"] <= _JWKS_TTL_SECONDS else None

    fresh = _usable_cache()
    if fresh is not None:
        return fresh

    async with _jwks_lock:
        # Re-check: another coroutine may have refreshed while we waited.
        fresh = _usable_cache()
        if fresh is not None:
            return fresh
        cached: dict[str, Any] | None = _jwks_cache["keys"]
        if force_refresh:
            _jwks_cache["last_forced_at"] = time.time()
        try:
            keys = await _fetch_jwks()
        except Exception as e:
            if cached is not None and force_refresh:
                logger.warning(f"JWKS rotation refresh failed, keeping cached keys: {e}")
                return cached
            logger.error(f"Failed to fetch JWKS from the identity provider: {e}")
            raise _idp_unavailable()
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = time.time()
        return keys


def _find_rsa_key(jwks: dict[str, Any], kid: str | None) -> dict[str, str] | None:
    for key in jwks.get("keys") or []:
        if key.get("kid") == kid:
            return {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"],
            }
    return None


class AuthenticatedUser:
    def __init__(self, sub: str, email: str | None, roles: list[str] | None = None):
        self.sub = sub        # stable Auth0 user id, e.g. "auth0|abc123" or "google-oauth2|..."
        self.email = email
        self.roles = roles or []   # from the Auth0Settings.roles_claim custom claim; [] if unset


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    settings = get_settings().auth0
    token = credentials.credentials

    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")

    kid = unverified_header.get("kid")
    jwks = await _get_jwks()
    rsa_key = _find_rsa_key(jwks, kid)
    if rsa_key is None:
        # Unknown kid usually means the IdP rotated signing keys inside our cache
        # TTL. Refresh once (rate-limited) before rejecting the token.
        jwks = await _get_jwks(force_refresh=True)
        rsa_key = _find_rsa_key(jwks, kid)
    if rsa_key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown signing key")

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=settings.algorithms,
            audience=settings.audience,
            issuer=settings.issuer_url(),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.JWTClaimsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid claims")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    roles = payload.get(settings.roles_claim, [])
    if not isinstance(roles, list):
        roles = []
    # Real Auth0 access tokens don't carry a plain "email" claim -- it has to be
    # stamped on via a Post-Login Action as the namespaced settings.email_claim
    # (see config.py). Fall back to plain "email" for OIDC providers that do put
    # it on the access token by default (e.g. the Keycloak realm used in tests).
    raw_email = payload.get(settings.email_claim) or payload.get("email")
    email = (raw_email or "").strip().lower() or None
    # `sub` is the only authorization identity we persist; a token without one
    # can't own anything, so it is not an authenticated principal.
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no subject")
    return AuthenticatedUser(sub=sub, email=email, roles=roles)


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser | None:
    """Like get_current_user, but anonymous when no credentials are sent at all.

    Contract (see the Auth0 audit plan, section 7.1)::

        no Authorization header  -> None (anonymous)
        valid credentials        -> AuthenticatedUser
        invalid credentials      -> 401
        JWKS / IdP unavailable   -> 503

    "Present but unusable" credentials -- an empty ``Bearer``, or a non-Bearer
    scheme such as ``Basic`` -- are *invalid*, not absent: HTTPBearer hands us
    None for those, so we check the raw header to tell the two cases apart.
    Silently downgrading them to anonymous would hand anonymous privileges to a
    caller who believes they are authenticated.
    """
    if credentials is None:
        if request.headers.get("authorization"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authorization header")
        return None
    return await get_current_user(credentials)
