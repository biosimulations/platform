# PR #111: implementation and merge-readiness plan

Date: 2026-10-27. Branch: `feature/auth0-backend-verification`.

PR: <https://github.com/biosimulations/platform/pull/111>.

This document specifies the remaining implementation and evidence needed to finish review items 1–4. The work performed in this session is the merge of current `origin/main`, the compatibility changes needed to resolve it, and this plan. It does not establish that the PR is fully implemented or ready to merge.

## 1. Baseline and meaning of completion

| Evidence | Recorded state |
| --- | --- |
| Original PR head | `05b5370093a1363c264d1ea6725bad0fe1448fa8` |
| Fetched `origin/main` | `c7c95a46dfe15e1e93e46d804084b598d6907d53` |
| Merge base before synchronization | `6b997ba117607f027caaf329ea1ee195bc211ee9` |
| Local synchronization commit | `114ccbda4d7c4722b3d0fb8337dea345f256e5a6` (parents: original PR head and fetched `origin/main`) |
| September review | Provider restrictions and same-token PATCH → GET consistency; retained during reconciliation |
| October integration feedback | [October 5 comment](https://github.com/biosimulations/platform/pull/111#issuecomment-5997771190); no subsequent feature commits preceded this synchronization |
| PR #119 | Merged into `main`; its tenant, transport, password-reset, and error-handling contracts are the baseline |
| Local interpreter | Python 3.14.7; strict mypy targets the repository's configured Python version |
| Remote PR update | This session does not push the branch or merge PR #111 |

“100% merge ready” means that every required gate in section 12 has direct evidence for the exact proposed PR head and the then-current target branch. It is not a probability, a guarantee about future changes to `main`, or a claim that a green mock test proves email delivery. Production acceptance is recorded separately.

Evidence labels used below:

- **CONFIRMED:** inspected in source or official provider documentation.
- **MEASURED:** observed in a named local command or probe.
- **PLANNED:** work or tests still to execute.
- **UNVERIFIED:** requires CI, a real tenant, a browser, or another environment.

## 2. Merge resolution already performed

The synchronization preserves both parents and the branch's useful history. It does not rebase, reset, or force-push the branch.

| Conflicting file | Resolution |
| --- | --- |
| `.gitignore` | Retain `main`'s expanded local-tool exclusions and the branch's machine-local MCP-config exclusion. |
| `backend/biosim_server/common/auth/auth0_management.py` | Retain `main`'s pooled client, token single-flight/cooldown, operation deadlines, typed errors, retry machinery, and password-change ticket implementation. Retain the resend helper, supply its now-required authentication deadline, and extend the explicit update signature for the branch's existing email/verification arguments. |
| `backend/biosim_server/users/models.py` | Retain the email field and fresh-email profile documentation, alongside `PasswordResetResponse` from `main`. |
| `backend/biosim_server/users/router.py` | Retain configured-tenant checks, private password-reset responses, step-up policy, and existing error classification. Preserve fresh Auth0 email precedence and database-provider restrictions. Apply the configured-tenant guard to the added resend route. |
| `backend/tests/users/test_router.py` | Retain both sets of regression tests. Use explicit local issuers and the current JWKS-cache interface. Reconcile the earlier name-only allowlist assertion with the now-reviewed email capability while continuing to reject forwarding of roles and caller-supplied verification status. |

Additional integration work registers `resend-verification-email` in the exhaustive API inventory and regenerates the committed OpenAPI artifact with the repository's generator.

The merge reconciliation fixes the immediate signature exceptions and adds the missing route guard. It deliberately does not treat these mechanical changes as completion of the reliability, validation, or adversarial test work below.

## 3. Review-item closure matrix

| Item | Status after reconciliation | Remaining closure work |
| --- | --- | --- |
| 1. Resend authentication deadline and transport | Required `deadline` argument supplied; standalone per-call client remains. | Use the pooled transport, enforce one whole-operation deadline through the job POST, classify failures, and add tests that retain the real authentication helper. |
| 2. Configured-tenant authorization | Guard added ahead of the provider check; existing PATCH/DELETE protections retained. | Prove foreign/missing issuer rejection, valid custom-domain behavior, same-subject isolation, and no outbound work on denied requests. Keep database-provider eligibility separate from tenant identity. |
| 3. Email-update helper compatibility | Explicit typed parameters reconcile the call sites; same-token regression retained. | Validate email/null semantics, keep the field allowlist strict, derive verification flags on the server, handle email conflicts, and test the actual HTTP helper. Resolve the retry side effect described in section 6. |
| 4. Throttling and retry responses | Existing PATCH/DELETE behavior preserved; resend still has a broad exception handler. | Return 503 plus `Retry-After` for classified Auth0 throttling, retain sanitized 502 behavior for other upstream failures, and document/test the response contract. |

No row is formally closed merely because it type-checks or its router-level mock passes.

## 4. Required public and provider contracts

### Public API

Keep these existing routes and operation IDs:

| Operation | Contract |
| --- | --- |
| `GET /api/v1/me` (`get-current-user`) | JWT-authenticated profile. Enrich only principals belonging to the configured tenant. Fresh Auth0 email wins when available; preserve JWT fallback on absent email or failed enrichment. Foreign principals receive their JWT-derived profile without querying another tenant's directory. |
| `PATCH /api/v1/me` (`update-current-user`) | Accept only reviewed profile fields, `name` and `email`. A name-only edit remains available for configured-tenant social accounts. An email edit requires a configured-tenant database account. No partial name update when a mixed request contains an ineligible email change. |
| `POST /api/v1/me/resend-verification` (`resend-verification-email`) | Authenticated, configured-tenant database account; no client-controlled target, identity, connection, or redirect. Preserve the 200 response and `message` key for compatibility. Describe success as acceptance of the verification request, not proof of inbox delivery. |

Keep the current behavior of ignoring unknown profile-request properties unless a separately reviewed API change is intended. Regardless of that policy, fields such as `roles`, `app_metadata`, `blocked`, `password`, `connection`, `user_id`, and caller-supplied `email_verified` must never enter the Management request.

### Provider API

The verified provider operations are:

- `POST https://{configured-domain}/api/v2/jobs/verification-email`, with a server-selected `user_id`, returns HTTP 201 when a job is created. Auth0 requires `identity` for primary social, enterprise, passwordless-email, and secondary identities. This PR retains its database-primary-only policy and does not add all-provider identity selection. [Auth0 verification-email API](https://auth0.com/docs/api/management/v2/jobs/post-verification-email).
- `PATCH https://{configured-domain}/api/v2/users/{verified-sub}` supports email and verification-control fields. Secondary-identity changes require a connection. Do not expose those targeting capabilities through this self-service route. [Auth0 user-update API](https://auth0.com/docs/api/management/v2/users/patch-users-by-id).

Deployment owners must verify `update:users` for update/job operations and `read:users` for enrichment on the real M2M grant. Preserve existing `delete:users` and password-reset grants; do not add broad permissions just to make a test pass. The email template must be enabled. Actual grants, connection policy, redirects, and delivery remain **UNVERIFIED** until checked in the intended tenant.

## 5. Work package A: finish the resend transport (item 1)

Modify `resend_auth0_verification_email` in `backend/biosim_server/common/auth/auth0_management.py`.

1. Create exactly one monotonic operation deadline using `_operation_deadline()` before token acquisition.
2. Obtain headers with `_auth_headers(deadline=deadline)`. Retain the existing token lock, cached failure behavior, and cancellation propagation; do not introduce another token cache or lock.
3. Get the process-owned client from `get_auth0_http_client()`. Do not open or close an `AsyncClient` inside this helper. Existing shutdown must remain its lifecycle owner.
4. After token acquisition, compute the remaining operation budget. If it is exhausted, raise `Auth0ManagementUnavailable` before starting the job request.
5. Wrap the entire in-flight job request in `asyncio.wait_for(..., timeout=remaining)`, retaining `_MGMT_HTTP_TIMEOUT_SECONDS` for HTTP phase limits. The current 25-second operation budget includes token-lock wait, token acquisition, and the resource request; separate phase timeouts alone are insufficient.
6. Send one job POST. Do **not** put this mail-sending operation through the generic retry loop: a timeout, connection loss, or 5xx can occur after the provider accepted the job. The password-change-ticket helper provides the relevant single-attempt pattern.
7. On an explicit HTTP 429, raise `Auth0ManagementRateLimited(_rate_limit_hint(response))`. On timeout or transport failure, raise a sanitized unavailable error. Propagate task cancellation without issuing another POST.
8. Require the expected provider success status and a JSON object with the expected job type and a nonempty job ID. Accept documented job states rather than hard-coding only `pending`; reject malformed success payloads as upstream failures.
9. Return only the helper's required job information; do not expose the M2M token, provider response body, or job metadata to the browser unnecessarily.

Acceptance: a valid database principal reaches the real helper without a missing-argument exception; one pooled client serves token and resource calls; an exhausted budget sends no job; a hanging in-flight request is cancelled within the budget; ambiguous outcomes cause at most one job POST per call. This does not promise deduplication across separate browser requests or API replicas.

## 6. Work package B: finish email-update semantics (item 3)

Modify `backend/biosim_server/users/models.py`, `users/router.py`, and `common/auth/auth0_management.py` together.

1. Validate supplied email values as email addresses and retain the public length bound. One concrete approach is Pydantic `EmailStr` plus an explicitly declared `email-validator` dependency and a regenerated `backend/uv.lock`; do not rely on a transitive package happening to exist in a developer environment. Use the repository's `uv` dependency workflow.
2. Preserve the distinction between omitted email and explicit `null`. Omitted means unchanged; explicit `null`, empty, whitespace-only, or malformed email returns 422 before any Auth0 call. Do not blindly lowercase the email local part. Keep the existing name/null behavior unless its contract is deliberately changed and tested.
3. Retain an explicit typed helper signature. Never restore arbitrary `**fields` forwarding at the HTTP-client boundary. Construct the JSON body from the approved fields and preserve name-only callers.
4. Derive `verify_email=True` and the intended unverified state on the server when changing an address. Do not accept verification status from request JSON or assume that a fake provider's automatic reset demonstrates the real tenant's behavior. Specify and verify the outgoing fields against the official API and a disposable real database account.
5. Apply the configured-tenant guard before resolving a subject in the Management API, and the database-account check before any mixed name/email write.
6. Handle definite duplicate-address responses with a stable sanitized 409 contract. Handle definite provider validation failures with a stable client-error response. Provider credential/scope failures are upstream/configuration failures; they must not masquerade as the user's tenant-authorization 403.
7. Preserve fresh profile construction on PATCH and GET, with JWT fallback only where enrichment is best-effort. Do not mutate the authenticated principal or assume a previously issued JWT changes after the update.
8. Address a consequence of the merge: `main` retries profile PATCH operations, but an email PATCH with `verify_email=True` may send mail again on replay. Keep existing bounded retry behavior for name-only edits; use a single-attempt bounded path for mail-producing email edits unless a verified provider idempotency mechanism is introduced. Retain the pooled client and token deadline in both paths.
9. Confirm no-op/empty PATCH makes no update call and name-only social edits still work. Do not “fix” the email path by removing `main`'s tenant guard or by allowing arbitrary Management fields.

Acceptance: name-only, email-only, and combined edits use the real helper with correct JSON; invalid inputs never reach Auth0; duplicate email is distinguishable from provider downtime; a single unchanged signed JWT sees the new address after PATCH → GET; ambiguous mail-producing PATCH failures do not cause an automatic second write.

## 7. Work package C: prove tenant and provider authorization (item 2)

The guard added during reconciliation needs regression coverage, including the actual verified-token boundary.

Required order for resend is authentication → Management configuration → configured-tenant eligibility → database-provider eligibility → provider operation. Preserve the existing unconfigured-503 vocabulary. A missing/invalid token remains 401 through the authentication dependency.

Use `tests/users/test_router.py` or a dedicated `backend/tests/users/test_verification_email.py` for the following cases:

- Configured tenant plus valid `auth0|...` subject succeeds.
- A separately trusted issuer with the same subject receives 403. Assert that neither token acquisition, profile lookup, nor job creation occurs for the denied route.
- Missing issuer fails closed with 403. Explicit configured custom-domain issuers follow `_configured_tenant_issuers`; unrelated trusted issuers do not.
- Google, GitHub, enterprise/SAML, and passwordless-email subjects receive the existing predictable 400 for these database-only email operations.
- Empty/malformed database identity tails and machine identities do not become eligible merely because of a prefix test. Define a local rejection response without weakening authentication elsewhere.
- Name-only edits for configured-tenant social principals continue to work; mixed name/email changes are refused atomically before any write.
- A real RS256 token from the foreign issuer is validated through current `get_current_user` using a mocked trusted JWKS transport/cache. Do not substitute a hand-constructed principal for the only security regression test.

Do not change the password-reset route's stricter issuer/subject and recent-authentication policies while implementing this feature. Its tests must continue to pass.

## 8. Work package D: finish failure and retry contracts (item 4)

Add a dedicated `except Auth0ManagementRateLimited` branch ahead of resend's generic exception handler, matching the existing PATCH/DELETE pattern. Keep logging bounded and sanitized; do not interpolate the subject, email, tokens, or raw provider error into resend logs.

| Condition | Required behavior |
| --- | --- |
| Missing/invalid access token | 401 through the existing auth dependency, including its normal challenge behavior |
| Management credentials not configured | 503, no outbound call |
| Foreign or missing issuer | 403, no tenant-directory lookup or mutation |
| Unsupported provider for email action | 400, no partial profile write or verification job |
| Malformed or explicitly null new email | 422 before outbound work |
| Definite duplicate email | Sanitized 409 for the email-update operation |
| Auth0 resource or token endpoint throttled | 503 plus a positive numeric `Retry-After` taken from the classified failure |
| Shared token-throttle cooldown | Same 503 class; hint represents the remaining cooldown rather than resetting or disappearing |
| Timeout, transport error, malformed success payload, or provider configuration failure | Sanitized 502; no automatic replay of an uncertain email-sending operation |
| Job accepted | 200 with the compatible message shape; no claim that inbox delivery was observed |

If a local resend abuse limit is added, use a separate issuer-scoped bucket patterned after `password_reset_rate_limit`, not the workflow quota. Return local 429 plus `Retry-After`, and document that existing in-memory limits are per process. This is a recommended follow-up unless made part of this PR's agreed acceptance scope; it is not a substitute for provider-throttling handling.

Do not expand this PR into a global retry-policy or rate-limiter rewrite. In particular, a finite-value parser issue in shared provider hints should receive a narrowly scoped regression/fix if the new tests expose it; do not silently suppress the failure with a blanket exception.

## 9. Regression test matrix and fixtures

Router mocks remain useful for response mapping. They cannot be the only proof of helper compatibility. Keep the real `_auth_headers` and `update_auth0_user` functions in at least one local HTTP-transport test for each public feature.

| Test group | Required assertions | Suggested location |
| --- | --- | --- |
| Resend success | Real token/header helper; correct domain, URL, Bearer header, user ID, HTTP 201 validation; no caller-controlled identity | `tests/common/test_auth0_management.py` |
| Pool/lifecycle | Shared client reused across calls; helper does not close it; application shutdown closes it | `tests/common/test_auth0_management_reliability.py` |
| Deadline | Lock wait, token acquisition, budget exhausted before job, and hanging job all respect the same deadline | Same reliability module |
| Concurrency | Concurrent cold calls share token acquisition; refresh failure/cooldown does not serialize repeated failed token POSTs | Same reliability module |
| Job side effects | Timeout, transport loss, and 5xx produce one job POST; cancellation produces no replay | Same reliability module |
| Provider response validation | Invalid JSON, list/scalar body, wrong type, missing/empty job ID, unexpected success status | `tests/common/test_auth0_management.py` |
| Update request shape | Real typed helper accepts name/email paths; only approved fields reach HTTP; verification flags are server-owned | Existing/new Management HTTP tests |
| Update retry split | Existing name-only retries remain; mail-producing email edits do not replay on uncertain outcomes | `tests/common/test_auth0_management_retry.py` |
| Profile continuity | One real signed JWT, unchanged old email claim, shared mutable provider state, actual PATCH/helper/GET sequence | `tests/users/test_router.py` |
| Authorization | Local, foreign, issuerless, custom-domain, supported/unsupported provider, malformed database identity, no outbound work when denied | `tests/users/test_router.py` or new verification-email module |
| Failure mapping | Token/resource 429, cooldown replay, deadline error, provider 403/scope, duplicate email, sanitized logs | User route tests plus HTTP helper tests |
| API inventory | Route, operation ID, required auth, no-body contract, documented statuses/headers, generated artifact equality | `tests/api/test_openapi_endpoints.py` |
| Regression safety | Existing GET/PATCH/DELETE, password-reset, issuer, token, and rate-limit tests stay green | Existing users/auth/rate-limit suites |

Use `httpx.MockTransport` with synthetic credentials and reset `_reset_management_state()` around tests that alter the pooled transport/cache. Restore dependency overrides and close test-owned clients in cleanup. Stub timing instead of sleeping through real backoff. Do not patch `_auth_headers` with an unrestricted `AsyncMock` in the only request-shape test: that hid the original required-argument incompatibility.

Mock transport establishes request shape and client reuse, not real socket reuse, tenant permissions, inbox receipt, or user login persistence.

## 10. File-by-file implementation map

All paths below exist unless marked NEW. They are relative to the repository root.

| Path | Planned change |
| --- | --- |
| `backend/biosim_server/common/auth/auth0_management.py` | Complete pooled single-attempt resend, whole-operation budgets, job validation, typed error classification, explicit email-update payload and retry split. |
| `backend/biosim_server/users/models.py` | Validate provided email while preserving omitted/null semantics and response aliases. |
| `backend/biosim_server/users/router.py` | Preserve tenant/provider gates, implement resend 503/retry mapping, sanitize logs, return consistent profiles and defined email errors. |
| `backend/tests/common/test_auth0_management.py` | Expand the current happy-path mock into real-auth-header/provider-shape and failure cases. |
| `backend/tests/common/test_auth0_management_reliability.py` | Exercise new helper deadline, cancellation, pooling, and cooldown behavior. |
| `backend/tests/common/test_auth0_management_retry.py` | Retain name-edit retry expectations; assert no replay for email-producing writes. |
| `backend/tests/users/test_router.py` | Retain the reconciled tests; strengthen tenant/provider, input, profile, and response-mapping assertions. |
| `backend/tests/users/test_verification_email.py` (NEW, optional split) | Isolate resend route/adversarial tests if the existing router module becomes unwieldy. |
| `backend/tests/api/test_openapi_endpoints.py` | Finish schema/error-header assertions beyond the inventory additions already made. |
| `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` | Regenerate after every final route/schema change; never hand-merge the generated YAML. |
| `backend/docs/auth0-tokens-claims-endpoints.md` | Document provider eligibility, updated email/source-of-truth behavior, request fields, failures, and provider prerequisites. |
| `backend/CLAUDE.md` | Update the relevant endpoint/verification guidance without removing password-reset or verification-listing documentation. |
| `backend/pyproject.toml`, `backend/uv.lock` | Change together if adopting `EmailStr`/`email-validator`; otherwise avoid unrelated dependency changes. |
| `frontend/app/pages/profile.vue` | Compatibility review and, only where needed, small response/state/error changes described below. |
| `backend/biosim_server/config.py`, `common/ratelimit.py` | Only if the separate local resend budget is included; keep new configuration narrowly scoped. |

**CONFIRMED:** the existing frontend hides the database-only email-edit control using the subject provider, but its unverified-email banner and resend button currently check only `email_verified`. They can therefore offer resend to an unsupported account. Apply database-account eligibility to every resend entry point; the backend remains the authorization authority. The page also discards the PATCH response and uses the Auth0 SDK's profile for display. Verify that a successful edit displays the new address and correct verification state; if SDK refresh does not deliver that, consume the PATCH/GET profile in local page state. Respect a 503/429 retry hint and show acceptance rather than claiming observed delivery. Include an unverified social account in browser acceptance. Keep password-reset UX and unrelated simulation/newsletter work outside this change.

## 11. Execution order and validation commands

Implement in these bounded steps, reviewing each resulting diff:

1. Finish resend transport and helper tests (A).
2. Finish email input/payload/retry semantics and same-token HTTP coverage (B).
3. Complete issuer/provider adversarial tests and any narrowly necessary guard fixes (C).
4. Finish failure mapping and retry-header tests (D).
5. Update API documentation, regenerate OpenAPI, and complete browser compatibility checks.
6. Run local gates, obtain exact-head CI/review evidence, refresh against any new `main`, and complete section 12.

Commands below run from `backend/`, using the repository's supported `uv` environment:

```bash
uv sync --frozen --all-groups
uv run python -m pytest tests/users tests/common/test_auth0_management.py tests/common/test_auth0_management_retry.py tests/common/test_auth0_management_reliability.py tests/common/test_ratelimit.py tests/api/test_openapi_endpoints.py -q
uv run python -m scripts.generate_openapi
uv run ruff check .
uv run mypy biosim_server tests
uv run python -m pytest -m "not integration"
```

Use Node 22 as configured by this repository for frontend checks; this session's default Node is 26.9.0 and is not frontend validation evidence. From `frontend/`, use the scripts actually present in `package.json`: `npm ci`, `npm run test:auth`, `npm run lint`, `npm run typecheck`, and `npm run build`. Do not silently omit a failed required script or describe an unrun build as passing.

From the repository root, verify branch integration and diff hygiene:

```bash
git fetch origin
git merge-base --is-ancestor origin/main HEAD
git ls-files --unmerged
git diff --check origin/main...HEAD
git status --short --branch
```

Ancestry must succeed and the unmerged-file list must be empty. Inspect and preserve any unrelated local state before another merge. If `main` advances, merge it into this feature branch, resolve the resulting semantic conflicts, regenerate the artifact, and repeat the required checks on the new head. Preserve this branch's history; do not reset it to `main` or force-push a rewritten replacement.

### Measurements from the synchronization session

| Check | Evidence/status |
| --- | --- |
| Unchanged fetched-main focused baseline | **MEASURED:** 262 passed in an archived copy of `c7c95a4`, using the same local interpreter and selected users/Management reliability/rate-limit/API suites. |
| Resolved-tree focused suite | **MEASURED:** 280 passed, including the branch's reconciled tests and generated-contract probes. |
| Full backend Ruff | **MEASURED:** passed. |
| Strict mypy, source and tests | **MEASURED:** passed across 199 source files. |
| OpenAPI generation | **MEASURED:** regenerated using `python -m scripts.generate_openapi`; contract equality passed in the focused suite. |
| Feature diff whitespace | **MEASURED:** `git diff --check origin/main` passed. A cached comparison to the old feature parent also includes an existing blank-line warning from `main` in `frontend/app/models/combine-api.ts`; do not attribute that inherited warning to the feature diff. |
| Full non-integration backend attempt | **MEASURED:** 1 failed, 1,472 passed, 15 skipped, 8 deselected, 86 errors. The failure reached an external simulator API and failed DNS; errors include unavailable Docker fixtures and Temporal test-server download. This is not a green full-suite result. |
| Comparison of full-suite failures with unchanged main | **MEASURED:** unchanged `c7c95a4` produced 1 failed, 1,454 passed, 15 skipped, 8 deselected, 86 errors. The failing/errored test-node sets match exactly; the merged tree adds 18 passing cases and no additional failures/errors. Full-suite green remains a CI/capable-environment gate. |
| Frontend, exact-head CI, live Auth0/inbox | **UNVERIFIED:** not established by the checks above. |

Do not alter unrelated fixture/test policy just to remove environment failures from the report. Reproduce suspected failures on the fetched-main baseline and retain a real full-suite CI or capable-environment gate.

## 12. Mandatory merge-readiness gates

Each gate requires an evidence link or command/result tied to the final head. All remain open for the future implementation unless a completed substep is explicitly recorded above.

- [ ] **G1 — Implementation:** all four review items meet their acceptance criteria; remaining per-request resend client and broad 502-only catch are removed; email validation and retry semantics are complete.
- [ ] **G2 — Security:** local/foreign/issuerless/custom-domain and provider tests pass, including a real signed foreign-token regression; no denied request triggers a configured-tenant Management call.
- [ ] **G3 — Compatibility:** real-helper resend/email paths pass with correct signatures, payloads, fresh-profile responses, and no arbitrary field forwarding.
- [ ] **G4 — Reliability:** deadline, cooldown, pooling, cancellation, typed throttle, positive retry hint, and no-ambiguous-replay tests pass. Existing password reset and name-only retry behavior remain intact.
- [ ] **G5 — Contracts/docs:** OpenAPI inventory and artifact equality pass; success/error/header semantics and tenant requirements match code and documentation; no unintended endpoint/schema changes.
- [ ] **G6 — Local verification:** full Ruff and strict mypy pass; the complete backend suite passes in a capable environment, with every skip individually justified; frontend required checks pass on the supported Node version.
- [ ] **G7 — PR delivery:** push the reviewed merge/implementation commits normally to `feature/auth0-backend-verification` when authorized, verify GitHub's PR head matches the expected commit, and refresh the task links after the PR updates.
- [ ] **G8 — CI/security checks:** required backend/frontend/smoke and configured security checks pass on that exact PR head. Verify actual repository-required checks rather than assuming every historical check is required. This repository's backend workflow runs pytest; do not mislabel that as CI Ruff/mypy coverage.
- [ ] **G9 — Fresh target:** fetch immediately before final approval, confirm current `origin/main` is an ancestor of the tested head, and resolve any further changes. GitHub reports a mergeable PR with no unresolved conflicts.
- [ ] **G10 — Review:** a reviewer verifies closure of September and October feedback plus current items 1–4. Resolve review threads only after their code and evidence are present; this plan is not a review approval.
- [ ] **G11 — Provider/browser acceptance:** capture the disposable-account and UI evidence in section 13. If project policy allows some evidence after merge, record that owner-approved release gate explicitly and do not claim production readiness.

Record final PR head, fetched target SHA, CI run URLs, required-check conclusions, review outcome, provider evidence, skips, and any accepted limitations in a short readiness ledger. A passing old SHA or branch-only mock suite cannot close these gates.

## 13. Real-provider and browser acceptance

Use an approved staging/disposable Auth0 database account and test inbox. A tenant maintainer or authorized operator performs changes; do not send verification mail or change a real user's login address as a side effect of routine CI.

1. Verify the configured issuer/domain relationship, API audience, enabled database connection, M2M grant, active verification template, and approved email-provider/redirect settings. Record configuration evidence without credentials.
2. Log in as the disposable database user. Read `/api/v1/me`, record current email/verification state, and retain the same access token securely for the same-token sequence.
3. Change to another controlled inbox address through PATCH. Confirm the response and a GET with that unchanged token return the new address and expected unverified state. Confirm the old email claim is still on the token, so the result proves refreshed source-of-truth precedence.
4. Confirm one expected verification message arrives after the email-changing action. Separately invoke resend, confirm acceptance and intended inbox receipt, redeem the link, and verify the profile reflects verification. Keep provider job acceptance, delivery, and redemption as separate evidence.
5. Sign out and back in to verify connection/login persistence and updated claims. Exercise the current profile page to catch stale SDK state and incorrect disabled/visible controls.
6. Check an approved social test account: name-only behavior follows connection policy; database-only email/resend operations are unavailable. Use local signed-token tests for cross-tenant collision coverage rather than creating collisions in production.
7. Exercise throttling/failures with local transports or staging fault injection; do not deliberately exhaust the production tenant. Confirm the UI honors retry timing and does not automatically replay an ambiguous email-sending mutation.
8. Restore or remove the disposable account according to the test owner's procedure. Record date, environment, tested commit, operator, observed job/inbox/redemption results, and remaining limitations. Exclude tokens, ticket URLs, and personal inbox content from repository artifacts.

Production deployment is a separate GitOps release task. If this feature is released, follow the repository's publish-before-overlay-bump procedure; use a revert of the feature commits/merge for rollback while recognizing that already-sent email and already-applied identity changes are not undone by a code revert.

## 14. Scope and handoff

The implementation targets the existing PR #111 capability on current `main`. Do not add newsletter enrollment, a new identity store, all-provider linked-account editing, a password-reset redesign, a generic retry framework, or a distributed queue as part of these four fixes.

Hand off the synchronized branch and this plan with: the merge commit, exact fetched-main SHA, focused/static evidence, full-suite baseline comparison, known pending work from section 3, and the still-open gates from section 12. The plan and a conflict-free merge are concrete preparation for implementation; they are not evidence that every gate has been satisfied.
