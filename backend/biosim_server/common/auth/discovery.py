"""OIDC discovery for the issuer and JWKS URLs (P2 #16).

Adds one tier between the explicit ``AUTH0_ISSUER``/``AUTH0_JWKS_URI`` overrides
and the Auth0 naming convention: a cached, best-effort fetch of
``{base}/.well-known/openid-configuration`` that yields ``issuer`` and
``jwks_uri``.

Resolution precedence (``resolve_oidc``):

    1. explicit ``AUTH0_ISSUER`` **and** ``AUTH0_JWKS_URI`` -> used verbatim, no
       network call;
    2. OIDC discovery, when the document is reachable;
    3. the current convention (``https://{domain}/`` and
       ``https://{domain}/.well-known/jwks.json``).

This module is a deliberate mirror of ``auth0.py``'s JWKS handling -- explicit
timeout, response-shape check before caching, negative cache to bound outbound
attempts, and a single-flight lock -- rather than a second, weaker design. In
particular it does **not** copy PyVCell's timeout-less fetch: a timeout-less
request on the authentication path is a hang, not an error.

Discovery is **never** a hard startup dependency. ``warm_discovery_cache`` is
best-effort and swallows every failure; a pod that cannot reach the discovery
endpoint at boot still starts and still serves tokens via the convention. The
startup gate (``Auth0Settings.configuration_errors``) is left untouched and
stays local and side-effect-free -- discovery never rescues a configuration the
gate would otherwise reject.
"""

import asyncio
import logging
import time
from typing import TypedDict

import httpx

from biosim_server.config import Auth0Settings

logger = logging.getLogger(__name__)

# Explicit timeout on the discovery request. The value the JWKS fetch uses; a
# discovery document served by any compliant provider returns well inside it.
_DISCOVERY_TIMEOUT_SECONDS = 5.0
# A discovery document changes far less often than a key set, but reusing the
# JWKS TTL keeps one mental model. Refreshed opportunistically, never on a
# hot path that blocks a request.
_DISCOVERY_TTL_SECONDS = 3600
# After a failed fetch, suppress further outbound attempts for this long -- the
# negative cache. Matches the JWKS failure backoff.
_DISCOVERY_FAILURE_BACKOFF_SECONDS = 10


class _DiscoveryCache(TypedDict):
    issuer: str | None       # last successfully discovered issuer
    jwks_uri: str | None     # last successfully discovered JWKS URL
    fetched_at: float        # when that discovery succeeded
    last_failure_at: float   # when a discovery last failed (0.0 = no active backoff)


_cache: _DiscoveryCache = {
    "issuer": None,
    "jwks_uri": None,
    "fetched_at": 0.0,
    "last_failure_at": 0.0,
}
# Serialises refreshes so N concurrent cold-start callers produce one outbound
# fetch, not N -- mirrors auth0.py's _jwks_refresh_lock.
_discovery_lock = asyncio.Lock()


def _reset_discovery_cache() -> None:
    """Clear all discovery state. Test-only affordance, mirroring auth0._reset_jwks_cache."""
    _cache["issuer"] = None
    _cache["jwks_uri"] = None
    _cache["fetched_at"] = 0.0
    _cache["last_failure_at"] = 0.0


def _discovery_base(settings: Auth0Settings) -> str | None:
    """The base URL discovery is performed against, or None when neither an
    explicit issuer nor a domain is configured (an invalid config the startup
    gate already rejects)."""
    if settings.issuer:
        return settings.issuer
    if settings.domain:
        return f"https://{settings.domain}/"
    return None


async def _fetch_discovery_locked(base: str, now: float) -> bool:
    """Fetch and cache the discovery document. Caller MUST hold _discovery_lock.

    Returns True on success. On failure it arms the negative cache and returns
    False rather than raising -- the caller falls back to any cached value or to
    the convention. Logs the exception *type* only, never the URL query or any
    token material.
    """
    url = base.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=_DISCOVERY_TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload: object = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("discovery document is not a JSON object")
        issuer = payload.get("issuer")
        jwks_uri = payload.get("jwks_uri")
        if not isinstance(issuer, str) or not issuer or not isinstance(jwks_uri, str) or not jwks_uri:
            raise ValueError("discovery document is missing a string issuer or jwks_uri")
    except Exception as e:
        _cache["last_failure_at"] = now
        logger.warning(
            "OIDC discovery failed (%s); falling back to configured/convention URLs.",
            type(e).__name__,
        )
        return False
    _cache["issuer"] = issuer
    _cache["jwks_uri"] = jwks_uri
    _cache["fetched_at"] = now
    _cache["last_failure_at"] = 0.0
    return True


def _cached_fresh(now: float) -> bool:
    return _cache["issuer"] is not None and (now - _cache["fetched_at"]) < _DISCOVERY_TTL_SECONDS


def _backoff_active(now: float) -> bool:
    return (
        _cache["last_failure_at"] != 0.0
        and (now - _cache["last_failure_at"]) < _DISCOVERY_FAILURE_BACKOFF_SECONDS
    )


async def _discover(settings: Auth0Settings) -> tuple[str, str] | None:
    """Return (issuer, jwks_uri) from discovery, or None if it is unavailable.

    Serves a fresh cache without a network call; honours a negative-cache
    backoff window; otherwise fetches once under the single-flight lock.
    """
    base = _discovery_base(settings)
    if base is None:
        return None
    now = time.time()
    if _cached_fresh(now):
        return _cache["issuer"], _cache["jwks_uri"]  # type: ignore[return-value]
    if _backoff_active(now):
        # Still in backoff: serve a stale value if we have one, else give up.
        if _cache["issuer"] is not None:
            return _cache["issuer"], _cache["jwks_uri"]  # type: ignore[return-value]
        return None
    async with _discovery_lock:
        now = time.time()
        # Double-checked: another caller may have refreshed while we waited.
        if _cached_fresh(now):
            return _cache["issuer"], _cache["jwks_uri"]  # type: ignore[return-value]
        if _backoff_active(now):
            if _cache["issuer"] is not None:
                return _cache["issuer"], _cache["jwks_uri"]  # type: ignore[return-value]
            return None
        ok = await _fetch_discovery_locked(base, now)
        if ok or _cache["issuer"] is not None:
            return _cache["issuer"], _cache["jwks_uri"]  # type: ignore[return-value]
        return None


async def resolve_oidc(settings: Auth0Settings) -> tuple[str, str]:
    """Resolve (issuer, jwks_uri) under the three-tier precedence.

    Explicit env overrides win outright and skip the network entirely. Otherwise
    discovery is consulted, and finally the convention. A per-field explicit
    value still wins over the discovered one.
    """
    if settings.issuer and settings.jwks_uri:
        return settings.issuer, settings.jwks_uri
    discovered = await _discover(settings)
    if discovered is not None:
        d_issuer, d_jwks = discovered
        return settings.issuer or d_issuer, settings.jwks_uri or d_jwks
    return settings.issuer_url(), settings.jwks_url()


async def warm_discovery_cache(settings: Auth0Settings) -> None:
    """Best-effort cache warm for application startup. Never raises.

    A discovery failure here is logged and swallowed: the request-path resolver
    is the contract, and it falls back to the convention. Startup must not
    depend on the identity provider being reachable at boot.
    """
    try:
        if settings.issuer and settings.jwks_uri:
            return  # explicit override configured; discovery is not consulted
        await _discover(settings)
    except Exception as e:  # pragma: no cover - defensive; _discover already guards
        logger.warning("OIDC discovery warm failed (%s); continuing startup.", type(e).__name__)
