import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt  # type: ignore[import-untyped]

from biosim_server.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)

_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


async def _get_jwks() -> dict[str, Any]:
    settings = get_settings().auth0
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["fetched_at"] > _JWKS_TTL_SECONDS:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.jwks_url(), timeout=5.0)
            resp.raise_for_status()
            _jwks_cache["keys"] = resp.json()
            _jwks_cache["fetched_at"] = now
    keys: dict[str, Any] = _jwks_cache["keys"]
    return keys


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

    jwks = await _get_jwks()
    rsa_key = next(
        (
            {"kty": k["kty"], "kid": k["kid"], "use": k["use"], "n": k["n"], "e": k["e"]}
            for k in jwks["keys"]
            if k["kid"] == unverified_header.get("kid")
        ),
        None,
    )
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
    return AuthenticatedUser(sub=payload["sub"], email=email, roles=roles)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser | None:
    """Like get_current_user, but degrades to None instead of raising -- for endpoints
    that stay open to anonymous callers while still trusting a token when one is given.
    """
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None
