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
    return AuthenticatedUser(sub=payload["sub"], email=payload.get("email"), roles=roles)
