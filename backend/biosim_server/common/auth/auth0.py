import asyncio
import hashlib
import logging
import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt  # type: ignore[import-untyped]
from jose.exceptions import ExpiredSignatureError, JWTClaimsError  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from biosim_server.common.auth.discovery import resolve_oidc
from biosim_server.config import Auth0Settings, get_settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def _log_auth_event(outcome: str, reason: str, *, sub: str | None = None) -> None:
    """Emit a bounded outcome without tokens, claims, or raw subjects."""
    extra: dict[str, str] = {"auth_outcome": outcome, "auth_reason": reason}
    if sub:
        extra["auth_subject_hash"] = hashlib.sha256(sub.encode()).hexdigest()[:12]
    logger.info("Authentication outcome", extra=extra)

# The JWT signature-algorithm allowlist. RS256 only.
#
# Deliberately NOT a Settings field. Under pydantic-settings v2 with
# env_prefix="" and no explicit alias (every other Auth0Settings field has
# one), a field named `algorithms` binds to a bare, case-insensitive
# ALGORITHMS environment variable -- so a stray, generically-named env var in
# any overlay's api.env, or a developer's local .env, would silently weaken
# the one control that defeats algorithm-confusion attacks (an attacker
# resubmitting a claims payload re-signed with a symmetric algorithm, using
# the RSA public key -- itself public, served by the JWKS endpoint by design
# -- as the HMAC secret). There is no deployment that legitimately needs any
# algorithm other than RS256; making the allowlist a module constant removes
# the environment as an attack surface entirely, rather than trying to
# validate it. See P1 #14's Security Considerations for the full analysis.
_ALLOWED_ALGORITHMS: tuple[str, ...] = ("RS256",)

# Allow the modest clock drift that is normal between clients, an IdP, and this
# service.  Keep this deliberately small: it applies to both ``exp`` and
# ``nbf``, so a larger value would materially extend a token's usable window.
_CLOCK_SKEW_LEEWAY_SECONDS = 60

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

class _IssuerJwksState:
    """JWKS document state for a single JWKS URL.

    Keys from different issuers must never share a document: a ``kid`` collision
    across tenants would otherwise let issuer A's key verify issuer B's token.
    """

    def __init__(self) -> None:
        self.keys: dict[str, Any] | None = None
        self.fetched_at: float = 0.0
        self.last_failure_at: float = 0.0
        self.last_forced_refresh_at: float = 0.0
        self._refresh_lock = asyncio.Lock()

    def backoff_active(self, now: float) -> bool:
        return (
            self.last_failure_at > 0.0
            and (now - self.last_failure_at) < _JWKS_FAILURE_BACKOFF_SECONDS
        )


class JwksCache:
    """Process-scoped JWKS cache: TTL, stale-while-revalidate, negative cache, rotation.

    Production uses one instance per process (see ``get_jwks_cache``). Tests
    construct a fresh instance instead of resetting module globals.

    State is keyed by JWKS URL so multi-issuer deployments cannot mix signing
    keys. ``.keys`` / ``.last_failure_at`` remain attributes for the single-
    issuer tests that inspect them; with more than one warmed URL they reflect
    the sole state when exactly one exists.
    """

    def __init__(self) -> None:
        self._states: dict[str, _IssuerJwksState] = {}
        self._roles_claim_warned_at: float = 0.0

    def _state_for(self, jwks_uri: str) -> _IssuerJwksState:
        state = self._states.get(jwks_uri)
        if state is None:
            state = _IssuerJwksState()
            self._states[jwks_uri] = state
        return state

    def _sole_state(self) -> _IssuerJwksState | None:
        if len(self._states) == 1:
            return next(iter(self._states.values()))
        return None

    @property
    def keys(self) -> dict[str, Any] | None:
        state = self._sole_state()
        return None if state is None else state.keys

    @property
    def last_failure_at(self) -> float:
        state = self._sole_state()
        return 0.0 if state is None else state.last_failure_at

    def backoff_active(self, now: float) -> bool:
        """True while any (or the sole) negative cache is suppressing fetches."""
        state = self._sole_state()
        if state is not None:
            return state.backoff_active(now)
        return any(s.backoff_active(now) for s in self._states.values())

    def status(self) -> dict[str, object]:
        """A read-only, side-effect-free snapshot of JWKS cache health, for /ready (#19c).

        Makes **no** outbound call -- it only inspects the in-process cache the
        request path already maintains. This is deliberate: a warm cache means
        authentication is working *even while Auth0 is unreachable*, so /ready
        reports the state of the thing that actually determines whether auth works
        (the cache), never the reachability of the IdP. A gating reachability check
        would evict every pod during an outage and force the roll backend/CLAUDE.md
        warns against.

        `state`:
          * ``no_keys_cached`` -- cold cache; authenticated requests will 503.
          * ``fresh``          -- within the TTL.
          * ``stale_servable`` -- past the TTL but within the staleness bound;
                                  tokens still validate (stale-while-revalidate).
          * ``expired``        -- past the staleness bound; will 503.
          * ``mixed``          -- more than one issuer URL, not all in the same state.
        `usable` is True whenever a token could currently validate against the cache
        (every warmed issuer, when more than one exists).
        `backoff_armed` is True while the negative cache is suppressing fetches.
        """
        now = time.time()
        if not self._states:
            return {"state": "no_keys_cached", "usable": False, "backoff_armed": False}
        snapshots = [self._status_of(state, now) for state in self._states.values()]
        if len(snapshots) == 1:
            return snapshots[0]
        usable = all(bool(s["usable"]) for s in snapshots)
        backoff_armed = any(bool(s["backoff_armed"]) for s in snapshots)
        states = {str(s["state"]) for s in snapshots}
        state = next(iter(states)) if len(states) == 1 else "mixed"
        return {
            "state": state,
            "usable": usable,
            "backoff_armed": backoff_armed,
            "issuers": len(snapshots),
        }

    def _status_of(self, state: _IssuerJwksState, now: float) -> dict[str, object]:
        keys = state.keys
        if keys is None:
            cache_state = "no_keys_cached"
            usable = False
        else:
            age = now - float(state.fetched_at)
            if age <= _JWKS_TTL_SECONDS:
                cache_state = "fresh"
                usable = True
            elif age <= _JWKS_STALE_MAX_AGE_SECONDS:
                cache_state = "stale_servable"
                usable = True
            else:
                cache_state = "expired"
                usable = False
        return {
            "state": cache_state,
            "usable": usable,
            "backoff_armed": state.backoff_active(now),
        }

    def usable(self, now: float, state: _IssuerJwksState | None = None) -> dict[str, Any]:
        """Return the cached document if it is fresh, or stale but within the bound.

        Raises the 503 when the cache is empty or has aged past
        _JWKS_STALE_MAX_AGE_SECONDS. This is the single place that decides
        "serve stale" versus "refuse to serve", so the staleness policy cannot
        drift between the TTL path and the failure path.
        """
        target = state if state is not None else self._sole_state()
        cached = None if target is None else target.keys
        fetched_at = 0.0 if target is None else target.fetched_at
        if cached is not None:
            age = now - float(fetched_at)
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

    async def _fetch_locked(self, now: float, jwks_uri: str, state: _IssuerJwksState) -> bool:
        """Fetch and store the JWKS document. The caller MUST hold ``state._refresh_lock``.

        Returns True on success. On failure it arms the negative cache and returns
        False rather than raising -- the caller may still have a stale document it
        can legitimately serve, and that decision belongs to ``usable()``.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(jwks_uri, timeout=5.0)
                resp.raise_for_status()
                payload: object = resp.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
                raise ValueError("response body is not a JWKS document")
            document: dict[str, Any] = payload
        except Exception as e:
            state.last_failure_at = now
            logger.error(
                "JWKS fetch failed (%s); suppressing further attempts for %ds.",
                type(e).__name__,
                _JWKS_FAILURE_BACKOFF_SECONDS,
            )
            return False
        state.keys = document
        state.fetched_at = now
        state.last_failure_at = 0.0
        logger.info("JWKS refreshed: %d key(s).", len(document["keys"]))
        return True

    async def get(
        self, settings: Auth0Settings, *, jwks_uri: str | None = None
    ) -> dict[str, Any]:
        """Return a usable JWKS document, refreshing it when the cached copy expired.

        Never raises for an ordinary identity-provider failure while a usable
        cached copy exists. Raises HTTP 503 (with Retry-After) only when there is
        nothing safe to serve.

        ``jwks_uri`` must be a URL from configuration (single-issuer resolver or
        a trusted-issuer map entry) -- never from an unverified token claim.
        """
        if not jwks_uri:
            _issuer, jwks_uri = await resolve_oidc(settings)
        state = self._state_for(jwks_uri)
        now = time.time()
        cached = state.keys
        if cached is not None and (now - float(state.fetched_at)) <= _JWKS_TTL_SECONDS:
            return cached

        if state.backoff_active(now):
            # A recent attempt failed. Do not touch the identity provider; serve
            # stale if we can, 503 if we cannot.
            return self.usable(now, state)

        async with state._refresh_lock:
            # Double-checked: another coroutine may have refreshed while we waited
            # on the lock. Same pattern as auth0_management.py:40-46.
            now = time.time()
            cached = state.keys
            if cached is not None and (now - float(state.fetched_at)) <= _JWKS_TTL_SECONDS:
                return cached
            if not state.backoff_active(now):
                await self._fetch_locked(now, jwks_uri, state)
            return self.usable(time.time(), state)

    async def force_refresh(
        self, settings: Auth0Settings, *, jwks_uri: str | None = None
    ) -> dict[str, Any] | None:
        """Force one JWKS refresh after a `kid` miss, subject to a cooldown.

        Returns whatever document is cached afterwards -- refreshed, unchanged, or
        None if the cache was empty and the refresh failed. The caller re-runs key
        selection against it, which is why this returns the current document rather
        than a success flag: two concurrent `kid` misses then both benefit from the
        single refresh the first one performed.
        """
        if not jwks_uri:
            _issuer, jwks_uri = await resolve_oidc(settings)
        state = self._state_for(jwks_uri)
        async with state._refresh_lock:
            now = time.time()
            last_forced = state.last_forced_refresh_at
            cooldown_active = (
                last_forced > 0.0 and (now - last_forced) < _JWKS_KID_REFRESH_COOLDOWN_SECONDS
            )
            if not cooldown_active and not state.backoff_active(now):
                # Stamp the cooldown BEFORE fetching: a failed forced refresh must
                # consume the window too, or a flood of bogus kids retries forever.
                state.last_forced_refresh_at = now
                await self._fetch_locked(now, jwks_uri, state)
            return state.keys

    def warn_roles_claim_absent(self, claim: str, claim_present: bool) -> None:
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
        if (now - self._roles_claim_warned_at) < _ROLES_CLAIM_WARN_INTERVAL_SECONDS:
            return
        self._roles_claim_warned_at = now
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


# One JWKS cache per process -- production lifetime is unchanged.
_jwks_cache = JwksCache()


def get_auth0_settings() -> Auth0Settings:
    """FastAPI-overridable dependency: Auth0 settings for this process."""
    return get_settings().auth0


def get_jwks_cache() -> JwksCache:
    """FastAPI-overridable dependency: the process JWKS cache."""
    return _jwks_cache


def jwks_cache_status() -> dict[str, object]:
    """Snapshot of the process JWKS cache. /ready may also inject ``get_jwks_cache``."""
    return get_jwks_cache().status()

def _jwks_unavailable() -> HTTPException:
    """The 503 returned when no usable key set exists.

    Deliberately a *factory*, not a raise: several call sites need it and the
    Retry-After value must be identical at every one of them. The detail text
    is generic -- it names no URL, no exception, and no token material.
    """
    _log_auth_event("jwks_unavailable", "jwks_unavailable")
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Authentication temporarily unavailable",
        headers={"Retry-After": str(_JWKS_RETRY_AFTER_SECONDS)},
    )


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


def _unauthorized(
    detail: str,
    *,
    error: str = "invalid_token",
    error_description: str | None = None,
    reason: str = "invalid_token",
) -> HTTPException:
    """
    The 401 returned for every bearer-token fault, with its RFC 6750 §3
    challenge attached.

    Factory, not a raise -- mirrors _jwks_unavailable() above. Six call sites
    in get_current_user need an identical WWW-Authenticate shape; building it
    in exactly one place means the header can never drift out of sync across
    them, and any future call site gets it correct by construction instead of
    by remembering to copy six lines.

    `detail` becomes the JSON body's "detail" field exactly as it did before
    this change (FastAPI's default exception handler serializes
    HTTPException.detail verbatim), so existing assertions like
    `response.json() == {"detail": "Token expired"}` are unaffected by this
    refactor. `error_description` defaults to `detail` when not given; the two
    are allowed to diverge (see the "Token expired" call site below) so the
    header can carry a slightly more descriptive phrase than the terse body
    string, without changing the body's wire format.

    Both `detail` and `error_description` MUST be static, developer-authored
    strings -- never an exception message, a claim value, or any fragment of
    the token itself. Every call site in this module honours that already
    (see auth0.py's existing discipline around never logging the
    attacker-controlled `kid`); this function does not enforce it at runtime,
    only by code-review convention -- see Security Considerations.
    """
    _log_auth_event("denied", reason)
    exc = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={
            "WWW-Authenticate": (
                f'Bearer realm="api", error="{error}", '
                f'error_description="{error_description or detail}"'
            )
        },
    )
    # Carry the bounded, developer-authored reason on the exception so
    # callers (and tests) can distinguish expired / malformed / unknown_kid
    # without parsing the human-readable detail. This is the same fixed
    # vocabulary the "denied" event above uses -- never a token or claim value.
    exc.auth_reason = reason  # type: ignore[attr-defined]
    return exc


def _token_audiences(claims: dict[str, Any]) -> list[str]:
    """Return the token's ``aud`` values as a list of non-empty strings."""
    raw = claims.get("aud")
    if isinstance(raw, str) and raw:
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str) and item]
    return []


def _intersect_audiences(token_audiences: list[str], allowed: tuple[str, ...] | list[str]) -> str | None:
    """First token audience that is explicitly allowed for this issuer, else None.

    python-jose's ``jwt.decode`` takes a single audience string (``audience not
    in token_aud_list``), so multi-audience issuers must pick one matching
    value rather than passing the whole allowlist. The match is taken from
    unverified claims only to choose *which* string to pass; signature
    verification still runs against that string.
    """
    allowed_set = set(allowed)
    for audience in token_audiences:
        if audience in allowed_set:
            return audience
    return None


async def _resolve_verification_targets(
    settings: Auth0Settings, unverified_claims: dict[str, Any]
) -> tuple[str, str, str]:
    """Return ``(issuer, audience, jwks_uri)`` for this token, or raise 401.

    JWKS URLs come only from configuration, never from the unverified ``iss``.
    """
    token_iss = unverified_claims.get("iss")
    token_audiences = _token_audiences(unverified_claims)

    if settings.has_explicit_trusted_issuers():
        # Exact iss lookup in the configured map. Unknown issuers, missing iss,
        # and malformed AUTH0_TRUSTED_ISSUERS all fail closed (empty map).
        # The JWKS URL is taken from the map entry, never from the token.
        trusted = settings.lookup_trusted_issuer(token_iss if isinstance(token_iss, str) else None)
        if trusted is None:
            raise _unauthorized("Invalid claims", error="invalid_token", reason="untrusted_issuer")
        audience = _intersect_audiences(token_audiences, trusted.audiences)
        if audience is None:
            raise _unauthorized("Invalid claims", error="invalid_token", reason="invalid_audience")
        return trusted.issuer, audience, trusted.jwks_uri

    resolved_issuer, jwks_uri = await resolve_oidc(settings)
    if not settings.audience:
        raise _unauthorized("Invalid claims", error="invalid_token", reason="invalid_audience")
    audience = _intersect_audiences(token_audiences, [settings.audience])
    if audience is None:
        raise _unauthorized("Invalid claims", error="invalid_token", reason="invalid_audience")
    return resolved_issuer, audience, jwks_uri


def _extract_string_list(payload: dict[str, Any], claim: str) -> tuple[list[str], str | None]:
    """Return (values, warning_kind). warning_kind is 'not_a_list' or None.

    Non-string / empty entries are dropped. A missing claim is ``[]`` with no
    warning kind -- callers decide whether absence is noteworthy.
    """
    if claim not in payload:
        return [], None
    raw = payload[claim]
    if not isinstance(raw, list):
        return [], "not_a_list"
    return [item for item in raw if isinstance(item, str) and item], None


def _extract_permissions(payload: dict[str, Any], settings: Auth0Settings) -> list[str]:
    """Permissions from the configured claim plus the OAuth ``scope`` string.

    Roles never imply permissions. Missing or malformed claims yield no
    permissions (fail closed at authorization time).
    """
    collected: list[str] = []
    claim_values, warning = _extract_string_list(payload, settings.permissions_claim)
    if warning == "not_a_list":
        logger.warning(
            "Permissions claim %r is present but is not a list; treating the token as having "
            "no permissions from that claim.",
            settings.permissions_claim,
        )
    else:
        collected.extend(claim_values)

    scope = payload.get("scope")
    if isinstance(scope, str):
        collected.extend(part for part in scope.split() if part)
    elif scope is not None:
        logger.warning("Token 'scope' claim is not a string; ignoring it.")

    seen: set[str] = set()
    unique: list[str] = []
    for item in collected:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


class AuthenticatedUser(BaseModel):
    """Identity extracted from a verified access token (P3 #28).

    Frozen so authorization attributes cannot be reassigned after construction.
    Invalid state (empty ``sub``, non-string role/permission entries) is rejected
    at construction -- it must never silently become an authorization bypass.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    sub: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    email_verified: bool = False
    permissions: list[str] = Field(default_factory=list)

    @field_validator("sub")
    @classmethod
    def _sub_must_be_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("sub must be a non-empty string")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email_is_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("roles", "permissions", mode="before")
    @classmethod
    def _none_means_empty(cls, value: object) -> object:
        if value is None:
            return []
        return value

    @field_validator("roles", "permissions")
    @classmethod
    def _claim_lists_are_non_empty_strings(cls, value: list[object]) -> list[str]:
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError("must be a list of non-empty strings")
            out.append(item)
        return out


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
    settings: Annotated[Auth0Settings | None, Depends(get_auth0_settings)] = None,
    jwks_cache: Annotated[JwksCache | None, Depends(get_jwks_cache)] = None,
) -> AuthenticatedUser:
    if credentials is None:
        raise _unauthorized("Missing bearer token", error="invalid_request")

    if settings is None:
        settings = get_auth0_settings()
    if jwks_cache is None:
        jwks_cache = get_jwks_cache()
    token = credentials.credentials

    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception:
        raise _unauthorized("Malformed token", error="invalid_request", reason="malformed")

    try:
        unverified_claims = jwt.get_unverified_claims(token)
    except Exception:
        raise _unauthorized("Malformed token", error="invalid_request", reason="malformed")

    expected_issuer, expected_audience, jwks_uri = await _resolve_verification_targets(
        settings, unverified_claims
    )

    jwks = await jwks_cache.get(settings, jwks_uri=jwks_uri)
    kid = unverified_header.get("kid")
    rsa_key = _select_rsa_key(jwks, kid)
    if rsa_key is None:
        # Auth0 rotates signing keys without notice. Before rejecting the
        # token, force one refresh (cooldown-guarded) and look again -- this is
        # what turns a rotation from an hour-long outage into a single slow
        # request. If the kid is still absent, the 401 below stands.
        refreshed = await jwks_cache.force_refresh(settings, jwks_uri=jwks_uri)
        if refreshed is not None:
            rsa_key = _select_rsa_key(refreshed, kid)
    if rsa_key is None:
        # The kid comes from an unverified header and is attacker-controlled,
        # so it is deliberately not echoed into the log line -- and, per
        # _unauthorized()'s own contract, not into the WWW-Authenticate header
        # either.
        logger.warning("Rejecting token: its signing key id is not in the JWKS.")
        raise _unauthorized("Unknown signing key", error="invalid_token", reason="unknown_kid")

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=list(_ALLOWED_ALGORITHMS),
            audience=expected_audience,
            issuer=expected_issuer,
            options={"leeway": _CLOCK_SKEW_LEEWAY_SECONDS},
        )
    except ExpiredSignatureError:
        raise _unauthorized(
            "Token expired",
            error="invalid_token",
            error_description="The access token expired",
            reason="expired",
        )
    except JWTClaimsError:
        raise _unauthorized("Invalid claims", error="invalid_token", reason="invalid_claims")
    except Exception:
        raise _unauthorized("Invalid token", error="invalid_token", reason="invalid_token")

    roles, roles_warning = _extract_string_list(payload, settings.roles_claim)
    if roles_warning == "not_a_list":
        logger.warning(
            "Roles claim %r is present but is not a list; treating the token as having "
            "no roles.",
            settings.roles_claim,
        )
        roles = []
    if not roles:
        jwks_cache.warn_roles_claim_absent(settings.roles_claim, settings.roles_claim in payload)
    permissions = _extract_permissions(payload, settings)
    # Real Auth0 access tokens don't carry a plain "email" claim -- it has to be
    # stamped on via a Post-Login Action as the namespaced settings.email_claim
    # (see config.py). Fall back to plain "email" for OIDC providers that do put
    # it on the access token by default (e.g. the Keycloak realm used in tests).
    raw_email = payload.get(settings.email_claim) or payload.get("email")
    email = raw_email.strip().lower() if isinstance(raw_email, str) else None
    email = email or None
    # Same fallback shape as email itself: prefer the namespaced claim, fall
    # back to a plain "email_verified" for OIDC providers that put it there
    # natively (standard OIDC ID-token claim; some providers, unlike Auth0,
    # also put it on the access token). Absent entirely -> False, fail closed
    # -- an IdP or Action that hasn't been updated yet must not silently
    # treat an unverified (or unsigned-as-verified) email as proof of
    # ownership. A namespaced claim that is PRESENT (with any value,
    # including null or a malformed one) must not fall through to a plain
    # email_verified: true on the same token.
    if settings.email_verified_claim in payload:
        raw_email_verified: Any = payload[settings.email_verified_claim]
    else:
        raw_email_verified = payload.get("email_verified")
    # Strict boolean check, never Python truthiness: only the JSON boolean
    # `true` verifies the email. Strings ("true", "false"), numbers (1),
    # lists, objects, and null are all unverified -- a misconfigured or
    # compromised Action stamping a malformed value must fail closed, not
    # accidentally satisfy the verified-email ownership fallback.
    email_verified = raw_email_verified is True
    # Auth0 normally always provides a string subject, but it is an identity
    # key in Platform data.  A signed token without a usable value is still an
    # invalid token, never an application error (or a non-string owner id).
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise _unauthorized("Invalid token", error="invalid_token", reason="missing_sub")
    _log_auth_event("success", "validated", sub=sub)
    return AuthenticatedUser(
        sub=sub, email=email, roles=roles, email_verified=email_verified, permissions=permissions
    )


async def get_optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
    settings: Annotated[Auth0Settings | None, Depends(get_auth0_settings)] = None,
    jwks_cache: Annotated[JwksCache | None, Depends(get_jwks_cache)] = None,
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> AuthenticatedUser | None:
    """Like get_current_user, but returns None when no credentials are present.

    Endpoints that stay open to anonymous callers still trust a token when one
    is given. A present-but-invalid token is rejected (401/403), never treated
    as anonymous -- otherwise a malformed or expired token would create a
    public resource on an optional-auth creation path. An Auth0 outage (503)
    is likewise not downgraded to anonymous.

    Anonymous means the Authorization header is completely absent. A header
    that IS supplied but that HTTPBearer could not extract a non-empty Bearer
    credential from (empty ``Bearer``, an unsupported scheme such as ``Basic``,
    a bare scheme with no token) is a malformed authentication attempt and is
    rejected with the standardized 401 -- it must never be downgraded to
    anonymous access. The raw ``authorization`` header is inspected only to
    make that absent-vs-malformed distinction; token validation itself stays
    entirely inside get_current_user.
    """
    if credentials is None:
        if authorization is None:
            # No Authorization header at all: a genuinely anonymous caller.
            # This path never touches the identity provider, so anonymous
            # access keeps working normally during an Auth0 outage.
            return None
        raise _unauthorized(
            "Invalid authorization header",
            error="invalid_request",
            reason="invalid_authorization_header",
        )
    return await get_current_user(
        credentials, settings=settings, jwks_cache=jwks_cache
    )
