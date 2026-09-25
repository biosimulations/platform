# Auth0 P2 — Decisions Register

Records the P2 decision gates and their status. Engineering-only decisions that
the P2 planning material recommends and that carry no product/privacy/infra
judgement have been ratified here and implemented. Decisions that require a
product, data-protection, infrastructure, or project-owner call are left
**OPEN** and must not be guessed — the affected work is blocked on them.

No credential value appears in this document.

Last updated: 2026-09-18.

| ID | Question | Status | Decision | Affected P2 item |
|----|----------|--------|----------|------------------|
| D-1 | Enable the Auth0 Management API, or document `/api/v1/me` writes as intentionally disabled? | **OPEN — product** | Not decided. Endpoints remain 503 (unconfigured) in every cluster. | #23 config (Phase 4) |
| D-2 | Delete, anonymize, or retain a deleted user's run records? | **OPEN — data-protection** | Not decided. (TODO steer: anonymize.) | #22 (Phase 4) |
| D-3 | If anonymizing, is `owner_sub` also cleared (making the record `is_ownerless` → publicly readable)? | **OPEN — data-protection** | Not decided. Sharpest unresolved question; not addressed by the source TODO. | #22 (Phase 4) |
| D-4 | Does a metrics backend exist / will one be provisioned? | **OPEN — infrastructure** | Not decided. No metrics library is declared; no `/metrics` endpoint exists. | #19 counters + histogram (Phase 2b) |
| D-5 | Should `/ready` gate on auth or inform only? | **DECIDED — inform** (2026-08-24) | `/ready` reports JWKS-cache health as a non-gating `info.auth` field; `ok` stays computed from MongoDB + Temporal. No outbound Auth0 call. | #19c (implemented) |
| D-6 | Gate the demo router or delete it? | **DECIDED — gate** (2026-08-24) | `ENABLE_RBAC_DEMO` (default false) gates `include_router`. `/api/v1/demo/*` 404s and is absent from OpenAPI in production; the test env enables it so the Keycloak suite still runs. | #20 (implemented) |
| D-7 | Which Auth0 tenant is production? | **OPEN — project owner (inherited from P0 #6)** | Not decided. All overlays point at a `dev-*` tenant; `backend/CLAUDE.md`'s decision block has unfilled placeholders. | #23 Outcome 1; #26 production values |
| D-8 | Management API retry policy (max attempts, base delay, multiplier, jitter, deadline; honour `Retry-After`?; exhausted-429 status). | **DECIDED — engineering** (2026-08-25) | 3 attempts (1+2), base 0.5 s × 2.0 full-jitter, 15 s total deadline; retry 429/5xx/transport only; honour a 429 `Retry-After` clamped to 30 s; exhausted 429 → HTTP 503 + `Retry-After` (`Auth0ManagementRateLimited`), exhausted 5xx/transport → HTTP 502 (`Auth0ManagementUnavailable`). Token cache/lock left byte-for-byte unchanged (EH-11). Password-change ticket issuance (`create_password_change_ticket`) is exempt: it is non-idempotent, so it makes a single 10 s attempt and never retries; upstream 429 → 503 `Retry-After: 10`, other upstream/malformed/unsafe responses → 502. See `common/auth/auth0_management.py`. | #23 retry (implemented); password-reset tickets (implemented) |
| D-9 | Discovery base URL when `AUTH0_ISSUER` unset; discovery cache TTL & negative-cache window. | **DECIDED — engineering** (2026-08-24) | Base = `settings.issuer` if set, else `https://{AUTH0_DOMAIN}/`. TTL 3600 s, failure backoff 10 s (mirrors the JWKS constants). See `common/auth/discovery.py`. | #16 (implemented) |
| D-10 | DI-seam shape: A (overridable settings dependency), B (encapsulated cache), or both? | **DECIDED — both, A first** (2026-08-24) | Overridable `get_auth0_settings`/`get_jwks_cache` FastAPI dependencies (A) plus a process-scoped `JwksCache` instance (B). Tests inject a settings copy and a fresh cache; production still has one settings object and one cache per process. | #24 (implemented) |
| D-11 | Hash or truncate `sub`? Latency histogram measurement point? Is a global JSON log-format change acceptable? | **PARTIALLY DECIDED** (2026-08-24) | `sub` is SHA-256-hashed and truncated to 12 hex chars in `_log_auth_event`. The global JSON formatter is in place (`log_config.py`); **compatibility with deployed-cluster log consumers is unverified from the repo** and remains an infra check. Histogram placement is moot until D-4. | #19a (implemented); #19 metrics (blocked) |
| D-12 | Step-up (recent-authentication) policy for direct hosted password change: enforce an IdP-asserted freshness claim, or keep ordinary token possession sufficient? | **DECIDED — enforce, opt-in, gated before the reset UI ships** (2026-09-18) | `POST /api/v1/me/password-reset` accepts an `auth_time` claim (OIDC standard; claim name configurable via `AUTH0_AUTH_TIME_CLAIM`) as the only accepted evidence of a recent interactive sign-in. A refreshed access token and `iat` are explicitly **not** evidence. Absent, malformed, future-dated (beyond 60 s skew), or older than `AUTH0_PASSWORD_RESET_MAX_AUTH_AGE_SECONDS` (default 300 s) → **403**, no ticket. Enforcement is behind `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` (default **false**) because it requires the tenant's Post-Login Action to stamp the claim; `_validate_auth0_configuration()` logs a WARNING at startup whenever `AUTH0_PASSWORD_RESET_CLIENT_ID` is set without the gate, so enabling the route without the assurance is never silent. **Ratification still required for the tenant-side half** (stamp `auth_time` in the Action, then set the flag in each overlay) before the reset UI is released. | AUTH-MAJ-004 (mechanism implemented; tenant config + UI are the rollout gate) |

## #24 — DI / cache testability seam: implemented (D-10 ratified 2026-08-24)

D-10 is an internal engineering choice (test seam shape). It was OPEN with the
plan recommendation **both, A first**. That recommendation is now ratified and
implemented. No product, privacy, infrastructure, or startup-gate decision was
required; `Auth0Settings.configuration_errors()` is unchanged.

**Shape A:** `get_auth0_settings()` returns `get_settings().auth0`.
`get_current_user` / `get_optional_user` take it via `Depends` (with a `None`
default so direct coroutine tests still work) and thread the resolved settings
into JWKS retrieval and `resolve_oidc(settings)`. HTTP tests override
`app.dependency_overrides[get_auth0_settings]`. Direct tests patch
`get_auth0_settings` via `tests/fixtures/auth_seam.py`.

**Shape B:** `_jwks_cache`, the refresh lock, and roles-claim warn state live on
a `JwksCache` instance. Production keeps one process singleton (`get_jwks_cache()`).
Tests construct a fresh instance instead of calling `_reset_jwks_cache()`.
`jwks_cache_status()` and `/ready` (`Depends(get_jwks_cache)`) read that instance.
TTL, stale-while-revalidate, negative cache, forced-refresh cooldown, and
single-flight behaviour are unchanged.

**Acceptance:** tests no longer mutate `get_settings().auth0` or a module-global
JWKS dict. The Keycloak fixture supplies issuer/JWKS/audience/roles claim through
the seam (TV-11 is not implemented). The two JWKS suites pass in either order.

## #23 — Management API retry/backoff: implemented (D-8 ratified 2026-08-25)

D-8 is an internal engineering choice (retry constants + response mapping); no
source gave a steer, so conservative, interactive-friendly defaults were chosen
and are recorded above. `_send_with_retry` in `common/auth/auth0_management.py`
wraps the three resource calls (`get`/`update`/`delete_auth0_user`); the token
cache/lock is deliberately **not** wrapped (EH-11 — left byte-for-byte unchanged).

- **Retries:** 429, 5xx, and `httpx.TransportError` only. Any other 4xx is
  returned unretried so the caller's `raise_for_status()` still surfaces it
  (e.g. 404 on a deleted user).
- **Backoff:** full-jitter exponential over 0.5 s then 1.0 s, bounded by a 15 s
  total deadline; a 429 `Retry-After` is honoured verbatim (clamped to 30 s and
  the deadline) in preference to jitter.
- **Response mapping (the 429-distinguishable requirement):** an exhausted 429
  raises `Auth0ManagementRateLimited` → `update_me`/`delete_me` return **503 +
  Retry-After** (the codebase's existing "try again shortly" shape); an exhausted
  5xx or transport failure raises `Auth0ManagementUnavailable` → **502**.
- **Logging:** each retry logs a WARN naming only the operation, HTTP status,
  attempt number, and exception type — never the bearer token, the client
  secret, or a response body. A leak-guard test asserts this.

**Still open for #23 (not this decision):** whether to *enable* the Management
API at all (D-1, product) and the production tenant (D-7). Retry/backoff ships
regardless — it hardens the calls that already exist behind the 503-when-
unconfigured gate.

R6/R7 publish, project-create, and `site-admin` blockers are recorded in
`backend/docs/auth0-r6-r7-decisions.md`. They are not guessed here.

**Tests:** `tests/common/test_auth0_management_retry.py` (8 cases via
`httpx.MockTransport`, no network, `asyncio.sleep` stubbed) + the existing
`tests/users/test_router.py` mapping tests.

## D-12 — password-reset step-up policy: implemented (2026-09-18)

Engineering decided the *mechanism*; the tenant-side configuration and the UI
release remain the gate. What is in place:

- `AuthenticatedUser.auth_time` (optional) is read from the configured claim with
  strict NumericDate semantics; anything else -- including a present but
  malformed value -- is `None`, i.e. "no evidence".
- `_require_recent_authentication` in `users/router.py` refuses issuance (403, no
  ticket, `no-store`) on missing, future-dated, or stale evidence, and logs a
  bounded reason (`missing_auth_time` / `future_auth_time` / `stale_auth_time`)
  with no claim value, threshold, or account identifier.
- The gate is off unless `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` is true, and
  the startup gate warns when the reset route is configured without it.

**Still required before enabling the reset UI:** the Auth0 Post-Login Action must
stamp `auth_time` (see `auth0/actions/post-login.js`), the flag must be set in
each overlay, and the authorized development-tenant checks in the audit's
section 12 must pass. None of that is inferred here.

**Tests:** `tests/users/test_password_reset.py` (gate on/off, fresh/stale/future/
missing, configurable window) + `tests/api/test_startup_auth_config.py`
(policy defaults, non-positive window rejected, startup warning).
