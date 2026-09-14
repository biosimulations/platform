"""
In-process rate limiting for workflow-starting endpoints (TODO P1 #10) and
POST /compatibility/check (separate budget).

Protects POST /verify/omex, POST /verify/runs, and POST /simulations/run -- the
three endpoints that start a Temporal workflow and, downstream, submit a job to
biosimulations.org. All three share ONE logical rate budget ("workflow starts"),
not three separate ones -- a caller denied on one endpoint could otherwise
recover the same throughput by round-robining across the other two, which
would not actually protect the shared resources these endpoints compete for.

POST /compatibility/check uses ``compatibility_rate_limit`` with the same
per-pod window and authenticated/anonymous ceilings but a ``compat:`` key
prefix, so the run wizard cannot starve simulation starts.

Design, and why it is intentionally small (see the tutorial's Section 9 Step 2
for the full rationale):

  * ONE process, in-memory counters. `api` runs 3 replicas
    (kustomize/base/api.yaml:8) and there is no Redis or other shared cache
    anywhere in the stack (confirmed: grep -rni 'redis' across kustomize/ and
    backend/pyproject.toml returns nothing) -- MongoDB is the only shared
    datastore every pod already has, and routing every rate-limit check
    through an extra network round-trip is not justified for a P1 item with
    no observed abuse yet. The accepted, explicitly-documented consequence:
    this limiter is PER-POD, not global. To target a global ceiling G,
    configure the per-pod setting as G / replica_count (currently G / 3) --
    see RateLimitSettings in config.py.
  * A fixed window, not a sliding log. O(1) memory and O(1) work per key; the
    one known imprecision (up to ~2x the configured rate across a window
    boundary) is an accepted P1-scoped trade-off, not a security hole -- the
    configured number is a soft operational guard, not a hard resource cap.
  * The same module-level-state-dict + factory-function idiom as
    common/auth/auth0.py: a plain dict as the cache (_rate_limit_buckets,
    mirroring _jwks_cache), and a _rate_limited() HTTPException factory
    (mirroring _jwks_unavailable()) so every call site raises an identical
    429 with an identical Retry-After.
  * `time.time()`, not `time.monotonic()`, as the clock -- specifically so
    tests/fixtures/jwks_fixtures.py's existing FakeClock is directly reusable
    here unmodified (it patches the module-global name `time`).

A precise, cluster-wide (not per-process) limit needs a Mongo- or Redis-backed
shared counter. Named as explicit P2/P3 future work; not built here.
"""

import ipaddress
import logging
import threading
import time

from fastapi import Depends, HTTPException, Request, status

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_optional_user
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)

# Module-level rate-limit state: one entry per caller-identity key. Each value
# is a fixed window -- {"window_start": <epoch seconds, floored to the window
# boundary>, "count": <requests seen so far in this window>}. Mirrors the
# shape and naming convention of auth0.py's `_jwks_cache`.

_rate_limit_buckets: dict[str, dict[str, float | int]] = {}
# Serialises increment + eviction. workflow_rate_limit is a sync FastAPI
# dependency, so threading.Lock (not asyncio.Lock) is the right primitive.
_rate_limit_lock = threading.Lock()
# Sampled eviction counter -- sweep stale keys every N checks so a caller
# rotating spoofed identities cannot grow the dict without bound.
_rate_limit_check_count = 0
_EVICT_EVERY_N_CHECKS = 32

def _reset_rate_limit_state() -> None:
    """
    Clear all rate-limit counters. Test-only affordance, mirroring
    auth0.py's `_reset_jwks_cache()` -- tests call this directly rather than
    reaching into the dict by hand, so the reset stays correct if the bucket
    shape ever changes.
    """
    global _rate_limit_check_count
    _rate_limit_buckets.clear()
    _rate_limit_check_count = 0

def _rate_limited(retry_after_seconds: int) -> HTTPException:
    """
    The 429 raised on quota exhaustion.

    A factory, not a raise, for the same reason `_jwks_unavailable()` is one
    in auth0.py: every protected endpoint needs an identical response, and a
    factory is the only way to guarantee that without repeating the header
    construction at each of the three call sites.
    """
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Rate limit exceeded. Slow down and retry after the indicated delay.",
        headers={"Retry-After": str(retry_after_seconds)},
    )

def _peer_is_trusted_proxy(host: str) -> bool:
    """
    True when the ASGI peer is a private, loopback, or link-local address.

    In this stack the `api` Service is reached only from the cluster network
    (ingress-nginx pod IPs are RFC 1918). A public peer means the caller
    reached the process directly (local tests, port-forward) and can set
    X-Forwarded-For themselves -- that header must not be trusted.

    REQUIRES EXTERNAL ACTION: this repo's Ingress objects
    (kustomize/overlays/biosim-{gke,rke}/ingress.yaml) do not set
    `nginx.ingress.kubernetes.io/use-forwarded-headers`. ingress-nginx's
    default for that ConfigMap key is `false`, which means nginx *generates*
    X-Forwarded-For from the TCP peer and does not pass through a
    client-supplied copy. Confirm the cluster's ingress-nginx ConfigMap has
    not flipped that default before treating the anonymous quota as a hard
    guarantee. Recorded in backend/CLAUDE.md → Rate Limiting.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _client_ip(request: Request) -> str:
    """
    Best-effort caller IP for anonymous callers.

    Trusts the first hop of X-Forwarded-For only when the immediate ASGI
    peer looks like a cluster proxy (private/loopback/link-local). Otherwise
    uses the ASGI-reported peer, which a remote caller cannot spoof.
    """
    client = request.client
    peer = client.host if client is not None else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _peer_is_trusted_proxy(peer):
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    return peer

def client_identity(user: AuthenticatedUser | None, request: Request) -> tuple[str, bool]:
    """
    Resolve the rate-limit key and whether it is an authenticated identity.

    Returns (key, is_authenticated). Keys are namespaced ("sub:..." vs
    "ip:...") so an authenticated `sub` string can never collide with an
    IP-shaped anonymous key.

    Deliberately independent of whether the calling endpoint itself requires
    authentication -- see the tutorial's Section 9 Step 2, Decision 5. Works
    identically whether or not TODO #9 (mandatory auth on /verify/omex and
    /verify/runs) has landed yet.
    """
    if user is not None:
        return f"sub:{user.sub}", True
    return f"ip:{_client_ip(request)}", False

def _evict_stale_buckets(window_start: float) -> None:
    stale = [k for k, bucket in _rate_limit_buckets.items() if bucket["window_start"] < window_start]
    for k in stale:
        del _rate_limit_buckets[k]


def _check_and_increment(
        key: str, limit: int, window_seconds: int, now: float
) -> tuple[bool, int]:
    """
    Increment the fixed-window counter for `key` and report the outcome.

    Returns (allowed, retry_after_seconds). retry_after_seconds is always
    computed -- the seconds remaining until the current window rolls over --
    so the caller has one code path regardless of outcome.
    """
    global _rate_limit_check_count
    window_start = (now // window_seconds) * window_seconds
    with _rate_limit_lock:
        _rate_limit_check_count += 1
        if _rate_limit_check_count % _EVICT_EVERY_N_CHECKS == 0:
            _evict_stale_buckets(window_start)
        bucket = _rate_limit_buckets.get(key)
        if bucket is None or bucket["window_start"] != window_start:
            bucket = {"window_start": window_start, "count": 0}
            _rate_limit_buckets[key] = bucket
        bucket["count"] = int(bucket["count"]) + 1
        retry_after = max(1, int(window_start + window_seconds - now) + 1)
        return int(bucket["count"]) <= limit, retry_after

def _enforce_rate_limit(
        request: Request,
        user: AuthenticatedUser | None,
        *,
        key_prefix: str | None = None,
) -> None:
    settings = get_settings().ratelimit
    if not settings.enabled:
        return
    ident, authenticated = client_identity(user, request)
    key = f"{key_prefix}:{ident}" if key_prefix else ident
    limit = settings.authenticated_per_window if authenticated else settings.anonymous_per_window
    allowed, retry_after = _check_and_increment(key, limit, settings.window_seconds, time.time())
    if not allowed:
        # The key itself (a `sub` or an IP) is never logged -- consistent
        # with auth0.py's discipline of never logging raw claims or token
        # material. The identity *class* is enough to distinguish an
        # authenticated-caller quota problem from an anonymous one.
        logger.warning(
            "Rate limit exceeded for %s caller; retry_after=%ds",
            "authenticated" if authenticated else "anonymous",
            retry_after,
        )
        raise _rate_limited(retry_after)


def workflow_rate_limit(
        request: Request,
        user: AuthenticatedUser | None = Depends(get_optional_user),
) -> None:
    """
    FastAPI dependency: enforce the shared workflow-start budget.

    Wire this into every workflow-starting endpoint's `dependencies=[]` list:
    POST /verify/omex, POST /verify/runs, POST /simulations/run. All three
    share one logical bucket per caller identity (Section 9 Step 2, Decision
    3) -- this is deliberate, not an oversight.

    `Depends(get_optional_user)` here is the same callable FastAPI already
    resolves for /simulations/run's own `user` parameter (and, once TODO #9
    lands, the same underlying verification `Depends(get_current_user)` on
    /verify/omex and /verify/runs builds on). FastAPI caches a dependency's
    result per request by the identity of the callable, so within a single
    request this token is verified against Auth0 at most once regardless of
    how many dependencies request it -- adding this rate limiter does not add
    a second JWKS round-trip.
    """
    _enforce_rate_limit(request, user)


def compatibility_rate_limit(
        request: Request,
        user: AuthenticatedUser | None = Depends(get_optional_user),
) -> None:
    """
    FastAPI dependency: enforce the compatibility-check budget.

    Separate from ``workflow_rate_limit`` so the run wizard cannot starve
    simulation starts (and vice versa). Uses the same per-pod window and
    authenticated/anonymous ceilings, keyed as ``compat:<identity>``.
    """
    _enforce_rate_limit(request, user, key_prefix="compat")
