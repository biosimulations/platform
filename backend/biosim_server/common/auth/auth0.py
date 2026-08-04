import time
from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from biosim_server.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)

_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


def _get_jwks() -> dict:
    settings = get_settings().auth0
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["fetched_at"] > _JWKS_TTL_SECONDS:
        resp = httpx.get(f"https://{settings.domain}/.well-known/jwks.json", timeout=5.0)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()
        _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


class AuthenticatedUser:
    def __init__(self, sub: str, email: str | None):
        self.sub = sub        # stable Auth0 user id, e.g. "auth0|abc123" or "google-oauth2|..."
        self.email = email


def get_current_user(
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

    jwks = _get_jwks()
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
            issuer=f"https://{settings.domain}/",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.JWTClaimsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid claims")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    return AuthenticatedUser(sub=payload["sub"], email=payload.get("email"))
