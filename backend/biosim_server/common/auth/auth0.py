import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt  # type: ignore[import-untyped]

from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

# How long a successfully fetched JWKS document is treated as fresh. Unchanged
# from the original implementation.
_JWKS_TTL_SECONDS = 3600
# Hard ceiling on how long an expired document may still be served while
# refreshes keep failing (stale-while-revalidate). Well inside Auth0's
# rotation overlap -- a key that has been rotated *out* stays published in the
# JWKS for far longer than a day -- so a key cached within this window is still
# a key the tenant published. See the security analysis before changing it.
_JWKS_STALE_MAX_AGE_SECONDS = 86400
# After a failed fetch, suppress further outbound attempts for this long. This
# is the negative cache: it turns "one outbound request per inbound request"
# into "one outbound request per window per process".
_JWKS_FAILURE_BACKOFF_SECONDS = 10
# Minimum interval between *forced* refreshes triggered by an unknown `kid`.
# Without it, a flood of tokens carrying bogus kids becomes an amplification
# vector against the identity provider. Not optional.
_JWKS_KID_REFRESH_COOLDOWN_SECONDS = 60
# Value advertised in the Retry-After header of the 503 we return when no
# usable key set exists. Matched to the failure backoff so a client that
# honours it comes back at roughly the moment the next attempt is allowed.
_JWKS_RETRY_AFTER_SECONDS = _JWKS_FAILURE_BACKOFF_SECONDS
# Rate limit on the "roles claim did not arrive" warning (P0 #4), so a
# misconfigured tenant produces a visible signal rather than a log flood.
_ROLES_CLAIM_WARN_INTERVAL_SECONDS = 300

# Module-level JWKS cache. The name and the "keys"/"fetched_at" entries are
# unchanged from the original implementation because
# tests/fixtures/keycloak/client.py resets them by hand; the three new entries
# carry the negative-cache and cooldown state.
_jwks_cache: dict[str, Any] = {
    "keys": None,                    # last successfully fetched JWKS document
    "fetched_at": 0.0,               # when that fetch succeeded
    "last_failure_at": 0.0,          # when a fetch last failed (0.0 = no active backoff)
    "last_forced_refresh_at": 0.0,   # when an unknown `kid` last forced a refresh
}
# Serialises refreshes so N concurrent cache misses produce one outbound fetch,
# not N. Mirrors the double-checked pattern already used for the Management API
# token in auth0_management.py:25, 40-46. Deliberately NOT held across token
# validation -- only across the fetch -- so jwt.decode stays fully parallel.
_jwks_refresh_lock = asyncio.Lock()
# Rate-limiter state for the roles-claim assertion (P0 #4).
_auth_warning_state: dict[str, float] = {"roles_claim_warned_at": 0.0}


def _reset_jwks_cache() -> None:
    """Clear all cached JWKS state. Test-only affordance.

    Tests previously reset `_jwks_cache["keys"]` and `["fetched_at"]` by hand
    (tests/fixtures/keycloak/client.py:34-35). With backoff and cooldown state
    in the same dict, resetting only those two would leak an armed backoff
    into the next test, so the reset belongs next to the state it clears.
    """
    _jwks_cache["keys"] = None
    _jwks_cache["fetched_at"] = 0.0
    _jwks_cache["last_failure_at"] = 0.0
    _jwks_cache["last_forced_refresh_at"] = 0.0
    _auth_warning_state["roles_claim_warned_at"] = 0.0


def _jwks_unavailable() -> HTTPException:
    """The 503 returned when no usable key set exists.

    Deliberately a *factory*, not a raise: several call sites need it and the
    Retry-After value must be identical at every one of them. The detail text
    is generic -- it names no URL, no exception, and no token material.
    """
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Authentication temporarily unavailable",
        headers={"Retry-After": str(_JWKS_RETRY_AFTER_SECONDS)},
    )


def _backoff_active(now: float) -> bool:
    """True while the negative cache is suppressing outbound JWKS fetches."""
    last_failure: float = _jwks_cache["last_failure_at"]
    return last_failure > 0.0 and (now - last_failure) < _JWKS_FAILURE_BACKOFF_SECONDS


def _usable_jwks(now: float) -> dict[str, Any]:
    """Return the cached document if it is fresh, or stale but within the bound.

    Raises the 503 when the cache is empty or has aged past
    _JWKS_STALE_MAX_AGE_SECONDS. This is the single place that decides
    "serve stale" versus "refuse to serve", so the staleness policy cannot
    drift between the TTL path and the failure path.
    """
    cached: dict[str, Any] | None = _jwks_cache["keys"]
    if cached is not None:
        age = now - float(_jwks_cache["fetched_at"])
        if age <= _JWKS_TTL_SECONDS:
            return cached
        if age <= _JWKS_STALE_MAX_AGE_SECONDS:
            logger.warning(
                "Serving stale JWKS (age %.0fs, TTL %ds): refresh against the identity "
                "provider is failing. Tokens signed with a newly rotated key will be "
                "rejected until a refresh succeeds.",
                age,
                _JWKS_TTL_SECONDS,
            )
            return cached
        logger.error(
            "Cached JWKS is %.0fs old, past the %ds staleness bound -- refusing to use it.",
            age,
            _JWKS_STALE_MAX_AGE_SECONDS,
        )
    raise _jwks_unavailable()


async def _fetch_jwks_locked(now: float) -> bool:
    """Fetch and store the JWKS document. The caller MUST hold _jwks_refresh_lock.

    Returns True on success. On failure it arms the negative cache and returns
    False rather than raising -- the caller may still have a stale document it
    can legitimately serve, and that decision belongs to _usable_jwks().
    """
    settings = get_settings().auth0
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.jwks_url(), timeout=5.0)
            resp.raise_for_status()
            document = resp.json()
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise ValueError("response body is not a JWKS document")
    except Exception as e:
        _jwks_cache["last_failure_at"] = now
        logger.error(
            "JWKS fetch failed (%s); suppressing further attempts for %ds.",
            type(e).__name__,
            _JWKS_FAILURE_BACKOFF_SECONDS,
        )
        return False
    _jwks_cache["keys"] = document
    _jwks_cache["fetched_at"] = now
    _jwks_cache["last_failure_at"] = 0.0
    logger.info("JWKS refreshed: %d key(s).", len(document["keys"]))
    return True


def _select_rsa_key(jwks: dict[str, Any], kid: str | None) -> dict[str, str] | None:
    """Pick the RSA public key matching `kid`, or None.

    Replaces the inline generator at the original auth0.py:52-59. Same
    selection rule (match on `kid`), but every field access is guarded: the
    original indexed k["kty"], k["kid"], k["use"], k["n"], k["e"] directly, so
    a JWKS entry missing any of them raised KeyError -> HTTP 500. `use` in
    particular is optional in RFC 7517 and is absent from key sets produced by
    python-jose's own jwk.construct(...).public_key().to_dict().
    """
    if not kid:
        return None
    for key in jwks.get("keys", []):
        if not isinstance(key, dict) or key.get("kid") != kid or key.get("kty") != "RSA":
            continue
        n, e = key.get("n"), key.get("e")
        if not isinstance(n, str) or not isinstance(e, str):
            continue
        return {"kty": "RSA", "kid": kid, "use": key.get("use") or "sig", "n": n, "e": e}
    return None


async def _get_jwks() -> dict[str, Any]:
    """Return a usable JWKS document, refreshing it when the cached copy expired.

    Never raises for an ordinary identity-provider failure while a usable
    cached copy exists. Raises HTTP 503 (with Retry-After) only when there is
    nothing safe to serve.
    """
    now = time.time()
    cached: dict[str, Any] | None = _jwks_cache["keys"]
    if cached is not None and (now - float(_jwks_cache["fetched_at"])) <= _JWKS_TTL_SECONDS:
        return cached

    if _backoff_active(now):
        # A recent attempt failed. Do not touch the identity provider; serve
        # stale if we can, 503 if we cannot.
        return _usable_jwks(now)

    async with _jwks_refresh_lock:
        # Double-checked: another coroutine may have refreshed while we waited
        # on the lock. Same pattern as auth0_management.py:40-46.
        now = time.time()
        cached = _jwks_cache["keys"]
        if cached is not None and (now - float(_jwks_cache["fetched_at"])) <= _JWKS_TTL_SECONDS:
            return cached
        if not _backoff_active(now):
            await _fetch_jwks_locked(now)
        return _usable_jwks(time.time())


async def _force_jwks_refresh() -> dict[str, Any] | None:
    """Force one JWKS refresh after a `kid` miss, subject to a cooldown.

    Returns whatever document is cached afterwards -- refreshed, unchanged, or
    None if the cache was empty and the refresh failed. The caller re-runs key
    selection against it, which is why this returns the current document rather
    than a success flag: two concurrent `kid` misses then both benefit from the
    single refresh the first one performed.
    """
    async with _jwks_refresh_lock:
        now = time.time()
        last_forced: float = _jwks_cache["last_forced_refresh_at"]
        cooldown_active = (
            last_forced > 0.0 and (now - last_forced) < _JWKS_KID_REFRESH_COOLDOWN_SECONDS
        )
        if not cooldown_active and not _backoff_active(now):
            # Stamp the cooldown BEFORE fetching: a failed forced refresh must
            # consume the window too, or a flood of bogus kids retries forever.
            _jwks_cache["last_forced_refresh_at"] = now
            await _fetch_jwks_locked(now)
        document: dict[str, Any] | None = _jwks_cache["keys"]
        return document


def _warn_roles_claim_absent(claim: str, claim_present: bool) -> None:
    """Runtime assertion that the Auth0 Post-Login Action is actually live (P0 #4).

    Both custom claims the backend depends on are stamped by an Auth0
    Post-Login Action (see auth0/actions/post-login.js). If that Action is
    absent, disabled, or erroring, every require_roles endpoint returns 403 and
    no admin exists -- a silent, total failure that presents as a permissions
    bug. This turns it into a named log line.

    Rate-limited: a tenant in this state produces one warning per token
    otherwise, which is a log flood rather than a signal.
    """
    now = time.time()
    if (now - _auth_warning_state["roles_claim_warned_at"]) < _ROLES_CLAIM_WARN_INTERVAL_SECONDS:
        return
    _auth_warning_state["roles_claim_warned_at"] = now
    if claim_present:
        logger.warning(
            "Validated token carries an empty %r claim: the Auth0 Action is stamping the "
            "claim, but this user has no roles assigned. require_roles endpoints will 403.",
            claim,
        )
    else:
        logger.warning(
            "Validated token carries no %r claim at all: the Auth0 Post-Login Action that "
            "stamps it is probably not deployed on this tenant, or AUTH0_ROLES_CLAIM does "
            "not match the namespace the Action uses. Every require_roles endpoint will "
            "403 and no admin will exist.",
            claim,
        )


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
    kid = unverified_header.get("kid")
    rsa_key = _select_rsa_key(jwks, kid)
    if rsa_key is None:
        # Auth0 rotates signing keys without notice. Before rejecting the
        # token, force one refresh (cooldown-guarded) and look again -- this is
        # what turns a rotation from an hour-long outage into a single slow
        # request. If the kid is still absent, the 401 below stands.
        refreshed = await _force_jwks_refresh()
        if refreshed is not None:
            rsa_key = _select_rsa_key(refreshed, kid)
    if rsa_key is None:
        # The kid comes from an unverified header and is attacker-controlled,
        # so it is deliberately not echoed into the log line.
        logger.warning("Rejecting token: its signing key id is not in the JWKS.")
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
        logger.warning(
            "Roles claim %r is present but is not a list; treating the token as having "
            "no roles.",
            settings.roles_claim,
        )
        roles = []
    if not roles:
        _warn_roles_claim_absent(settings.roles_claim, settings.roles_claim in payload)
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
        # No token at all: a genuinely anonymous caller. Note that this path
        # never touches the identity provider, so anonymous access keeps
        # working normally during an Auth0 outage.
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException as e:
        if e.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            # An infrastructure failure must not silently downgrade an
            # authenticated caller to anonymous -- that would change the
            # authorization outcome (ownership checks, role gates) based on
            # an Auth0 outage. Propagate it as the 503 it is.
            raise
        # 401s stay swallowed: a bad or expired token on an optional-auth
        # endpoint is still just "not authenticated".
        return None
