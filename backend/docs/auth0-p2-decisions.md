# Auth0 P2 — Decisions Register

Records the P2 decision gates and their status. Engineering-only decisions that
the P2 planning material recommends and that carry no product/privacy/infra
judgement have been ratified here and implemented. Decisions that require a
product, data-protection, infrastructure, or project-owner call are left
**OPEN** and must not be guessed — the affected work is blocked on them.

No credential value appears in this document.

Last updated: 2026-08-24.

| ID | Question | Status | Decision | Affected P2 item |
|----|----------|--------|----------|------------------|
| D-1 | Enable the Auth0 Management API, or document `/api/v1/me` writes as intentionally disabled? | **OPEN — product** | Not decided. Endpoints remain 503 (unconfigured) in every cluster. | #23 config (Phase 4) |
| D-2 | Delete, anonymize, or retain a deleted user's run records? | **OPEN — data-protection** | Not decided. (TODO steer: anonymize.) | #22 (Phase 4) |
| D-3 | If anonymizing, is `owner_sub` also cleared (making the record `is_ownerless` → publicly readable)? | **OPEN — data-protection** | Not decided. Sharpest unresolved question; not addressed by the source TODO. | #22 (Phase 4) |
| D-4 | Does a metrics backend exist / will one be provisioned? | **OPEN — infrastructure** | Not decided. No metrics library is declared; no `/metrics` endpoint exists. | #19 counters + histogram (Phase 2b) |
| D-5 | Should `/ready` gate on auth or inform only? | **DECIDED — inform** (2026-08-24) | `/ready` reports JWKS-cache health as a non-gating `info.auth` field; `ok` stays computed from MongoDB + Temporal. No outbound Auth0 call. | #19c (implemented) |
| D-6 | Gate the demo router or delete it? | **DECIDED — gate** (2026-08-24) | `ENABLE_RBAC_DEMO` (default false) gates `include_router`. `/api/v1/demo/*` 404s and is absent from OpenAPI in production; the test env enables it so the Keycloak suite still runs. | #20 (implemented) |
| D-7 | Which Auth0 tenant is production? | **OPEN — project owner (inherited from P0 #6)** | Not decided. All overlays point at a `dev-*` tenant; `backend/CLAUDE.md`'s decision block has unfilled placeholders. | #23 Outcome 1; #26 production values |
| D-8 | Management API retry policy (max attempts, base delay, multiplier, jitter, deadline; honour `Retry-After`?; exhausted-429 status). | **OPEN — team** | Not decided; not specified by any source. | #23 retry (Phase 4) |
| D-9 | Discovery base URL when `AUTH0_ISSUER` unset; discovery cache TTL & negative-cache window. | **DECIDED — engineering** (2026-08-24) | Base = `settings.issuer` if set, else `https://{AUTH0_DOMAIN}/`. TTL 3600 s, failure backoff 10 s (mirrors the JWKS constants). See `common/auth/discovery.py`. | #16 (implemented) |
| D-10 | DI-seam shape: A (overridable settings dependency), B (encapsulated cache), or both? | **DECIDED — both, A first** (2026-08-24) | Overridable `get_auth0_settings`/`get_jwks_cache` FastAPI dependencies (A) plus a process-scoped `JwksCache` instance (B). Tests inject a settings copy and a fresh cache; production still has one settings object and one cache per process. | #24 (implemented) |
| D-11 | Hash or truncate `sub`? Latency histogram measurement point? Is a global JSON log-format change acceptable? | **PARTIALLY DECIDED** (2026-08-24) | `sub` is SHA-256-hashed and truncated to 12 hex chars in `_log_auth_event`. The global JSON formatter is in place (`log_config.py`); **compatibility with deployed-cluster log consumers is unverified from the repo** and remains an infra check. Histogram placement is moot until D-4. | #19a (implemented); #19 metrics (blocked) |

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
