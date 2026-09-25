# Authentication and Page Endpoints Audit & Improvement Plan

Audit date: 2026-09-17. Checkout: `/Users/novrusshehaj/Github/UCHC/platform`. Branch: `chore/api-improvements`. Audited HEAD: `0d408205e1a962cadcafbb44b0b4c17dab6fd31d`.

## 1. Executive Summary

Both original plans were read completely, their committed implementations compared with the earlier baseline, and the current authentication, password-reset, page-aggregation, supporting database, frontend authentication, and regression-test paths inspected. The initial working tree had **no staged, unstaged, or untracked files**. The implementations are committed work, not an outstanding working-tree patch.

The page optimization is substantially implemented: run identity and satellites overlap, cancellation is drained on run-page failure, both assemblers have asynchronous time budgets, and owned response contracts and request isolation have strong tests. The password-reset backend is also substantially implemented: verified issuer/sub account selection, database-provider restriction, server-only M2M credentials, short-lived tickets, exact-host URL validation, isolated quota, and sanitized failures. Frontend integration was explicitly deferred in that plan and remains absent.

The most important authentication findings are inconsistent issuer binding outside the reset route, acceptance of signed tokens without expiration, and destination-independent bearer attachment in the browser. The reset flow also deliberately authorizes password changes from ordinary access-token possession; stronger authentication is recommended before enabling its UI. The project-page reliability defect is failure to cancel a sibling satellite after another satellite fails. For the run page, the main concurrency optimization already works; the remaining scalability concern is unbounded response buffering and synchronous parsing/mapping/serialization, not database query count.

This report contains **8 Major recommendations and 7 Minor recommendations**. No deployed exploit or production latency regression is asserted. The issuer-collision risk depends on enabled trust configuration and overlapping subjects; frontend token disclosure depends on the requested destination accepting the browser request. These are concrete missing boundaries, not proof that a production account has been compromised.

Validation: **553 targeted tests passed**, Ruff passed, mypy passed across **167 files**, and an in-memory OpenAPI comparison matched the committed artifact. Additional local probes reproduced the identified claim, issuer-binding, header, cancellation, and token-refresh behaviors. No real Auth0 calls, reset emails, production API calls, database mutations, migrations, or deployment changes were performed. Tenant configuration, hosted reset completion, real HTTP pool behavior, upstream visibility guarantees, and representative production load remain unverified.

## 2. Audit Scope

The user-facing shorthand `/projects/page` and `/run/page` does not identify literal routes in this checkout. The actual detail routes are:

| Requested area | Actual mounted route | Handler |
| --- | --- | --- |
| `/projects/page` | `GET /projects/{project_id}/page` | `projects/router.py::get_project_page` |
| `/run/page` | `GET /runs/{run_id}/page` | `simulations/router.py::get_run_page`, on `run_summary_router` |
| Password reset | `POST /api/v1/me/password-reset` | `users/router.py::reset_my_password` |

Paths below are repository-relative; backend module paths begin with `backend/biosim_server/` unless written in full. These page routes assemble **one resource's detail page**, not a page of projects/runs. They have no pagination, count query, SQLAlchemy session, local Mongo query, JOIN, eager/lazy relationship loading, or database transaction. Nearby list/search routes were inspected to establish that distinction, not substituted for the requested routes.

Authentication tracing includes configuration, discovery/JWKS, JWT verification, principal construction, authorization consumers, Management API calls, rate limiting, profile operations, the browser token interceptor, and password-reset UI. It is not a general audit of simulation execution, storage, or all unrelated list-query optimizations.

Only this document was created. Application source, tests, configuration, dependencies, generated OpenAPI, and both original plans were left unchanged.

## 3. Sources Reviewed

### 3.1 Original Plans

Read in full:

1. `.agents/page-endpoints-performance-audit-and-optimization-plan.md` — 1,706 lines, including its implementation record and corrections. Goals: overlap run identity/satellites, preserve project identity dependency, preserve owned schemas/error and credential boundaries, add total deadlines and cancellation, establish deterministic regression tests and comparative measurements. Caching, retries, pool tuning, and frontend migration were deliberately deferred.
2. `.agents/auth0-option-a-password-reset-implementation-plan.md` — 169 lines, including revised backend-only scope and acceptance criteria. Goals: B1 issuer retention, B2 reset client configuration/disable switch, B3 single-attempt Management ticket creation, B4 authenticated issuer/sub eligibility, quota and safe response, B5 backend tests. F1–F4 are explicitly future frontend work. Tenant configuration and real hosted-flow verification are rollout gates, not completed test evidence.

The plans describe historical intent and contain some inaccurate explanatory material. They were not treated as proof of current behavior. Corrections relevant to decisions in this audit:

- The page plan's earlier claim of double specification normalization is retracted within the plan and is not a current finding. Each mapper normalizes once.
- Actual satellite URLs are `/files/{run_id}`, `/specifications/{run_id}`, and `/logs/{run_id}`; several plan examples instead show `/runs/{id}/files` and similar paths.
- Installed HTTPX 0.28.1 uses client defaults of 100 maximum connections, 20 idle keepalive connections, and 5-second keepalive expiry, not the plan's assumed 10-per-host pool. `httpx.Timeout(30.0)` sets all four timeout phases to 30 seconds; it is not a total request deadline. Confirmed using local introspection and [HTTPX resource limits](https://www.python-httpx.org/advanced/resource-limits/) / [timeouts](https://www.python-httpx.org/advanced/timeouts/).
- Ignoring local overhead, the ideal reduction from serial identity plus satellites to overlapping them is `min(T_identity, max(T_satellites))`. It is not zero merely because identity is the slower request.
- Run cancellation cannot undo work already accepted by the upstream service. The current implementation correctly uses actual tasks and drains them; the plan's original bare-coroutine sketch is not executable guidance.
- The reset plan promises cache/referrer headers on every response. Authentication dependency failures currently bypass those headers.

### 3.2 Git Changes

Commands inspected: `git status --short`, `git status --porcelain=v1`, `git diff`, `git diff --stat`, `git diff --numstat`, their staged counterparts, `git ls-files --others --exclude-standard`, recent history, relevant commit statistics, and historical diffs.

| State at audit start | Result |
| --- | --- |
| Unstaged changes relative to HEAD | None |
| Staged changes relative to HEAD | None |
| Untracked files | None; the historical plan's temporary benchmark files are not current untracked work |
| Relevant committed comparison | `23f406b..0d40820`: 23 files, 3,103 insertions, 21 deletions, including the two plans |

Relevant history:

- `03f4a7b`: page service concurrency/deadlines, page tests/baseline replicas, `backend/scripts/bench_pages.py`, and an ignore-rule change.
- `4bb027e`: reset endpoint/client/config/models/quota, issuer retention, tests, documentation, OpenAPI refresh, deterministic `scripts/generate_openapi.py`, and `.kilo` ignore rule.
- `54293b9`: removes `.agents/` ignore rule; the plans are now tracked.
- `0d40820`: edits the password-reset plan.

Additional changes beyond the narrow runtime implementations include benchmark tooling, documentation, ignore rules, and OpenAPI regeneration that captures older unrelated schema/security drift. Those generated changes do not prove that all displayed runtime behaviors were introduced by the reset commit. No frontend change exists in this historical implementation range.

### 3.3 Supporting Code

| Area | Sources and symbols inspected |
| --- | --- |
| Configuration/lifecycle | `config.py::Auth0Settings`, `parse_trusted_issuers_json`, `RateLimitSettings`, cached `get_settings`; `api/main.py` startup validation, lifespan, CORS and router mounts; `dependencies.py` HTTP/Mongo/Temporal lifecycle |
| Authentication | `common/auth/auth0.py::get_current_user`, `get_optional_user`, `_resolve_verification_targets`, `_select_rsa_key`, `AuthenticatedUser`, `JwksCache`; `common/auth/discovery.py` |
| Authorization | `common/auth/roles.py` role/permission dependencies, `is_owner`, `creation_policy`, ownership/public-resource rules; simulation router and database caller scoping |
| Auth0/self-service | `users/router.py`, `users/models.py`, `common/auth/auth0_management.py`, `common/ratelimit.py`, `log_config.py`, `auth0/README.md`, backend auth docs and example environment declarations |
| Pages | `projects/router.py::get_project_page`; `simulations/router.py::get_run_page`; all of `pages/service.py`, `pages/mapping.py`, `pages/models.py`; `common/upstream.py`; supporting summary models/mappers |
| Database distinction | `projects/database.py`, `projects/search.py::query_project_stubs/query_project_stats/ensure_indexes`; `simulations/database.py::query_simulation_runs`, ownership filtering, sort and index definitions |
| Browser boundary | `frontend/app/plugins/auth0.client.ts`; profile/login pages; project/run detail pages; `frontend/app/composables/useVisualizations.ts`; frontend package scripts |
| Tooling/contracts | Backend `pyproject.toml`, `pytest.ini`, backend guide verification commands, OpenAPI artifact/generator/inventory tests, `scripts/bench_pages.py` |

Official Auth0 references were checked for the ticket trust boundary and API contract: [password-change overview](https://auth0.com/docs/authenticate/database-connections/password-change) and [ticket endpoint](https://auth0.com/docs/api/management/v2/tickets/post-password-change). They support the distinction between emailed recovery and application-authorized ticket issuance; they do not establish this tenant's current settings.

### 3.4 Tests and Validation

Inspected page mapping, mounted contract, timeout, concurrency, cancellation, baseline, fixture, and path-encoding tests; reset router/MockTransport tests; profile tests; JWT/JWKS/discovery/trusted-issuer/permissions/roles/rate-limit/error tests; OpenAPI inventory and live-summary test requirements. Test configuration and fixtures were inspected before selecting commands.

All commands below ran from `backend/`. `--frozen --no-sync` used the installed environment without dependency/lock changes. No formatter or spec-writing generator ran.

```bash
uv run --frozen --no-sync pytest tests/pages tests/summaries tests/users \
  tests/common/test_upstream.py tests/common/test_auth0_jwks.py \
  tests/common/test_auth0_reliability.py tests/common/test_auth0_management_retry.py \
  tests/common/test_trusted_issuers.py tests/common/test_authenticated_user.py \
  tests/common/test_auth_discovery.py tests/common/test_auth0_roles_claim.py \
  tests/common/test_permissions.py tests/common/test_roles.py \
  tests/common/test_ratelimit.py tests/common/test_auth_observability.py \
  tests/api/test_auth_error_responses.py tests/api/test_openapi_endpoints.py \
  tests/api/test_ready_auth.py tests/api/test_startup_auth_config.py \
  -q -o log_cli=false -p no:cacheprovider
uv run --frozen --no-sync ruff check --no-cache .
uv run --frozen --no-sync mypy --cache-dir=/tmp/platform-audit-mypy biosim_server tests
```

| Check | Current audit result |
| --- | --- |
| Targeted pytest command above | **553 passed**, 2,602 warnings, 78.14 seconds; Python 3.14 environment. Warnings include dependency and pytest-asyncio deprecations, not test failures |
| Ruff | **Passed** |
| mypy | **Passed**, 167 source files |
| Generated OpenAPI comparison | **Passed**: `ENABLE_RBAC_DEMO=true`, compare `app.openapi()` with `yaml.safe_load` of the committed artifact entirely in memory |
| Controlled assembler benchmark | Completed locally with MockTransport; results in section 8 |
| Git whitespace/state verification | Completed; only the requested audit document was added |
| Full backend suite, real Mongo/Temporal/Keycloak, live upstream and hosted Auth0 completion | **Not run**. The selected checks require no such infrastructure; a full suite has broader infrastructure/network effects. No claim that all other tests pass |

Read-only Python probes were executed from stdin with ephemeral RSA keys, mocked HTTP transports/helpers, and ASGITransport/TestClient; no probe files were added. Results:

1. A genuinely RS256-signed foreign trusted-issuer token reached `DELETE /api/v1/me`: **204**, with mocked Management deletion called for its subject. The same token received **403** on password reset. This demonstrates the inconsistent issuer boundary without deleting any account.
2. A signed local-issuer token without `exp` reached password reset: **200** with a mocked ticket. A signed whitespace-only subject produced **500**; a padded subject was silently normalized to another string by principal construction.
3. Project files returned 500 after the specifications task started: page assembly returned **502**, while the sibling task was still running and not cancelled. The probe explicitly cancelled/drained it afterward.
4. Anonymous reset returned **401** with neither `Cache-Control` nor `Referrer-Policy`.
5. Five simultaneous failed cold M2M token acquisitions produced **five serialized token POSTs**, not one shared failed refresh result.

## 4. Current Implementation Assessment

### 4.1 Authentication / Auth0

Configuration uses Pydantic settings, dotenv/environment inputs and a cached settings object. Server Management credentials are separate from the public SPA reset client ID. Startup validates configured authentication unless deliberately disabled; the reset route still depends on mandatory `get_current_user` regardless of that startup switch.

Authentication flow:

1. Extract explicit HTTP Bearer credentials. Missing credentials fail with 401 and a challenge; optional auth rejects supplied invalid credentials instead of treating them as anonymous.
2. Parse unverified header/claims solely to select a configured trust target. Multi-issuer mode binds issuer to its own audience/JWKS entry. Unverified token URLs do not select network destinations.
3. Use configured overrides or cached OIDC discovery and a per-JWKS-URL key cache. JWKS refresh has a lock, failure backoff, rotation cooldown, and a bounded stale-key policy. Cold/unusable cache failure returns 503, not an authenticated principal.
4. Verify signature with a hardcoded RS256 allowlist, expected issuer and audience. Expiration and not-before are checked when present with 60-second leeway. **Expiration presence is not required**; this is a remaining gap.
5. Derive subject, roles, permission/scope lists, email and strictly boolean email verification. Retain verified issuer in the principal. Missing subject is rejected, but whitespace handling has a gap.
6. Authorization helpers enforce roles/permissions and owner/admin rules. Their persisted owner identity is still subject-only; issuer retention has not propagated to those consumers.

Password reset performs no local user/database lookup and no email search. After authentication it checks configuration, consumes a reset-specific bucket, requires the configured tenant issuer and primary `auth0|<id>`, then requests a ticket with `user_id`, configured SPA `client_id`, `ttl_sec=600`, `mark_email_as_verified=false`, and `includeEmailInRedirect=false`. It accepts no caller-selected account or return URL. The token endpoint is `/oauth/token`; the resource endpoint is `/api/v2/tickets/password-change`. A cached M2M token avoids most token calls. Ticket POSTs are deliberately not retried after uncertain completion.

The returned URL must use HTTPS and exactly match the configured netloc; userinfo, explicit ports, nonempty fragments, whitespace, control characters and backslashes are rejected. A terminal empty `#` is accepted and tested. Ticket errors are generic, without logging the exception or ticket. Local quota exhaustion maps to 429; ticket upstream 429 maps to 503 with Retry-After; other provider failures/timeouts/malformed responses map to 502. Successful and handler-generated error responses include no-store/no-referrer, but dependency errors do not.

There is no server cookie session or password store on this boundary. CSRF tokens are not a missing requirement for the current explicit-Bearer operation; a future cookie-auth change would require reevaluation. CORS is centrally configured. Browser login uses Auth0 localstorage caching and refresh tokens; the global fetch interceptor currently broadens the token's exposure.

Plan-to-code assessment:

| Plan item | Assessment and evidence |
| --- | --- |
| B1 issuer retention | Implemented in principal and verified JWT return; reset uses it, adjacent profile/ownership consumers do not |
| B2 configuration/disable switch | Implemented in settings/example environment and reset preflight |
| B3 ticket helper | Implemented, exact payload/host policy and no-retry tests present; shared token refresh failure/latency handling remains weak |
| B4 route/quota/headers | Mostly implemented; all-response header promise is incomplete |
| B5 tests/validation | Substantial isolated coverage and OpenAPI inventory added; mounted real-JWT-to-ticket composition is not covered by the committed reset tests |
| F1–F4 frontend | Unimplemented **by design**, not an accidental backend omission; existing profile still initiates an email directly |
| Tenant grants, connections, login URI and hosted completion | Needs runtime verification; repository instructions are not tenant-state evidence |

### 4.2 `/projects/page`

Actual path: `GET /projects/{project_id}/page` → HTTP client dependency → `assemble_project_page` → `_assemble_project_page_inner` → `/projects/{project_id}/summary` → `parse_project` → concurrent `/files/{simulationRun.id}` and `/specifications/{simulationRun.id}` → `map_project_page` → `ProjectsPagePayload` → FastAPI response serialization.

The embedded run ID is required before satellite requests can start. The mapper derives model formats before projecting specification models away, selects the first metadata record, and exposes only owned fields. Missing satellite resources become empty lists. Identity 404 remains 404, upstream 5xx becomes sanitized 502, timeouts become 504, and malformed JSON/schema drift fail with 502. Other upstream 4xx statuses are preserved with generic messages.

**Representative success: zero local database operations, zero authentication/user lookups, three upstream GETs.** Identity failure causes one GET; invalid local path can cause zero. There are no counts, filters, sorting, pagination or ORM relationships. File/specification item counts increase CPU/memory and output size, not request count. Processing is broadly linear in parsed payload size; model-format extraction additionally traverses specification models.

The HTTP client is shared and closed at application shutdown. A 45-second `wait_for` bounds cooperative asynchronous assembly; it cannot preempt synchronous CPU work. Project failure cleanup is incomplete when gather raises from one satellite. This is the primary endpoint-specific remaining defect.

### 4.3 `/run/page`

Actual path: `GET /runs/{run_id}/page` → HTTP client dependency → `assemble_run_page` → `_assemble_run_page_inner` starts `/runs/{run_id}/summary`, `/files/{run_id}`, `/specifications/{run_id}`, `/logs/{run_id}` together → await/parse identity → gather satellite results → `map_run_page` → `RunsPagePayload` → FastAPI serialization.

**Representative success: zero local database operations, zero authentication/user lookups, four upstream GETs.** Even a missing run can initiate all four; invalid local IDs are rejected before outbound requests. The critical path is approximately the slowest request plus local processing. The 60-second budget and `finally: _cancel_and_drain(...)` cover identity failure/drift, satellite failure after identity, and cancellation. Satellite 404 means empty files/specifications or null logs; other errors retain the shared policy.

Awaiting identity first gives its failure precedence over a satellite that has already failed. This deliberately means an early satellite failure can remain pending while identity runs. Do not replace the structure with indiscriminate fail-fast TaskGroup semantics without deciding how identity 404/502/504 precedence should behave.

Plan-to-code assessment for both pages:

| Page-plan phase | Assessment |
| --- | --- |
| 0 baseline | Implemented as old-assembler replicas and timing/ordering tests; historical copies do not independently test the current project identity barrier |
| 1 run overlap | Implemented and event-tested; current local benchmark confirms the structural benefit |
| 2 parameter rename | Implemented as `resource_id`; the original `run_id` was also semantically correct, so no further rename needed |
| 3 total timeout | Implemented at 45/60 seconds; cooperative network/assembly budget, not a hard CPU or serialization ceiling |
| 4 structured cleanup | Manual cancel/drain appropriately used instead of optional TaskGroup; complete for run tasks, incomplete on project satellite failure |
| 5 regression tests | Implemented for run overlap, cancellation, timeouts, paths and isolation |
| 6 validation | Targeted current checks passed; live verification remains unrun |

Supporting database inspection found Motor/MongoDB services, not SQLAlchemy. `ProjectSearchServiceMongo.query_project_stubs` does a find plus count for `GET /projects`, with offset pagination and text/default sorting. `SimulationRunDatabaseServiceMongo.query_simulation_runs` does count plus find for `POST /simulations/runs`, with visibility/owner filtering. **Neither is called by the audited page routes.** Their existing indexes cannot reduce these page requests' cost. No new index, keyset pagination, count elimination, selectinload/joinedload, or local query-count optimization is justified for these page routes. Upstream database operations are outside this repository and cannot be counted statically here.

The current frontend project/run detail pages still issue their own legacy API fetches (`frontend/app/pages/projects/[id].vue` and `frontend/app/pages/runs/[id].vue`). Therefore the measured backend aggregation improvement does not establish a user-visible speedup for those screens. Frontend page migration was explicitly outside the original page plan; it is a separate integration decision, not an unfulfilled backend acceptance criterion.

## 5. Major Improvements

### 5.1 Authentication

#### AUTH-MAJ-001 — Preserve issuer identity through account and ownership authorization

- **Classification:** Major. **Priority:** P1. **Area:** Authentication / authorization / Auth0 account safety. **Type:** Confirmed boundary defect with configuration-dependent exploitability.
- **Evidence:** `users/router.py::_build_profile` (line 34), `update_me` (72), `delete_me` (106) pass `user.sub` to the single configured Management tenant without checking `user.issuer`. Reset checks both at line 148 onward. `common/auth/roles.py::is_owner` compares only `owner_sub`; `creation_policy` persists `user.sub`; `simulations/database.py::_visible_to_caller` filters on subject. Multi-issuer validation is explicitly supported. The mounted signed-token probe returned 204 and invoked the mocked delete helper for a foreign trusted issuer.
- **Current Behavior:** An authenticated subject from any trusted issuer can select the same-named account in the configured Auth0 tenant and collide with subject-only local ownership.
- **Issue / Opportunity:** Signature validity within one trust domain does not establish identity in another. Account deletion makes this materially more serious than a display inconsistency. No deployed overlapping subject or multi-issuer configuration was verified.
- **Recommended Improvement:** Apply a shared configured-tenant issuer guard before all Management reads/writes; foreign principals may retain JWT-only profile display if that is intentional, but must not enrich from or mutate the configured tenant. For locally owned resources, use an issuer-plus-subject identity or explicitly restrict resource ownership operations to one issuer until a migration is ready.
- **Purpose:** Prevent cross-tenant account targeting and ownership collisions.
- **Expected Impact:** Closes a data exposure/destructive-account boundary; no latency gain is claimed.
- **Implementation Notes:** Reuse the reset issuer rule without indiscriminately applying its database-provider restriction to all profile users. Define legacy `owner_sub` backfill/issuer association explicitly; never assign old records to whichever issuer first presents a matching subject. Audit OMEX/workflow ownership consumers when changing the identity representation. Restricting additional issuers is an interim policy choice, not silently disabling signature checks.
- **Validation:** Mounted tests with two issuers, different RSA keys and the same sub: foreign token must cause zero configured-tenant Management calls for GET/PATCH/DELETE, while permitted local/social profiles keep intended behavior. Add cross-issuer owner read/write/list denial and legacy migration cases. Keep issuer/audience and wrong-key tests.
- **Plan Relationship:** New finding discovered during audit; builds on B1 issuer retention.

#### AUTH-MAJ-002 — Require token expiration and validate subjects without identity rewriting

- **Classification:** Major. **Priority:** P1. **Area:** JWT validation. **Type:** Confirmed validation defects.
- **Evidence:** `common/auth/auth0.py::get_current_user`, lines 668–675, passes only leeway in decode options; installed python-jose `_validate_exp` returns immediately when `exp` is absent. Principal config at line 575 strips whitespace; the preconstruction sub check only rejects nonstrings/empty strings. Local signed-token probes reached reset with no expiry (200), produced 500 for whitespace-only sub, and changed a padded sub to an unpadded identity.
- **Current Behavior:** Expired tokens are rejected, but signed tokens lacking expiry are accepted. Some malformed subjects escape the HTTP authentication error path or are normalized.
- **Issue / Opportunity:** Accepted credentials can lack a bounded lifetime; identity keys are altered after verification, and malformed claims can generate server errors plus a premature authentication-success event.
- **Recommended Improvement:** Require `exp` in the access-token contract with validated NumericDate semantics; retain issuer/audience/signature and leeway checks. Validate subject before principal construction, reject whitespace-only/padded invalid values according to an explicit subject policy, and preserve accepted subject strings exactly. Map claim-construction failures to a sanitized 401 and emit success only after construction succeeds.
- **Purpose:** Bound credential validity and make identity validation fail closed consistently.
- **Expected Impact:** Rejects incorrectly issued tokens and prevents claim-driven 500s/identity normalization collisions. This does not imply an attacker can modify an otherwise valid signed token.
- **Implementation Notes:** Keep string cleanup for display fields separate from identity identifiers. Add required-exp options or equivalent explicit validation; avoid imposing optional claims unrelated to authorization without a provider contract.
- **Validation:** Sign tokens locally with missing/null/malformed expiry, expired/current expiry at skew boundaries, and empty/whitespace/padded/nonstring subjects. Exercise mounted reset and a regular protected route; expect 401, no ticket/Management call, no raw claims in logs, and no success event on rejection.
- **Plan Relationship:** New finding discovered during audit.

#### AUTH-MAJ-003 — Restrict browser bearer attachment to the intended API origin

- **Classification:** Major. **Priority:** P1. **Area:** Credential/token safety. **Type:** Confirmed missing destination boundary.
- **Evidence:** `frontend/app/plugins/auth0.client.ts` configures global `$fetch.onRequest` without inspecting `request`, sets credentials to include, and attaches an audience-bound access token for any destination. `composables/useVisualizations.ts:52` fetches `file.url`; line 100 fetches legacy results. `pages/profile.vue::sendPasswordResetLink` uses that client for an Auth0 Authentication API URL.
- **Current Behavior:** Logged-in browser fetches to legacy/provider/file origins can inherit the platform bearer token.
- **Issue / Opportunity:** A token is offered beyond its resource-server boundary. Actual transmission depends on CORS/preflight and the destination; an accepting untrusted destination could receive it. No malicious production URL or successful exfiltration was observed.
- **Recommended Improvement:** Introduce an API-scoped authenticated client or resolve the final request URL against baseURL/origin and enforce an exact trusted API origin/path policy before adding credentials. Use an explicitly unauthenticated client for file, visualization, legacy-public and Auth0 recovery requests. Define failure behavior for protected requests when token acquisition fails.
- **Purpose:** Prevent accidental bearer disclosure and authenticated-to-anonymous downgrade on operations requiring identity.
- **Expected Impact:** Reduces token exposure and unnecessary token acquisition on public/external fetches.
- **Implementation Notes:** Resolve relative/protocol-relative URLs and Request objects correctly; do not use hostname substring matching. Review callers that explicitly set Authorization as well as automatically added headers. Hosted ticket consumption must remain top-level navigation, not fetch.
- **Validation:** Interceptor tests for configured API, same-host lookalike, alternate port, protocol-relative URL, relative URL with external baseURL, file metadata URL, Auth0 reset endpoint, and token-acquisition failure. Browser test must prove no application Authorization header reaches external mock servers.
- **Plan Relationship:** Completes an existing plan item: the explicitly deferred origin-restriction follow-up in F3.

#### AUTH-MAJ-004 — Require stronger authorization for direct hosted password changes

- **Classification:** Major. **Priority:** P1 before enabling the new UI. **Area:** Password-reset security. **Type:** Security enhancement to an explicitly accepted design tradeoff.
- **Evidence:** `reset_my_password` accepts any otherwise valid eligible application access token; `AuthenticatedUser` retains no recent-authentication assurance. `auth0.client.ts` caches tokens in localstorage. Reset plan section 12 explicitly identifies token possession replacing inbox possession and defers recent authentication/MFA.
- **Current Behavior:** Possession of an eligible access token can mint a password-changing capability, without proving recent interaction or inbox access.
- **Issue / Opportunity:** A stolen ordinary API token can gain a more durable account-changing capability. The 600-second ticket TTL limits ticket exposure, but does not prove the caller recently authenticated.
- **Recommended Improvement:** Design and enforce a server-verifiable step-up/recent-authentication policy for ticket issuance, or retain emailed recovery if that assurance cannot be established. Bind any short-lived action authorization to the verified issuer/sub and password-change purpose.
- **Purpose:** Reduce the escalation from session/token theft to password takeover.
- **Expected Impact:** A deliberate security improvement with an extra user interaction; not a fix for a signature-verification bypass.
- **Implementation Notes:** Do not equate a freshly refreshed access token or `iat` with fresh interactive authentication. Define supported signed assurance claims or a backend action grant with the tenant, and reject missing/stale evidence. Do not trust a boolean supplied by the SPA. Auth0 makes the application responsible for identity verification in a custom ticket flow ([ticket contract](https://auth0.com/docs/api/management/v2/tickets/post-password-change)).
- **Validation:** Mock tests for ordinary token, fresh verified assurance, missing/stale/wrong-user assurance and replay where applicable. Separately verify the chosen assurance claims and interaction in an authorized development tenant. No automatic test should send real recovery emails or consume live tickets.
- **Plan Relationship:** Improves an implemented plan item, following the risk acknowledged in section 12.

#### AUTH-MAJ-005 — Bound Management-token refresh failures and complete operation latency

- **Classification:** Major. **Priority:** P1. **Area:** Auth0 reliability / async behavior. **Type:** Confirmed failure amplification and missing operation deadline.
- **Evidence:** `common/auth/auth0_management.py::_get_management_token` (line 43) serializes refresh with a lock but stores only successful outcomes; each waiter retries after failure. `create_password_change_ticket` (235) has a 10-second HTTP phase timeout after token acquisition but no whole-operation/lock-wait budget. `_send_with_retry` (123) checks a deadline before sleeping, not around the active request. Five concurrent failed refresh probes made five token POSTs.
- **Current Behavior:** Successful refreshes are shared; failed cold refreshes form a serialized retry queue. Per-request HTTP timeouts do not include lock waiting or guarantee total elapsed time. Profile retry attempts can run beyond the nominal deadline.
- **Issue / Opportunity:** During Auth0 failure, queued requests consume capacity and issue repeated token requests. Ticket issuance and adjacent profile operations have less predictable tail latency than their comments suggest.
- **Recommended Improvement:** Share a failed refresh result for a short bounded cooldown, honor bounded token-endpoint throttling, and apply a monotonic operation deadline covering lock wait, token acquisition and resource request. Bound profile retry attempts by remaining deadline. Preserve single-attempt ticket issuance.
- **Purpose:** Improve failure isolation and bound interactive request lifetime and provider load.
- **Expected Impact:** Fewer outbound token requests during outages and more predictable tail latency; no production percentage is claimed.
- **Implementation Notes:** Do not cache invalid credentials indefinitely. Validate token response fields before atomically replacing the cache. Keep cache bound to configured tenant/client identity if configuration reload is supported. Cancellation must release locks; timeout after ticket submission must not automatically trigger a second ticket POST. Preserve the documented distinction between ticket 429 and token-acquisition failures or update it deliberately.
- **Validation:** Concurrent cold success and failure tests; assert one refresh per recovery/backoff window, recovery after expiry, cancellation while waiting, malformed token response leaving cache unchanged, token 429, and end-to-end deadline including a slow body stream. Verify exactly zero or one ticket POST, never an uncertain retry. Test slow profile attempts against remaining retry budget.
- **Plan Relationship:** Improves an implemented plan item through B3's reused Management client.

#### AUTH-MAJ-006 — Complete the deferred reset UI and signed-token integration gate

- **Classification:** Major. **Priority:** P2, after AUTH-MAJ-003/004. **Area:** Password-reset integration / testing. **Type:** Planned feature completion and high-risk regression protection.
- **Evidence:** Reset plan F1–F4 remains marked future. `profile.vue::sendPasswordResetLink` still calls `/dbconnections/change_password`, displays email-sent copy and logs raw errors. `login.vue::submitForgotPassword` is a separate public email flow. Committed reset route tests override `get_current_user`; helper tests mock token/HTTP separately.
- **Current Behavior:** The new backend ticket endpoint is not used by the UI. Existing tests validate pieces but not the mounted signed-JWT → issuer-bound route → token grant → ticket HTTP contract as one flow.
- **Issue / Opportunity:** Backend completion is not user-flow completion. Future integration can misstate success, attach tokens to ticket navigation, mishandle session recovery, or weaken account selection despite green isolated tests.
- **Recommended Improvement:** Implement the already-planned authenticated profile action with correct copy, duplicate-click prevention, explicit API token acquisition, generic errors and direct browser navigation. Keep anonymous recovery separate. Add a mounted composition regression using local RSA/JWKS and mocked Management transport; add the frontend harness needed for navigation/error/session cases.
- **Purpose:** Deliver the intended experience and protect the complete authentication-to-ticket boundary.
- **Expected Impact:** Users reach hosted password change through the platform policy; tests catch integration defects that router overrides cannot detect.
- **Implementation Notes:** This was intentionally out of scope for the original backend change and is not evidence that its author missed an acceptance criterion. Ticket issuance is not proof of password change. Do not persist/log the URL or assume automatic sign-in after completion. Use the configured login URI and tested session recovery. Tenant setup remains a separate release gate.
- **Validation:** Signed valid/expired/wrong-issuer/unsupported-provider tests through the mounted route, asserting exact downstream subject and no attacker body/query forwarding; frontend 401/403/429/502/503, loading, token failure and direct-navigation tests. Authorized development-tenant checks for completion, expiry/reuse, email-verification preservation, return route and login with the new password.
- **Plan Relationship:** Completes existing plan items F1–F4 and the manual rollout acceptance gate.

### 5.2 `/projects/page`

#### PROJECTS-MAJ-001 — Cancel and drain project satellites on partial failure

- **Classification:** Major. **Priority:** P1. **Area:** `/projects/page` reliability / upstream resource use. **Type:** Locally reproduced defect.
- **Evidence:** `pages/service.py::_assemble_project_page_inner`, lines 50–63, awaits bare coroutines with `asyncio.gather` and has no task cleanup. The run equivalent uses `_cancel_and_drain` in finally. `test_project_page_total_timeout` covers cancellation of a pending gather, but no held-open sibling test covers one satellite failing first.
- **Current Behavior:** One satellite can raise 502/504 while the sibling continues running after the page request has failed. The outer `wait_for` has already finished and no longer bounds that orphaned work.
- **Issue / Opportunity:** Upstream connections and application work outlive the failed response; concurrent upstream failures can amplify pool pressure.
- **Recommended Improvement:** Retain explicit project satellite tasks and cancel/drain them on every early exit, mirroring the run implementation, or use equivalent structured cleanup preserving HTTPException status semantics.
- **Purpose:** Release request-owned work promptly and keep the page budget meaningful on failure paths.
- **Expected Impact:** Less wasted upstream work and better capacity under partial outages; successful response latency should remain unchanged.
- **Implementation Notes:** Keep the identity barrier and embedded run ID. Keep satellite 404 fallback. Cancellation cannot undo work already accepted upstream. Do not introduce an ExceptionGroup that changes 404/502/504 into 500.
- **Validation:** Parameterize files/specifications failure with the other request held open; assert 502/504, sibling cancellation/drain before returning, and no pending task. Retain success, embedded-ID, timeout and 404 tests; add caller-cancellation cleanup.
- **Plan Relationship:** Improves an implemented plan item: page timeout/cancellation work.

### 5.3 Shared / Cross-Cutting

#### SHARED-MAJ-001 — Bound upstream page payloads and verify CPU/memory limits

- **Classification:** Major. **Priority:** P2. **Area:** Both page endpoints / reliability / scalability. **Type:** Confirmed absence of size bounds; production impact needs runtime verification.
- **Evidence:** `common/upstream.py::fetch_upstream_json_value` buffers `client.get` fully then calls synchronous `response.json`. `pages/mapping.py` validates all files/specifications/logs, dumps intermediate models and constructs public models; `pages/models.py` has unbounded lists and log-output strings. `wait_for` surrounds assembly, while FastAPI serialization follows outside it.
- **Current Behavior:** A fixed three/four requests can return arbitrarily large bodies. Parsed dictionaries, private models, intermediate dumps and public models coexist. CPU-heavy parsing/mapping does not yield to the event loop; response serialization is outside the page budget.
- **Issue / Opportunity:** A deadline alone does not bound buffered bytes, memory or synchronous CPU time. Large logs/specifications are the concrete scaling dimension, even though current small fixtures pass quickly. No production exhaustion or unacceptable mapping time was measured.
- **Recommended Improvement:** Establish supported payload budgets from representative archives, then enforce decoded-byte limits while streaming upstream bodies and define a sanitized oversize error. Measure parsing/mapping/serialization and event-loop lag at those bounds; introduce bounded concurrency or offloading only if measurements show it is needed.
- **Purpose:** Bound endpoint resource use and prevent one oversized page from monopolizing a worker.
- **Expected Impact:** Predictable memory and failure behavior; potentially better tail latency under large payloads. Normal-size speedup is not claimed.
- **Implementation Notes:** Limit decompressed bytes, not only Content-Length; handle absent/incorrect lengths and close streams on rejection. Do not silently truncate files/logs or add pagination to existing owned contracts. Resource splitting, optional sections or offloading require explicit contract/performance decisions. Preserve private/public validation boundaries.
- **Validation:** Local AsyncByteStream tests for chunked, compressed, no-length, over-limit and just-under-limit payloads; verify cleanup and generic error. Profile small/large fixtures through the mounted route, record peak memory and event-loop lag, and run simultaneous small requests to detect starvation. Keep 3/4 call-count and response-contract assertions.
- **Plan Relationship:** New finding discovered during audit, extending the plan's unverified small-payload assumption.

## 6. Minor Improvements

### 6.1 Authentication

#### AUTH-MIN-001 — Apply reset privacy headers to dependency failures

- **Classification:** Minor. **Priority:** P2. **Area:** Password reset / error consistency. **Type:** Confirmed plan-contract gap.
- **Evidence:** `users/router.py::_NO_STORE` is applied inside `reset_my_password`; `get_current_user` executes first. `api/main.py` has no reset-specific response wrapper. `test_authentication_required` asserts only status. Local anonymous probe returned 401 without either header.
- **Current Behavior:** Success and handler errors get privacy headers; authentication 401/JWKS 503 do not.
- **Issue / Opportunity:** The plan and helper comment promise all responses. These dependency errors contain no ticket, so this is not a demonstrated ticket-cache leak.
- **Recommended Improvement:** Apply route-scoped response header handling that includes dependency/exception responses and retains existing authentication/Retry-After headers.
- **Purpose:** Fulfill the documented per-principal response contract consistently.
- **Expected Impact:** Predictable cache/referrer policy on all reset responses.
- **Implementation Notes:** Use a route wrapper or narrowly scoped middleware; avoid globally replacing exception handlers solely for this endpoint. Document treatment of router-level method errors separately.
- **Validation:** Assert both headers on success, missing/malformed/expired token, unavailable JWKS, unsupported issuer, configuration failure, quota failure and provider failure; preserve WWW-Authenticate and Retry-After.
- **Plan Relationship:** Corrects an implementation of B4.

#### AUTH-MIN-002 — Separate reset quota policy from workflow limits and issuer collisions

- **Classification:** Minor. **Priority:** P2. **Area:** Abuse resistance / configuration.
- **Evidence:** `common/ratelimit.py::password_reset_rate_limit` has a separate prefix but reuses workflow settings; `client_identity` keys authenticated users by sub only. `reset_my_password` charges quota before issuer eligibility. Defaults are 30 authenticated requests per 60-second process window.
- **Current Behavior:** Reset traffic cannot consume the workflow bucket, but its ceiling/kill switch is coupled to workflow policy. Same-sub foreign trusted users can consume the same reset key before denial.
- **Issue / Opportunity:** Operators cannot tune a sensitive account action independently; issuer collisions cause avoidable quota interference. Per-process and fixed-window behavior is already documented and is not described here as a new bypass.
- **Recommended Improvement:** Give reset a dedicated configurable ceiling/window and namespace its authenticated key by issuer plus sub. Preserve pre-eligibility charging and record the intended effective multi-worker/replica budget.
- **Purpose:** Tune abuse resistance without affecting workflow throughput and isolate principals correctly.
- **Expected Impact:** Clearer operations and fewer false quota collisions. A strict global cap would still need ingress/shared enforcement if required.
- **Implementation Notes:** Validate positive windows/nonnegative policy values; do not add a new datastore solely for this improvement without a hard global-limit requirement.
- **Validation:** Reset/workflow bucket isolation, issuer collision, boundary rollover, disabled policy, concurrent increments and configuration-validation tests. Verify actual deployment multiplier separately.
- **Plan Relationship:** Improves B4's implemented isolated limiter.

#### AUTH-MIN-003 — Document reset errors and sensitive response handling in OpenAPI

- **Classification:** Minor. **Priority:** P2. **Area:** API contract / maintainability.
- **Evidence:** The reset route decorator declares a response model but no error responses or sensitive-response/header description; generated `/api/v1/me/password-reset` documents only 200 and bearer security. The in-memory generated artifact matches current code.
- **Current Behavior:** Consumers need prose outside OpenAPI to discover eligibility failures, retry behavior and ticket handling.
- **Issue / Opportunity:** Missing error/header documentation encourages incorrect UI retry and success behavior.
- **Recommended Improvement:** Describe 401/403/429/502/503, Retry-After, no-store/no-referrer, no request-body selection, single-attempt semantics, and direct navigation of the sensitive URL. Keep example URLs synthetic.
- **Purpose:** Make the operation usable correctly by generated/documentation-driven clients.
- **Expected Impact:** Better integration consistency; no runtime performance change.
- **Implementation Notes:** Regenerate later with `scripts/generate_openapi.py`; retain its deterministic demo-router setting. Do not misdocument generic 502 as proof of account nonexistence.
- **Validation:** OpenAPI assertions for status/header descriptions and in-memory/committed artifact parity; existing operation/auth inventory must pass.
- **Plan Relationship:** Improves B5 and the additional generated-contract work.

#### AUTH-MIN-004 — Reuse a dedicated Auth0 HTTP transport

- **Classification:** Minor. **Priority:** P2. **Area:** Auth0 client efficiency / lifecycle.
- **Evidence:** `_get_management_token`, `get_auth0_user`, `update_auth0_user`, `delete_auth0_user`, and `create_password_change_ticket` each create/close an AsyncClient; the page upstream client is already pooled.
- **Current Behavior:** M2M tokens are cached, but sequential Management requests do not retain a transport pool across calls.
- **Issue / Opportunity:** Repeated connection establishment is avoidable; real network contribution is unmeasured.
- **Recommended Improvement:** Inject a lifecycle-owned Auth0 client, separate from the public page upstream client, and reuse it with per-request credentials and explicit timeout policy.
- **Purpose:** Reduce unnecessary connection setup and centralize safe testing/cleanup.
- **Expected Impact:** Potential reduction in external-service latency overhead and simpler transport injection; no measured speedup claimed.
- **Implementation Notes:** Keep tenant/token headers scoped to each request, preserve no ticket retry, and close the client on shutdown. Do not introduce client defaults that forward credentials to page resources or redirects.
- **Validation:** Lifecycle close/reuse tests, isolated transport contracts, credential-destination assertions and concurrent refresh tests. A local real-HTTP server is needed to measure socket reuse; MockTransport alone cannot prove it.
- **Plan Relationship:** Improves B3's reused Management client.

### 6.2 `/run/page`

#### RUN-MIN-001 — Pin mixed-failure precedence and caller cancellation

- **Classification:** Minor. **Priority:** P2. **Area:** `/run/page` regression completeness.
- **Evidence:** `_assemble_run_page_inner` awaits identity before consuming satellite errors. `test_run_page_satellite_failure_cancels_siblings` returns identity immediately; existing failure tests vary one resource at a time. Total-timeout tests cover timeout-driven cancellation but not explicit caller task cancellation.
- **Current Behavior:** An early satellite failure waits for identity; identity errors win. Finally cancels/drains tasks.
- **Issue / Opportunity:** A future concurrency refactor can change 404/502/504 precedence or swallow caller cancellation without breaking the existing single-failure assertions.
- **Recommended Improvement:** Document and test identity precedence with satellites failing first, plus explicit caller cancellation. Retain the current semantics unless a separate latency-versus-precedence decision authorizes a change.
- **Purpose:** Protect externally visible error behavior during future concurrency maintenance.
- **Expected Impact:** Regression protection rather than an asserted latency improvement.
- **Implementation Notes:** Use event barriers and task completion assertions, not narrow wall-clock thresholds; ensure no unhandled task-exception warnings.
- **Validation:** Early files 500 followed by identity 404, identity timeout, and identity success; then explicit cancellation with identity/satellites held open. Assert selected status or propagated CancelledError and that all child tasks are drained.
- **Plan Relationship:** Improves page-plan phases 4–6.

### 6.3 Shared / Cross-Cutting

#### SHARED-MIN-001 — Instrument actual page phases and reset outcomes

- **Classification:** Minor. **Priority:** P2. **Area:** Observability / performance verification.
- **Evidence:** `pages/service.py` and `common/upstream.py` log failures but no phase durations/bytes; `reset_my_password` emits one generic failure warning. `log_config.py::JsonFormatter` retains only three explicit auth extra fields, so arbitrary new logging extras would be dropped. `scripts/bench_pages.py` measures end-to-end latency, not where it is spent.
- **Current Behavior:** Logs can show failure but do not distinguish page pool/network/body/mapping/serialization costs or token-acquisition versus ticket failure.
- **Issue / Opportunity:** Remaining optimizations and operational diagnosis cannot be prioritized reliably from current evidence.
- **Recommended Improvement:** Add bounded route/resource/outcome metrics or explicitly supported structured fields for phase durations, decoded bytes, deadline and cancellation outcomes, and token-versus-ticket failures. Extend the local benchmark workflow to report payload sizes and mixed failure/size workloads.
- **Purpose:** Identify real bottlenecks and verify reliability improvements without logging secrets.
- **Expected Impact:** Better diagnosis and defensible performance decisions; no direct speedup asserted.
- **Implementation Notes:** No tickets, body content, email, raw sub, dynamic resource IDs or Authorization labels. Keep metrics cardinality bounded; adapt the formatter if using extras. Measure final ASGI response duration separately from assembly. Do not log ticket URLs through HTTP/APM instrumentation.
- **Validation:** Capture logs/metrics for success, 404 fallback, 502, 504, cancellation, token failure and ticket throttling; assert bounded labels and absence of synthetic secret markers. Compare mocked and real-transport measurements separately.
- **Plan Relationship:** Improves page performance verification and reset failure handling.

#### SHARED-MIN-002 — Reuse immutable Pydantic collection adapters

- **Classification:** Minor. **Priority:** P3. **Area:** Response construction / small CPU efficiency.
- **Evidence:** `pages/mapping.py::normalize_specifications` line 217 and `map_files` line 224 construct the same `TypeAdapter` on every request.
- **Current Behavior:** Per-request collection validation also repeats adapter construction; private/public projections remain intentionally distinct.
- **Issue / Opportunity:** Small reusable schema setup work can be removed without weakening validation. Its share of endpoint time has not been measured.
- **Recommended Improvement:** Define reusable module-level adapters for the two stable list types; retain current validation/projection and public response models.
- **Purpose:** Avoid repeated schema setup with minimal complexity.
- **Expected Impact:** Small local CPU reduction at most; not a remedy for upstream latency or large-body costs.
- **Implementation Notes:** Do not use model_construct, raw passthrough responses or remove validation to chase a microbenchmark. No specification-normalization consolidation is needed.
- **Validation:** Existing mapping/drift/closed-contract tests plus a local mapping benchmark with equivalent outputs. Defer the cleanup if measured value is negligible relative to higher priorities.
- **Plan Relationship:** New finding discovered during audit.

## 7. Test and Regression Coverage Improvements

This section maps tests to the recommendations above; it does not add unnumbered recommendations.

| Boundary | Already covered | Remaining targeted coverage |
| --- | --- | --- |
| JWT | RS256, none/HS256 rejection, bad issuer/audience/key, expired and skew cases, missing/empty/nonstring sub, email verification, roles/scopes | Required expiry, whitespace identity policy and no-success-event-on-failure (AUTH-MAJ-002) |
| Tenant/owner | Issuer/audience pairing and JWKS-key isolation; owner/admin and verified legacy email rules | Same sub across issuers at Management routes and persisted ownership (AUTH-MAJ-001) |
| Reset | Router eligibility/config/quota/injection/generic errors; exact token/ticket payload; URL validation; no retry on ticket failure | Real signed-token mounted composition, fresh-action assurance, dependency privacy headers (AUTH-MAJ-002/004/006, AUTH-MIN-001) |
| Management client | Successful token cache; profile retry status/log behavior | Concurrent failed refresh sharing, whole deadline, cancellation, malformed cache update and token-endpoint throttling (AUTH-MAJ-005) |
| Browser | No configured dedicated UI unit/E2E suite found for this flow | Origin-scoped headers, navigation/no persistence, token failure, loading/error/session behavior (AUTH-MAJ-003/006) |
| Project page | Three-call contract, parallel satellites, embedded run ID encoding, 404/errors, total timeout | Failed satellite with sibling held open, current-implementation identity barrier and caller cancellation (PROJECTS-MAJ-001) |
| Run page | Four-call contract, overlap, failure cleanup, 404/errors, timeout and latency guard | Mixed failures and caller cancellation (RUN-MIN-001) |
| Payload/serialization | Nested projection, generator unions, nullable logs, required-field drift, unknown-field omission | Large/streaming/compressed payload bounds, memory/loop-lag and final serialization measurements (SHARED-MAJ-001) |
| API contract | Exhaustive operation/auth inventory and generated artifact parity | Reset errors and response headers (AUTH-MIN-003) |

Tests must retain the distinction between application behavior using dependency overrides, client behavior using MockTransport, mounted local JWT verification, and actual provider behavior. A mock can prove requested TTL and flags; it cannot prove Auth0 ticket expiry, one-time use, grants or post-reset session behavior. Do not send real password-reset requests from the automated suite.

Database query-count tests are unnecessary for these detail routes. Their regression metric is a fixed **three/four outbound requests independent of nested item count**, plus zero database dependency use. Pagination boundaries belong to nearby list-route tests, not fabricated parameters on these page routes.

## 8. Performance Verification Recommendations

Current controlled benchmark: direct assembler calls, local HTTPX MockTransport, existing fixtures, 50 ms artificial delay per upstream request, five samples per case, no real sockets, no FastAPI response serialization. The “before” functions are the committed baseline replicas in `tests/pages/test_phase0_baseline.py`.

| Case | Minimum ms | Median ms | Maximum ms |
| --- | ---: | ---: | ---: |
| Run before | 103.3 | 103.9 | 110.2 |
| Run current | 53.6 | 54.5 | 57.0 |
| Project before | 103.9 | 104.6 | 107.9 |
| Project current | 103.3 | 105.0 | 105.4 |

These numbers support overlapping run requests and preserving the project dependency order. They do not predict production latency or socket-pool behavior. The old plan's separate 100-ms/ten-sample numbers are historical, not rerun results from this audit.

Under SHARED-MAJ-001 and SHARED-MIN-001, measure:

1. **Outbound work:** three/four successful calls, queue/connection/network duration, decoded bytes, request cleanup on error. The project bottleneck remains necessary serial identity plus parallel satellites; the run bottleneck is the slowest branch.
2. **CPU/serialization:** separate JSON decode, private validation, projection, public construction and full mounted-response serialization. Include many files, many specification outputs/models, and large nested logs. Model dumps are real copies, but private/public validation is not automatically redundant; FastAPI's handling of already-created model instances should be measured rather than assumed to revalidate every leaf.
3. **Memory/concurrency:** peak memory, event-loop lag, small-request latency alongside a large request, canceled connections and pool timeout behavior. MockTransport does not exercise DNS/TLS/httpcore pooling; use a controllable local HTTP server for these checks.
4. **Failure load:** concurrent identity/satellite failures and M2M outages with token cache cold/warm. Verify cancellation and bounded refresh attempts before increasing connection limits.

`scripts/bench_pages.py` was **not executed**: its default workflow fetches a baseline, creates a worktree, starts APIs with Mongo/Temporal initialization, and calls the public upstream. That is incompatible with this audit's no-production-calls/no-database-mutation boundary. A future authorized run should use safe preconfigured local/staging targets and pinned baseline refs, with a representative request mix and recorded configuration.

No cache is currently recommended. There are no duplicate requests within a page to memoize; files, status, specifications and logs have different freshness and absence semantics. Reconsider caching/coalescing only after repeated-ID traffic is measured and invalidation/error rules are agreed. No new retries, HTTP/2 toggle, raw response serializer, index or pool-size increase is justified by this audit's measurements.

## 9. Security Verification Recommendations

Execute these as validation of AUTH-MAJ-001 through 006 and AUTH-MIN-001 through 003:

- **Identity:** demonstrate issuer-bound account selection and ownership using distinct local signing keys, required expiry, exact subject preservation and audience isolation. Unknown/unavailable JWKS must never become an authenticated principal.
- **Sensitive action:** verify step-up evidence at the backend and keep reset identity derived from the authenticated principal. Requests containing email/user_id/client_id/redirect fields must not influence the Management payload.
- **Credential destinations:** test the browser interceptor against accepting external mock origins; verify no platform bearer or ambient credentials are added to ticket/file/provider requests. Check final navigation and analytics instrumentation for ticket handling.
- **Provider contract:** assert one ticket POST, configured SPA association, 600-second TTL, false email-verification/redirect-email flags, exact configured host, and sanitized failure responses. Preserve empty-terminal-fragment compatibility.
- **Abuse/error handling:** verify quota before eligibility, issuer-scoped budgets, dependency-level privacy headers, challenge/retry headers and no raw provider body/URL in logs.
- **Tenant gate:** in a separately authorized development tenant, verify required M2M grants, intended database connection/application eligibility, New Universal Login/default login URI, ticket expiry/reuse, email-verification preservation and post-reset login/session behavior. Do not broaden the SPA to Management scopes.

The public page endpoints deliberately omit authentication and do not forward caller Authorization/Cookie headers. Their upstream-only public data boundary must be verified operationally; no local ownership bypass is asserted solely because these routes are public. A local `private` simulation record and an upstream run ID are distinct concepts. Verify that upstream resources reachable through public summaries/satellites are intended to be public before changing ACL or caching behavior.

## 10. Prioritized Implementation Order

| Phase | IDs | Why grouped / dependencies | Expected outcome and gate before moving on |
| --- | --- | --- | --- |
| 1. Identity and token exposure | AUTH-MAJ-001, AUTH-MAJ-002, AUTH-MAJ-003 | Highest-impact account/token boundaries; issuer migration policy precedes persisted identity changes | Signed cross-issuer and malformed-claim regressions pass; foreign Management calls blocked; browser origin matrix passes |
| 2. Failure isolation | AUTH-MAJ-005, PROJECTS-MAJ-001 | Independent backend reliability fixes; both need deterministic cancellation/failure tests | Failed refreshes bounded, deadlines include waiting, project siblings drained; page contracts/call counts unchanged |
| 3. Sensitive-action policy and integration | AUTH-MAJ-004, AUTH-MAJ-006, AUTH-MIN-001, AUTH-MIN-002, AUTH-MIN-003 | Agree step-up first; UI depends on safe authenticated transport and error/header contract | Mounted signed-token-to-ticket tests and UI tests pass; development-tenant gate completed before enabling UI |
| 4. Measurement and capacity bounds | SHARED-MIN-001, SHARED-MAJ-001, RUN-MIN-001 | Instrumentation identifies safe payload budgets; mixed-error tests preserve semantics during changes | Representative local real-transport workload records latency/bytes/memory/loop lag; oversize/cleanup contracts tested |
| 5. Small efficiency work | AUTH-MIN-004, SHARED-MIN-002 | Builds on stable lifecycle/deadline and measured local cost | Client reuse/cleanup and mapping equality pass; retain only demonstrated or clearly low-complexity gains |

No P0 recommendation is assigned because current deployment exposure and exploitation prerequisites were not established. If deployment verification confirms overlapping trusted subjects with Management mutation enabled, or bearer delivery to an untrusted accepting origin, treat the corresponding P1 boundary as an immediate incident/remediation priority.

## 11. Suggested Validation Checklist

Completed for this audit:

- [x] Established HEAD/branch and inspected staged, unstaged and untracked state.
- [x] Read both plans fully and compared their intent with committed changes.
- [x] Traced authentication and both actual page routes, including supporting database distinction.
- [x] Inspected relevant tests and established safe repository commands.
- [x] Ran targeted tests, read-only lint/type checking and in-memory OpenAPI comparison.
- [x] Reproduced key defects locally without live identity/provider/database operations.
- [x] Recorded benchmark scope and runtime limitations.
- [x] Created only the requested audit document; original plans and application files unchanged.

For implementation of this plan:

- [ ] Complete the ID-specific validation listed in sections 5–7.
- [ ] Preserve RS256, issuer/audience, fail-closed optional auth, strict email verification and no caller-identity selection.
- [ ] Preserve public page shapes, aliases, three/four calls, encoded-ID protection and satellite 404/error semantics.
- [ ] Run established Ruff/mypy and relevant backend regression suites; run broader infrastructure suites only in an isolated authorized environment.
- [ ] Regenerate and compare OpenAPI after actual API changes; run frontend checks plus newly added boundary tests after frontend work.
- [ ] Complete the authorized development-tenant and representative transport/load gates; record any skipped gate explicitly.
- [ ] Inspect final diff and `git diff --check`; distinguish committed/pushed/deployed status from validation status.

## 12. Items Requiring Runtime Verification

| Item | Static observation / unknown | Safe verification needed |
| --- | --- | --- |
| Active issuer trust/exposure | Multiple issuers supported; profile/owner consumers use sub alone. Deployed mappings/subject collisions not inspected | Review nonsecret deployment trust policy and isolated same-sub test tenants; do not dump tokens/secrets |
| Actual reset availability | Blank client ID disables route; credentials/grants are environment-owned | Verify development configuration and exact M2M grant without logging secrets |
| Hosted reset behavior | Code requests TTL/flags and validates origin; mocks cannot prove completion | Authorized development-account expiry/reuse/change/return/email-verification/session checks |
| Step-up assurance | No current backend assurance policy | Confirm provider-supported signed evidence and freshness semantics before enabling direct password change |
| Browser token disclosure reachability | Interceptor has no origin check; file URLs can be external | Browser/network test using controlled accepting mock origins; production exfiltration is not asserted |
| Public page visibility | Public HTTP aggregation bypasses local simulation database by design | Verify upstream visibility/data ownership policy with known public and private development resources |
| Page payload budgets | Unlimited buffered bodies/lists/log text | Representative archive sizes, decoded bytes, peak memory, mapping/serialization CPU and loop-lag measurements |
| Pool/cancellation behavior | Shared client/default pool; mock cancellation works for run paths | Local real HTTP server/load tests; inspect tail latency, connection reuse and cancellation under slow streams |
| Reset global quota | Per-process fixed-window implementation | Actual replicas/workers, ingress enforcement and desired effective policy; no cluster-wide guarantee inferred |
| Broader regressions | Targeted tests passed; entire suite was not run | Isolated Mongo/Temporal/Keycloak and other required test resources; preserve external-service restrictions |

None of these unknowns is reported as a successful live test. Existing plan statements about production settings and historic full-suite results remain historical claims, not current verification.

## 13. Definition of Done

The **audit deliverable** is complete when this document exists at the exact requested path, includes evidence and purpose/validation for every numbered recommendation, records current test results and uncertainty, and is the only repository modification.

The **future improvement work** is complete when:

1. Every accepted Major recommendation has an implementation or an explicit, evidence-backed risk disposition; issuer/account targeting, expiry/subject handling, browser credential routing and project cancellation defects have regression protection.
2. Direct password-change authorization has an agreed server-enforced assurance policy, its frontend integration is tested, and the development-tenant rollout gate is satisfied before enabling the new UI.
3. Management outages and page partial failures leave bounded work and no orphaned request-owned tasks; ticket issuance remains single attempt.
4. Page response contracts and public upstream credential isolation remain intact; representative payload limits and performance evidence distinguish network, CPU, serialization and memory costs.
5. Accepted Minor improvements pass their specific checks; optional adapter/pooling optimizations are not represented as measured gains without evidence.
6. Required backend/frontend checks, OpenAPI parity and authorized integration gates pass, or unresolved gates remain explicitly marked unverified. No mocked result is substituted for live Auth0 behavior or real network/database performance.
