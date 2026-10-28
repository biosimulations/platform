# PR #111: merged implementation plan

Date: 2026-10-27. Repository: `biosimulations/platform`. Branch: `feature/auth0-backend-verification`.

**Phase 6 backend update, 2026-10-27:** Phases 0–4 are committed through `267651c`. The backend contract docs, the verification-email quota, and the `read:users` grant guidance are now **committed in `887a91d` and pushed** to `origin/feature/auth0-backend-verification`. Phase 5, the frontend half of the local gates, live Auth0 acceptance, and review-thread closure remain open.

**Review corrections, 2026-10-28:** `5c6fbd8` committed this plan to `docs/` and became the pushed PR head; it changes no other file. An independent review of `5c6fbd8` confirmed the Phase 0–4 and Phase 6 backend work and found two gaps against section 10, plus stale statements in this document. The commit that adds this paragraph closes them; see [Review corrections after `5c6fbd8`](#review-corrections-after-5c6fbd8). That commit is local until it is pushed.

PR: <https://github.com/biosimulations/platform/pull/111>.

This is the only PR #111 plan. It replaces two earlier drafts: the post-merge plan that was committed at `23a7434` as `docs/pr-111-implementation-plan.md` (that file is removed), and the pre-merge draft that previously occupied this path. Section 1 still names those drafts where their claims differ.

Section 1 records which source is accurate, which is the stronger specification, and what this plan selects. Sections 2–16 are the implementation. Writing this file does not change application code, push the branch, send mail, or close a merge gate.

Evidence labels:

- **CONFIRMED:** inspected source or Git state. The pushed PR head is `5c6fbd8`, which only adds this plan on top of `887a91d`; the review-correction commit follows it locally. Phase 1 fixes are committed in `dd850f0`, Phases 2 and 3 in `e1809fb`, Phase 4 in `267651c`, and the Phase 6 backend documentation in `887a91d`. Historical comparisons explicitly name their older snapshots.
- **MEASURED:** a command result tied to its stated snapshot. The phase status sections record the independent local review on 2026-10-27; section 13 retains older synchronization-tree measurements. Neither is evidence for future fixes or an untested commit.
- **OFFICIAL:** Auth0 documentation cited by both source plans.
- **SELECTED:** a choice this merged plan makes where the sources disagree.
- **UNVERIFIED:** needs CI, a real tenant, a browser, or a fresh command.

## Phase 0 status — reconciliation and baseline

**Status: complete locally and committed.** The independent review confirmed:

- Current remote main `c7c95a46dfe15e1e93e46d804084b598d6907d53` is an ancestor of local HEAD.
- Original PR head `05b5370093a1363c264d1ea6725bad0fe1448fa8` remains an ancestor.
- There are no unmerged paths; `git diff --check origin/main...HEAD` and `git diff --check` pass.
- The merge retains configured-tenant guards, email editing, password reset, pooled Auth0 infrastructure, and the same-token PATCH→GET regression.
- Phases 2 and 3 are committed at `e1809fb`, Phase 4 at `267651c`, and the Phase 6 backend docs at `887a91d`. The remote-tracking feature ref matched `887a91d` after the Phase 6 push and matched `5c6fbd8` (this plan) on 2026-10-28; `origin/main` was still `c7c95a4`. Refresh remote refs before publication.
- Deletion of `docs/pr-111-implementation-plan.md` is committed in `3f7d1da`. No tracked deletion remains uncommitted. This replacement plan was first kept untracked under the gitignored `.agents/` directory. `5c6fbd8` then committed it at `docs/pr-111-main-reconciliation-and-implementation-plan.md`, so it is now tracked and ships with the feature.

Recheck main before publication and final approval. Phase 0 completion records successful reconciliation, not approval of the whole feature or closure of the later implementation gates.

## Phase 1 status — resend transport and helper tests

**Status: complete locally and committed in `dd850f0`.** P1-D1, P1-D2, and P1-T1 were independently verified at that commit: 345 focused tests, 1,630 full-suite tests with 17 skipped, Ruff, mypy, formatting, diff checks, and a completed four-file CodeRabbit review with zero findings. These are local results, not live-provider or CI evidence.

The Phase 1 commit changes:

- `backend/biosim_server/common/auth/auth0_management.py`
- `backend/tests/common/test_auth0_management.py`
- `backend/tests/common/test_auth0_management_reliability.py`

Commit `4183086` introduced the resend implementation; `dd850f0` contains its defect fixes and additional tests, including `tests/common/test_auth0_management_retry.py`. The implementation described next reflects both commits. Publication remains a separate gate.

In the committed tree (`dd850f0`), the former standalone `httpx.AsyncClient` has been replaced. The helper now:

- uses the existing pooled Auth0 client and the real `_auth_headers(deadline=...)` path;
- owns one 25-second operation budget across token acquisition, the live eligibility GET, and the conditional job POST;
- uses the shared private `_get_user()` for both public profile reads and the resend eligibility read, with a reduced read deadline preserving the 10-second POST reserve;
- validates the live user id, database primary identity, present email address, and boolean verification state;
- returns `ResendOutcome.ALREADY_VERIFIED` without creating a job for an already-verified live account;
- makes exactly one job POST once submission starts, including job timeout, transport failure, and 5xx; eligibility/budget failures make no job POST, and no generic retry loop replays submission;
- validates HTTP 201, the acknowledgment object, nonempty id, and `type=verification_email`; accepts only a string status of `pending` or `completed`. A list or object status raises `Auth0ManagementError` after one job POST;
- raises typed `Auth0ManagementRateLimited` on 429 and sanitized `Auth0ManagementUnavailable` on timeout or transport failure;
- propagates cancellation during token wait, lookup, and job POST;
- rejects NaN and infinities in `Retry-After` with `math.isfinite`, preserving the existing fallback and ceiling behavior;
- encodes the user id as one path segment for the eligibility GET (and the other user-resource helpers).

The success-path test retains the real token/header helper rather than mocking `_auth_headers`. Synthetic HTTP is provided by `httpx.MockTransport`; no real tenant or email was used.

**Measured evidence from the independent review of HEAD `3f7d1da` plus the three working-tree changes:**

- `.venv/bin/python -m pytest tests/common/test_auth0_management.py tests/common/test_auth0_management_reliability.py tests/common/test_auth0_management_retry.py tests/users tests/common/test_ratelimit.py tests/api/test_openapi_endpoints.py -q -o log_cli=false --disable-warnings` from `backend/` → **331 passed**.
- `.venv/bin/python -m pytest -q -o log_cli=false --disable-warnings` from `backend/` → **1,616 passed, 17 skipped**, in 120.86 seconds. These are local suite results, not live Auth0 delivery evidence.
- `ruff check .` → passed.
- `ruff format --check` on the three changed Python files → **3 files already formatted**. The independent review did not reformat application files.
- `mypy biosim_server tests` → **Success: no issues found in 199 source files**.
- Both `git diff --check` and `git diff --check origin/main...HEAD` → passed.
- CodeRabbit review of the then-uncommitted changes completed with **one minor test-quality finding**; see P1-T1 below. It did not detect the two independently reproduced implementation defects.

The earlier implementation report recorded 84 passing helper/reliability/retry tests and a 67-test baseline across a different four-module selection. Those are historical results, not additional fresh runs in this review. Passing suites do not close defects absent from their test cases.

### Phase 1 defects closed in `dd850f0`

The identifiers below are the review's tracking IDs. Both fixes are committed in `dd850f0`, with regressions. Source lines below describe the tree before these edits.

#### P1-D1 — malformed job status escapes typed validation

**Location:** `backend/biosim_server/common/auth/auth0_management.py:650–659`, especially the set-membership check at line 657.

**Reproduced:** a valid HTTP 201 object with a valid job id/type but `status: []` or `status: {}` raises `TypeError` because the value is unhashable. It should raise the helper's `Auth0ManagementError` for an invalid acknowledgment. The probe used the real token/header/resend helper and `httpx.MockTransport`; each case made exactly one job POST. The current route's broad exception handler masks this as a 502, but the helper's typed failure contract is still violated.

**Required fix:** check that `job_status` is a string before membership in `{"pending", "completed"}`. Retain existing validation and the one-attempt policy. Do not retry malformed acknowledgments, since a job may already have been created.

**Required regression:** extend `test_invalid_job_acknowledgment_is_an_upstream_error` in `tests/common/test_auth0_management.py` with array and object status values. Assert `Auth0ManagementError`, one token acquisition/eligibility flow as appropriate, and exactly one job POST. Keep valid pending/completed cases passing.

- [x] P1-D1 corrected. `job_status` must be a string before membership. `test_invalid_job_acknowledgment_is_an_upstream_error` covers `status: []` and `status: {}`, asserts `Auth0ManagementError`, one token POST, one eligibility GET, and one job POST.

#### P1-D2 — eligibility-lookup Retry-After is rounded down

**Location:** `backend/biosim_server/common/auth/auth0_management.py:395–399`, in `_send_with_retry`.

**Reproduced:** the real resend helper receives repeated eligibility-GET 429 responses with `Retry-After: 7.5`. It raises `Auth0ManagementRateLimited(retry_after=7)` because the retry loop uses `int(retry_after)`. Token/job hint handling rounds up to 8. The probe made one token POST, three bounded GET attempts, and zero job POSTs. The lookup hint must not tell the caller to return earlier than the latest upstream delay.

**Required fix:** replace truncation with `math.ceil`, retaining the positive minimum, finite-value validation, ceiling, and fallback. Apply this to the shared retry helper rather than adding a resend-only workaround. Preserve name-only/read retry behavior and typed 429 classification.

**Required regression:** exercise lookup throttling through the real resend helper with fractional hints, asserting the ceiling-rounded typed hint and zero job POSTs. Extend the shared retry tests to cover fractions below one and above one, ordinary integers, clamped hints, and invalid/non-finite hints. Verify token cooldown, lookup, and job paths use consistent positive integer hints.

- [x] P1-D2 corrected. `_send_with_retry` uses `math.ceil` for the exhausted hint. `test_lookup_retry_after_is_rounded_up_and_sends_no_job` drives a real resend helper through three eligibility-GET 429s with `Retry-After: 7.5` and expects hint 8 and zero job POSTs. `test_exhausted_429_hint_is_a_positive_ceiled_integer` covers fractions below and above one, integers, the 30-second clamp, and non-finite or malformed hints on the shared read path.

### Completed Phase 1 verification work

**P1-T1 — strengthen deadline evidence (not a third reproduced implementation defect).** Add the plan's missing slow-token-acquisition case: consume part of the shared budget during the real token request and demonstrate that lookup/job work either retains the full POST reserve or stops without a job. Include both admission and refusal cases without real long sleeps.

CodeRabbit's **minor** finding concerns `test_exhausted_resend_budget_sends_no_job` at `tests/common/test_auth0_management_reliability.py:602–616`: with a cold cache and an already-expired deadline, it exits during token acquisition. That is useful token-budget coverage, but it does not exercise the post-lookup reserve check. Name/document that case accurately and assert its failure stage; retain the separate `test_lookup_entering_the_post_reserve_sends_no_job` and strengthen the slow-token coverage. Patch the module's clock reference locally rather than mutating the process-wide `time.monotonic`, which also changes the event-loop clock.

- [x] P1-T1 completed. Deadline checks use `_monotonic`, so tests do not patch the process-wide clock. `test_token_budget_already_exhausted_sends_no_lookup_or_job` fails in token acquisition and makes no HTTP call. `test_lookup_entering_the_post_reserve_sends_no_job` still covers a lookup that consumes the reserve. `test_slow_token_preserves_or_refuses_the_post_reserve` advances the module clock during the real token POST: at 5 seconds the job still has the full 10-second reserve; at 16 seconds the lookup stops and no job is sent.
- [x] Helper/reliability/retry tests: **98 passed**. The former 331-test selection, plus the new cases: **345 passed**.
- [x] `mypy biosim_server tests` → no issues in 199 source files. `ruff check` on the four touched Python files passed. Full suite, outside the sandbox so Docker and live-API tests can run: **1,630 passed, 17 skipped**, in 116.44 seconds. That is the previous 1,616 plus the 14 new cases. These implementation-time counts predate the independent committed verification of `dd850f0` recorded above.
- [x] This status records that evidence. The fixes are committed in `dd850f0`. Publication and PR-wide readiness remain separate gates.

## Phase 2 status — resend route, quota, privacy, and response contract

**Status: complete and committed in `e1809fb`; independently revalidated at that commit.**

**Historical audit of `dd850f0`, before Phases 2 and 3 were committed:** at that commit Phase 2 was not in the tree. Seven tracked files were modified in the working tree, and `backend/tests/users/test_verification_email.py` plus `docs/verification-email-api.md` were untracked. `git show dd850f0:backend/biosim_server/users/router.py` had the old 200 response, generic 502 mapping, and subject/exception logging. That commit had no verification quota and no typed resend response model. Phase 1's live-read helper was present, and its route outcomes were not wired.

That Phase 2 snapshot passed a fresh rerun of all **390 focused tests** (5.41 seconds), including the OpenAPI artifact comparison; `git diff --check` also passed. The full-suite and CodeRabbit results below belong to the preceding implementation validation, not a new run during this committed-state audit. The plan was still untracked then. This was a pre-commit caution: the complete Phase 2 and Phase 3 source/test/schema/dependency/documentation changes were subsequently committed together in `e1809fb` and revalidated as recorded in the Phase 3 status.


Completed:

- The route calls the real Phase 1 helper once after all guards. That helper owns the live account read, subject match, eligibility validation, deadline, and conditional single-attempt job POST; the route does not duplicate the read.
- Typed `VerificationEmailResponse` preserves `message`, returns 202/`accepted` for a validated acknowledgment and 200/`already_verified` with no job. Acceptance never claims delivery.
- Nonempty bodies (including `{}`, null, whitespace, and invalid JSON) and query strings produce generic 422 before quota or upstream work. Body streaming stops at the first nonempty chunk.
- Guard order is authentication, input, configuration, configured tenant, quota, database subject, helper. Empty/malformed database tails, additional delimiters, whitespace/control characters, machine-account suffixes, and unsupported providers are rejected.
- A typed `Auth0ManagementIneligible` maps a live absent address or unsupported primary provider to 400. Different subjects or malformed required live fields remain generic 502; neither creates a job.
- Dedicated issuer/subject `verification-email` quota uses defaults 3 requests/600 seconds, positive setting validation, independent namespace, shared kill switch, and no refund for failures, unsupported accounts, or no-ops. Input/configuration/foreign-issuer failures are free.
- `_PrivacyHeadersRoute` applies no-store on successes and HTTP errors, including dependency 401/503, preserving challenge/retry headers. Router-level 404/405 remain outside the handler's scope.
- Auth0 token/lookup/job throttling and token cooldown return 503 plus positive Retry-After. Other upstream errors return sanitized 502. Cancellation propagates.
- Fixed-category application logs omit exception text and identity data. A ContextVar-scoped transport filter suppresses HTTPX/HTTPCore records only during resend; unrelated concurrent requests retain logging, and cancellation resets the scope.
- Existing provider tests were retained and updated for the new contract. New route tests exercise the real token/lookup/job helper, no-job branches, quota behavior/isolation/rollover/disable, input streaming, privacy, cancellation, and real signed local/foreign/invalid JWTs with mocked JWKS.
- Generated OpenAPI now describes both outcomes and errors. `docs/verification-email-api.md` documents request/response semantics, quota settings and per-process limits, logging scope, and external acceptance boundaries.

Pre-commit validation for the Phase 2 working tree on 2026-10-27:

- Focused route/helper/reliability/retry/quota/OpenAPI selection: **390 passed** (`tests/users`, the three `test_auth0_management*` modules, `tests/common/test_ratelimit.py`, `tests/api/test_openapi_endpoints.py`).
- Full backend suite: **1,675 passed, 17 skipped**, in 123.89 seconds; 45 tests added over the independently verified Phase 1 baseline.
- `ruff check biosim_server tests` passed; `mypy biosim_server tests` passed for 200 source files. Formatting checks passed for the route, response models, Management helper, and new verification test module. Existing unrelated formatting in config, rate-limit, and provider-test files was preserved.
- OpenAPI regenerated; the artifact-equals-runtime regression passed. `git diff --check` passed.
- CodeRabbit completed its review of all nine changed/new source, test, schema, and API documentation files with **zero findings**. The plan, untracked at the time, was not part of that review.

No live Auth0 request or email dispatch is used by the new tests. The Phase 2 changes and their documentation are now committed in `e1809fb`; the exact committed-head revalidation is recorded below. No push or GitHub comment is part of this review.

## Phase 3 status — email validation, strict PATCH, and retry split

**Status: complete and committed in `e1809fb`; independently revalidated at that commit.** The implementation satisfies Phase 3's local and mocked-provider acceptance criteria. Real tenant connection, verification-control, delivery, and redemption behavior remains unverified under section 15, and later phases do not close because Phase 3 is complete.

Completed:

- `UpdateUserProfileRequest` forbids all unknown fields. Only `name` and `email` are accepted. Omitted email means unchanged; explicit null, empty/blank, malformed, non-string, display-name, and over-255-character addresses are 422 before Auth0. Mixed valid-name/invalid-email requests write nothing. `EmailStr` performs syntax validation without DNS/delivery checks, preserving local-part case while normalizing the domain.
- `email-validator>=2.2,<3` is an explicit runtime dependency; `uv add` regenerated `backend/uv.lock` (locked 2.3.0). The OpenAPI property is optional but non-nullable, without a misleading null default, and the schema forbids additional properties.
- Existing configured-tenant and database guards remain before email writes. Empty PATCH still performs no update; name-only social updates and the pre-existing null-name behavior are preserved. No authenticated principal is mutated.
- The helper retains explicit keyword-only `name`, `email`, and `verify_email` parameters. Any supplied email produces server-owned `email_verified=False` and `verify_email=True`; a caller cannot override either through the request model. Even helper calls with `verify_email=False` cannot carry verified state onto a new address.
- Email-containing PATCHes, and any helper operation explicitly requesting verification, make one attempt with the pooled client, original operation deadline, HTTP phase timeout and no redirect following. Timeouts, transport loss, 5xx, malformed success, and cancellation do not start a second PATCH. Name-only updates retain the existing bounded retry. Each supplied email is conservatively treated as potentially mail-producing, including a same-address update; no preliminary equality read is added.
- Typed conflict and provider-validation errors map email-update 409 to sanitized 409 and provider 400/422 to stable sanitized 422. Token/Management throttling retains 503/Retry-After. Credential/scope failures, missing users, and other upstream errors stay generic 502. Classification uses response status for this allowlisted payload; raw provider text is never returned or logged. Error logs use fixed categories.
- `tests/users/test_profile_updates.py` drives the mounted route and actual token/update/read helpers through `httpx.MockTransport`. Coverage includes name/email/combined payloads, validation and extra fields before all outbound work, provider error mapping, retry split, deadline exhaustion, hanging requests, cancellation, social name-only updates, and denied guards.
- A new continuity regression uses one real signed JWT and mocked JWKS with the real `_auth_headers`, update, and read helpers. PATCH then GET both show the changed provider email and unverified state, while the unchanged JWT still contains the old email. The original September regression in `tests/users/test_router.py` is retained. These are local integration results, not proof of real tenant email behavior.
- The previous test that expected ignored extra fields now expects atomic 422. OpenAPI was regenerated. `docs/profile-email-api.md` and the backend token/endpoint guide document the strict contract and retry semantics.

Pre-commit validation recorded during Phase 3 implementation:

- Baseline before Phase 3: **390 focused tests passed** in 5.32 seconds.
- Updated focused selection: **466 passed** in 5.81 seconds (76 new Phase 3 cases); this includes all Phase 2 route tests and OpenAPI artifact equality.
- Ruff and strict mypy passed (201 source files). Formatting checks passed for the changed model, router, helper, and new profile tests. `uv lock --check` and `git diff --check` passed.
- Full backend suite: **1,751 passed, 17 skipped**, in 126.15 seconds; 76 more passing tests than the Phase 2 baseline. Skips remain distinct from passing tests and do not establish live provider behavior.
- CodeRabbit completed its review of all 14 changed/new source, test, dependency, schema and API-documentation files with **zero findings**. The plan, untracked at the time, was outside that review.
- Those were pre-commit results. Phases 2 and 3 were then committed together in `e1809fb6add656e74453d185f11b26bc9855eb4a`.

Committed-head revalidation of `e1809fb` on 2026-10-27:

- The same focused route/helper/reliability/retry/quota/OpenAPI selection passed again: **466 passed** in 5.98 seconds.
- The full backend suite passed again: **1,751 passed, 17 skipped**, in 119.52 seconds.
- `uv run ruff check .` passed; strict `uv run mypy biosim_server tests` passed for 201 source files; `uv lock --check` passed.
- The tracked working tree and index were clean. `git diff --check origin/main...HEAD` and both required ancestry checks passed.
- A fresh CodeRabbit committed review of `dd850f0..e1809fb`, covering all 14 changed source, test, dependency, schema, and API-documentation files, completed with **zero findings**.
- At that snapshot the last inspected remote-tracking feature ref was `dd850f0`; `e1809fb` was local and ahead by one commit. No push, final-head CI, live Auth0 request, or browser acceptance was claimed then. The branch has since been pushed: `887a91d` is the current PR head.

The [official Auth0 update-user contract](https://auth0.com/docs/api/management/v2/users/patch-users-by-id) documents `email_verified`, `verify_email`, and the primary/secondary identity restrictions. Outgoing fields were checked against that contract. Real tenant verification control, custom connection behavior, delivery, and redemption remain section 15 gates.

## Phase 4 status — tenant and provider composition

**Status: complete and committed in `267651c`.** No application guard change was required. `_require_database_account` already rejects an empty tail, a second `|`, whitespace, and `@clients`. `_configured_tenant_issuers` already accepts both `https://{AUTH0_DOMAIN}/` and an explicit `AUTH0_ISSUER` custom domain.

`tests/users/test_verification_email.py` already existed and still covers the route with overridden principals, including foreign and issuerless 403s, provider rejection, and one signed-token resend matrix. The new module does not override `get_current_user`.

Completed in `tests/users/test_verification_email_composition.py`:

- A custom-domain issuer (`AUTH0_ISSUER=https://login.example.com/` while Management stays on `tenant.auth0.com`) and the domain-convention issuer both reach one verification job.
- A trusted foreign issuer with the same subject is 403 on resend and on email PATCH. Token acquisition, profile reads, patches, and jobs stay at zero.
- Empty tails, extra delimiters, `@clients`, whitespace, and Google, GitHub, SAML, and passwordless subjects are 400 with no Management call.
- A custom-domain social account can change its name and cannot send a verification job. A database email PATCH is one write with server-owned `verify_email=True` and `email_verified=False`.
- Wrong audience, expiry, missing `exp`, a bad signature, a missing `iss`, a bare `Bearer`, and a non-JWT are 401 and do not reach Management.

A JWT with no `iss` is rejected by authentication with 401, so it never becomes the route's issuerless 403. That 403 remains covered by `test_foreign_issuer_is_free` using a principal whose issuer is `None`.

Measured at committed HEAD `267651c` on 2026-10-27: the composition module **21 passed**; the whole `tests/users` suite **255 passed**; the full backend suite **1,772 passed, 17 skipped**; `ruff check .` passed; `mypy biosim_server tests` passed across **202 source files**. The 1,772 count is the `e1809fb` figure of 1,751 plus these 21 new cases. These are local results; no live Auth0 request or browser acceptance is claimed.

## Phase 5 status — frontend profile state

**Status: not started. Owned by the frontend team; the backend author of this plan does not implement it.**

Section 9 defines the required frontend behavior: authoritative `/me` state, 202/200 handling, provider gating of email and resend controls, `retry: 0`, no false success, `test:profile`, and CI wiring. None of it is in the tree. The backend contract it must consume is committed in `887a91d`:

- `POST /api/v1/me/resend-verification` returns 202 `outcome=accepted`, 200 `outcome=already_verified`, 400 for unsupported accounts, 403 for foreign tenants, 429 and 503 with `Retry-After`, and 502 for uncertain upstream failures.
- `PATCH /api/v1/me` accepts only `name` and `email`; unknown fields are 422, so a stale client that posts an extra field now sees an error rather than a silent no-op.
- `GET /api/v1/me` is the authority for `email`, `emailVerified`, `name`, and `provider`.

Phase 5 is not a prerequisite for the backend work already done in Phase 6, but it does block G1's section 9 clause, the frontend half of G6, the frontend job in G8, and section 15's browser steps.

## Phase 6 status — backend documentation and local validation

**Status: backend portion complete and committed in `887a91d` (pushed). The release gates are not closed.**

Already in the committed tree, and confirmed unchanged by regenerating the artifact (`python -m scripts.generate_openapi` produced no diff):

- `POST /api/v1/me/resend-verification` is in the OpenAPI inventory as `resend-verification-email`.
- The generated spec documents `202` and already-verified `200` with `VerificationEmailResponse` (`outcome` of `accepted` or `already_verified`, plus `message`), `Cache-Control: no-store`, `Retry-After` on 429 and 503, and `WWW-Authenticate` on 401.
- `UpdateUserProfileRequest` documents optional non-null `email` (`format: email`), forbids additional properties, and the PATCH operation documents 409, 422, 502, 503, and 403.
- `docs/verification-email-api.md` and `docs/profile-email-api.md` already describe those contracts, the quota, and the acceptance boundary.

Added in `887a91d`:

- `backend/.env.example` documents `read:users` on the Management client and `RATE_LIMIT_VERIFICATION_EMAIL_PER_WINDOW` / `RATE_LIMIT_VERIFICATION_EMAIL_WINDOW_SECONDS`.
- `backend/CLAUDE.md` lists `GET`/`PATCH`/`DELETE /api/v1/me` and the resend route, the same scopes, and the verification-email rate-limit bucket.
- `backend/docs/auth0-tokens-claims-endpoints.md` records the resend grant, the strict PATCH rule, and both endpoint rows.
- `kustomize/README-config.md` lists the quota variables and adds `read:users` to the Management secret description.
- `backend/README.md` points at the two API notes.

Measured before the documentation commit, from `backend/`:

- `uv run ruff check .` passed.
- `uv run mypy biosim_server tests` passed, 202 source files.
- `uv run python -m pytest -q` passed **1,772** tests and skipped 17, in 123.66 seconds. The 17 skips are the existing integration cases, not new resend tests.

Re-verified at committed HEAD `887a91d` on 2026-10-27: the OpenAPI artifact regenerates with no diff when the generator's `ENABLE_RBAC_DEMO=true` is set (the artifact test in `tests/api/test_openapi_endpoints.py` passes, 91 tests); the full backend suite passed **1,772** with 17 skipped; `ruff check .` and `mypy biosim_server tests` (202 files) passed.

One measurement that is not a gate: `ruff format --check .` reports 153 files would be reformatted, including files untouched by this branch. Neither CI nor lefthook runs `ruff format` (CI runs `pytest`; lefthook runs `ruff check`), and the same is true on `origin/main`, so this is pre-existing repository state, not a regression from this feature. Do not reformat the tree inside this PR.

Left undone, on purpose for this backend pass:

- Frontend lint, typecheck, `test:profile`, and production build. `test:profile` does not exist until Phase 5.
- Live Auth0 grants, delivery, and browser acceptance (section 15).
- Refreshing `origin/main` and closing review threads.

The branch was pushed: `887a91d` was then the PR head on GitHub (PR #111, `MERGEABLE`). CI on that head: GitGuardian **pass**, Snyk **pass** (1 test skipped), backend `build` **pass** (2m36s), frontend `build (ubuntu-latest, 24)` **pass** (42s), `smoke` **pass** (2m31s). A green backend workflow is G8 evidence, not G6. The next push, `5c6fbd8` (this plan only), passed the same five checks, and GitHub reported the PR `MERGEABLE` with merge state `CLEAN` on 2026-10-28.

Note on the generator: `backend/scripts/generate_openapi.py` forces `ENABLE_RBAC_DEMO=true` before importing the app, because the demo router is config-gated and the committed baseline includes its paths. Regenerating by hand without that variable drops four `/api/v1/demo/*` paths and four schemas, which looks like drift but is not. Use the script, or set the variable.

### Review corrections after `5c6fbd8`

`5c6fbd8` is described by its commit message as changes to the resend endpoint, tests, and documentation, but it adds only this plan. It is pushed, so do not rewrite it. Describe it accurately in the PR body (G7), or squash on merge.

An independent review of `5c6fbd8` on 2026-10-28 reproduced the Phase 0–4 and Phase 6 backend evidence at that head: both ancestry checks, no unmerged paths, `git diff --check origin/main...HEAD`, `ruff check .`, strict mypy (202 files), `uv lock --check`, OpenAPI regeneration with no diff, **1,772 passed / 17 skipped** in the full suite, 21 composition tests and 255 `tests/users` tests. It also found the following, all closed in one commit on top of `5c6fbd8`:

- **Untyped job redirect (section 5 item 10, section 10 "Acknowledgment").** `resend_auth0_verification_email` called `raise_for_status()` on the job response, so a redirect (and any 4xx or 5xx) escaped as `httpx.HTTPStatusError` instead of the helper's typed error. A job 5xx now raises `Auth0ManagementUnavailable`, because Auth0 may have queued the job before failing. Any other status except 201, including a redirect that is never followed, raises `Auth0ManagementError`. Each case still makes exactly one job POST, and the route still returns 502. `test_invalid_job_acknowledgment_is_an_upstream_error[redirect]` is new. `test_ambiguous_resend_failures_never_replay_the_job` now requires `Auth0ManagementUnavailable` instead of also accepting `httpx.HTTPStatusError`. Both failed against the previous helper with `httpx.HTTPStatusError`.
- **Missing "Live resend state" cases (section 10).** `test_missing_live_account_sends_no_job` covers an account deleted in Auth0: the lookup returns 404, the route returns 502, and only the token and lookup calls are made. `test_live_verification_state_wins_over_the_token_claim` covers both directions: an unverified token with a verified live account returns 200 with no job, and a verified token with an unverified live account returns 202 with one job. These pass on the previous code too, since the behavior was already correct; they keep it from regressing.
- **Document corrections.** Head and remote-ref statements now name `5c6fbd8` and the local correction commit. Statements that this plan is ignored under `.agents/` now record that `5c6fbd8` committed it to `docs/`. Sections 10 and 11 no longer place quota and hint tests in the unchanged `tests/common/test_ratelimit.py`. `backend/README.md` now links the two API notes with paths relative to `backend/`.

Measured on the working tree of that commit, from `backend/`, on 2026-10-28:

- `uv run ruff check .` passed. `ruff format --check` on the four changed Python files passed. `uv run mypy biosim_server tests` passed for 202 source files.
- `uv run python -m scripts.generate_openapi` produced no diff; the change adds no route or schema.
- The section 13 focused selection: **506 passed** (502 at `5c6fbd8`, plus the four new test cases). `tests/users`: **258 passed**.
- Full backend suite: **1,776 passed, 17 skipped**, in 129.86 seconds. That is the 1,772 at `5c6fbd8` plus the four new cases; the 17 skips are the same existing integration cases.

### Remaining work after this pass

- Phase 5: authoritative profile state, 202/200 UI handling, safe retry hints, explicit no-replay frontend behavior, profile tests/CI wiring, and browser acceptance. The existing frontend has not been migrated in this phase.
- Phase 6 remainder: frontend lint, typecheck, `test:profile`, and build; live Auth0 and browser acceptance; publication and review-thread closure. The backend documentation and local backend suite for this phase are committed in `887a91d`.
- Real tenant `read:users`/`update:users` grants, connection/template policy, actual delivery and redemption remain unverified. Quota capacity must be reviewed for the deployed worker/replica count.


## 1. Comparison and selections

The two source-plan columns below describe the earlier drafts at reconciliation time. Their statements about the old helper and parser are historical; the phase status sections above describe the committed state and remaining phases.

| Topic | Docs plan | Agents plan | Selection |
| --- | --- | --- | --- |
| Whether `main` is already merged | Accurate. Merge commit `114ccbd` exists. Parents are the original PR head `05b5370` and fetched `origin/main` `c7c95a4`. `origin/main` is an ancestor of HEAD. | Stale as a status report. It says the merge was only previewed and every checkbox starts open. The conflict inventory and resolution rules were right before that merge. | Treat the merge as done. Keep the agents resolution rules and the "merge again, do not rebase or force-push" rule for any later `main`. |
| Current HEAD | Stops at `114ccbd` plus the plan commit. | Stops at `05b5370`. | The pushed head is `5c6fbd8`, which adds this plan on top of `887a91d`; the review-correction commit follows it locally. `887a91d` carries the Phase 6 backend documentation on top of Phase 4's `267651c`; Phases 2 and 3 are committed in `e1809fb` on `dd850f0`, and Phase 1 fixes are committed in `dd850f0` on `4183086`. Earlier commit `3f7d1da` commits removal of the obsolete docs plan; `20b9eaa` merged remote feature `086c788` with `23a7434` (the docs plan on top of `114ccbd`). Phases 1–4 and the Phase 6 backend documentation are committed and locally verified. The last inspected `origin/main` is `c7c95a4`; the remote-tracking feature ref is `5c6fbd8`. |
| Email editing | Accurate to the merged code and to the September same-token review. `UpdateUserProfileRequest.email`, `update_auth0_user(..., email, verify_email)`, and the profile page's email form are all present. The remaining work (validation, server-owned `verify_email`, no replay of a mail-producing PATCH, 409, same-token GET) is the right closure for that code. | Proposes removing email editing and showing "not available here." That was a scope choice, not a description of the branch. It was not applied. Its warning is still real for a different bug: on `main` before this feature, an unknown `email` field is ignored, so an email-only PATCH succeeds as a no-op and the UI can announce a change that did not happen. After this merge, `email` is a real field, so that particular no-op is not the branch's behavior. | **Keep email editing and finish it.** Adopt the agents rule that an invalid email must not partially apply a valid name, and adopt `extra=forbid` so injected `roles`, `email_verified`, `password`, `connection`, and `user_id` are 422 rather than silently ignored. Do not delete the email field or replace the form with an unavailable message. |
| Resend HTTP contract | Matches today's route: `200` and `{"message": ...}`. The success string says the email was resent, which overclaims delivery. Quota is an optional follow-up. No live read before the job. | Stronger contract: live account read, `202` only after a valid 201, `200` with no job when already verified, no job id or address in the body, `Cache-Control: no-store`, dedicated quota, one job POST, and one 25-second budget around lookup and send together. | **Use the agents resend contract.** Add `outcome: "accepted" \| "already_verified"` and keep operation id `resend-verification-email` and the existing `message` key, so the response change is additive to today's body. The message must say the request was accepted, not that delivery was observed. Update the profile page in the same change. The docs "preserve 200 for compatibility" is the behavior being replaced. |
| Resend transport | Strong and accurate about the current defect: `resend_auth0_verification_email` still opens its own `httpx.AsyncClient`, passes `deadline` into `_auth_headers`, and does not use the pooled client, the 25-second operation budget, job-body validation, or typed 429 mapping. The password-ticket helper is the right single-attempt pattern. | Same transport requirements, plus a live GET that must share the 25-second budget, `follow_redirects=False`, and a finite `Retry-After` check. | Combine them. `resend_auth0_verification_email` owns the only deadline and performs both the live GET and job POST; the route never sees the deadline. Reserve one full 10-second HTTP phase for the non-replayable POST so the lookup cannot consume the entire budget. `_MGMT_OPERATION_DEADLINE_SECONDS` is **25.0**. `_MGMT_TOTAL_DEADLINE_SECONDS` on the generic retry loop is **15.0** and is a different constant. Do not describe the operation budget as 15 seconds. |
| `Retry-After` parsing | Says to fix a non-finite hint only if a new test exposes it. | Requires inspecting NaN and infinities. | The working-tree parser now rejects non-finite values with `math.isfinite`. Keep that fix. P1-D2 is fixed in `dd850f0`: `_send_with_retry` uses ceiling rounding with shared and real-helper regression coverage. Do not rewrite the limiter. |
| Tenant and provider tests | Strong on order (auth → configured Management → configured tenant → database account → provider call), foreign issuer, custom domain, and one real RS256 token through `get_current_user`. | Stronger composition: real router, real helpers, mocked JWKS, wrong audience, expiry, missing `exp`, and no `get_current_user` override in that suite. Also requires the live user id to match `sub` before a job. | Use both. Password reset's stricter issuer, subject, and recent-auth checks stay as they are. |
| Frontend | Accurate: the unverified banner and resend button key off `email_verified` only, so a social account can be offered resend; the page discards the PATCH body and can show the Auth0 SDK's stale profile. | Accurate that resend is called and email PATCH is submitted. Stronger on `retry: 0`, in-flight locking, no local "verified" flag, and a CI-wired test file. Its instruction to remove the email form follows the rejected scope cut. | Keep the email form. Make `GET /api/v1/me` authoritative for mutable account fields (`email`, `emailVerified`, `name`, and `provider`); retain the Auth0 SDK for session and identity. Apply `profile.provider === "auth0"` gating to email editing and every resend control, hide verification actions when `emailVerified` is null, and disable mutations with a read-only SDK fallback if `/me` fails. Apply the agents interaction rules (no automatic retry, no duplicate in-flight call, honest 202/200/429/503 copy). Add `test:profile` and wire it into `frontend-ci`. |
| Node version | Says use Node 22 because that is what the frontend guide names, and that Node 26.9.0 is not evidence. | Says use Node 24 because that is the CI matrix, and that the guide's Node 22 note is older. | Agents matches `.github/workflows/frontend-ci.yaml` (`node: [24]`). Run frontend gates on Node 24. |
| Validation evidence | Records focused 280 passed, ruff, mypy, OpenAPI generation, and a full-suite comparison against `c7c95a4`. Valuable, and explicitly not a green full suite. | Records older baselines only (26 tests on the original PR, 51 on main) and correctly says neither qualifies the reconciled branch. | Keep the docs measurements as historical evidence for the synchronization tree. Re-run them on the final head. The full-suite failure set matching unchanged `main` is a baseline comparison, not a waiver. |
| Publication | Push when authorized; fetch before approval; do not reset the branch. | Stronger: backup ref, ordinary fast-forward push, stop if rejected, do not force-push, do not `git clean`, preserve untracked `.agents/` and `.perch/`, update the PR title and body, reply to the September and October threads only when authorized. | Use the agents publication procedure. `.agents/` is gitignored (`.gitignore` line 19), and other `.agents/` notes stay untracked. This plan itself was committed at `docs/` in `5c6fbd8` and now ships with the feature; do not force-add anything from `.agents/`. |

Review links both plans rely on: [PR #111](https://github.com/biosimulations/platform/pull/111), [September review](https://github.com/biosimulations/platform/pull/111#pullrequestreview-5159527335), [addressed-items reply](https://github.com/biosimulations/platform/pull/111#issuecomment-5670149549), [October 5 integration comment](https://github.com/biosimulations/platform/pull/111#issuecomment-5997771190), [PR #119](https://github.com/biosimulations/platform/pull/119) merged at `679fe91`.

## 2. Baseline

| Item | State |
| --- | --- |
| Original PR head | `05b5370093a1363c264d1ea6725bad0fe1448fa8`. Preserve it as an ancestor. |
| Remote feature ref | `origin/feature/auth0-backend-verification` at `5c6fbd8` (2026-10-28). The review-correction commit is local until pushed. Refresh before publication. |
| `origin/main` | `c7c95a46dfe15e1e93e46d804084b598d6907d53` (merge of PR #159). Refresh again before implementation and before publication. |
| Merge base of the original PR | `6b997ba117607f027caaf329ea1ee195bc211ee9`. |
| Main merge | `114ccbda4d7c4722b3d0fb8337dea345f256e5a6`. |
| Local HEAD | The review-correction commit on top of `5c6fbd8`. Reconciliation, obsolete-plan removal, Phases 1–4, the Phase 6 backend documentation (`887a91d`), and this plan (`5c6fbd8`) are committed and pushed; the review corrections are not yet pushed. |
| Phases 1–4 and Phase 6 backend docs | Phase 1 fixes are committed in `dd850f0`. Phase 2 route/quota/privacy and Phase 3 strict PATCH/retry/dependency changes are committed together in `e1809fb`. Phase 4 composition tests are in `267651c`, and the Phase 6 backend documentation is in `887a91d`. |
| PR #119 | Tenant, pooled transport, password-reset, and error-classification contracts on `main` are the baseline the feature must keep. |
| Reuse candidate | Local `fix/user-verification-profile-opt-in` at `6e45ac20bcb9ba490c7178e617ac0a219b5a346b` exists. It is a mixed newsletter and Auth0 change. Read it for the resend patterns in section 5. Do not merge or cherry-pick it. |

The original feature diff against the old merge base is `.gitignore`, `backend/biosim_server/common/auth/auth0_management.py`, `backend/biosim_server/users/models.py`, `backend/biosim_server/users/router.py`, `backend/tests/common/test_auth0_management.py`, and `backend/tests/users/test_router.py`.

### Merge already performed

`114ccbd` is a merge, not a rebase. The following records what the merge retained in committed history; the Phase 1 status above describes subsequent changes committed in `4183086` and `dd850f0`:

| File | What the tree kept |
| --- | --- |
| `.gitignore` | `main`'s local-tool exclusions and the branch's machine-local exclusions. `.perch/` and `.agents/` stay ignored. |
| `auth0_management.py` | `main`'s pooled client, token single-flight and cooldown, operation deadlines, typed errors, retry machinery, and password-change ticket. `update_auth0_user` takes explicit `name`, `email`, and `verify_email`. `resend_auth0_verification_email` still uses a standalone client. |
| `users/models.py` | `email` on the profile request, fresh-email profile documentation, and `PasswordResetResponse`. |
| `users/router.py` | Configured-tenant checks, private password-reset responses, step-up policy, database-provider restrictions, fresh Auth0 email precedence, and the resend route behind `_require_configured_tenant` and `_require_database_account`. |
| `tests/users/test_router.py` | Both parents' regressions, reconciled during the merge. |

That merge fixed call signatures and route guards. It did not finish transport, validation, adversarial coverage, or the resend contract below. A later commit, `20b9eaa`, merged the remote feature's gitignore commits into this history. It did not close the four review items.

If `origin/main` advances before publication, merge it into this branch with `--no-ff`, resolve conflicts by inspecting both parents, and repeat the gates. Do not rebase, reset, or force-push.

## 3. What is still open

CONFIRMED in the committed helpers and route at `e1809fb` and `267651c`:

| Item | Present now | Still required |
| --- | --- | --- |
| 1. Resend transport | Committed code has pooling, one 25-second budget, live eligibility GET, 10-second POST reserve, single-attempt submission, typed outcomes/errors, encoded user paths, string job-status checks, ceiling-rounded lookup hints, and the slow-token reserve tests. | Phase 1 is committed in `dd850f0`; preserve these regressions through later changes. The review correction after `5c6fbd8` also types every non-201 job response (5xx as unavailable, redirects and other statuses as `Auth0ManagementError`); keep its regressions too. |
| 2. Configured tenant | Route guards and the committed composition suite (`267651c`) cover a real signed foreign token, custom-domain and domain-convention issuers, malformed database subjects, and authentication failures. Denied requests make no Management call. | Keep the suite green. A token with no `iss` is 401 at authentication; the issuerless 403 stays on the overridden-principal test. |
| 3. Email update | Phase 3 validates addresses, rejects explicit null and unknown fields, keeps verification controls server-owned, maps conflict/validation/throttle errors, and makes email-containing updates once while retaining name-only retry. Same-token continuity uses real helpers. | Retain tests through Phase 4–6; real tenant connection and verification behavior remain unverified until section 15. |
| 4. Throttling | PATCH and DELETE map `Auth0ManagementRateLimited` to 503 plus `Retry-After`. Resend now maps typed throttling to 503 with Retry-After, ineligible accounts to 400, and other helper errors to a sanitized 502; logs omit identity and exception content. | Retain Phase 2 privacy and throttling regressions. P1-D2 lookup rounding is closed. |

No row is closed because it type-checks or because a router mock passed.

## 4. Contracts

### 4.1 Profile routes that stay

| Operation | Contract |
| --- | --- |
| `GET /api/v1/me` (`get-current-user`) | JWT profile. Enrich only a configured-tenant principal. Fresh Auth0 email wins when present. JWT email remains the fallback when enrichment has no email or fails. A foreign principal gets the JWT profile and does not query the configured tenant's directory. |
| `PATCH /api/v1/me` (`update-current-user`) | Fields are `name` and `email` only. Unknown properties are **422**, including `roles`, `app_metadata`, `blocked`, `password`, `connection`, `user_id`, and caller-supplied `email_verified`. A name-only edit remains available for a configured-tenant social account. An email edit requires a configured-tenant database account. A request that mixes a valid name with an ineligible or invalid email performs no write. Omitted `email` means unchanged. Explicit `null`, empty, whitespace-only, or malformed email is 422 before any Auth0 call. Do not lowercase the local part. |
| `POST /api/v1/me/resend-verification` (`resend-verification-email`) | Authenticated. No body and no query, including `{}`. The user comes only from the verified issuer and subject. Configured-tenant primary database account. The caller cannot choose an address, user, identity, connection, tenant, application, organization, or redirect. |

`extra=forbid` is a deliberate change from `main`'s ignore-unknown behavior. Document it as such so reviewers do not treat the 422 as an accidental break. The Management helper signature stays keyword-only. The HTTP body is built from approved fields. Name-only callers still work.

Password reset is out of this change: its issuer and subject checks, recent-authentication gate, privacy headers, and tests stay as they are.

### 4.2 Resend results

Return a typed body with `outcome: "accepted" | "already_verified"` and the existing `message` key. Do not return an email address, job id, raw job body, or verification link. Apply `Cache-Control: no-store` on this route's responses, including validation and dependency errors. Reuse `_PrivacyHeadersRoute` the way password reset does, and confirm the wrapper covers the responses this route promises.

| Condition | HTTP / body | Outbound work |
| --- | --- | --- |
| Missing, invalid, or expired token | Existing 401 Bearer challenge | No Management call |
| JWKS temporarily unavailable | Existing authentication 503, including its `Retry-After` | No Management call |
| Body or query present | 422, generic validation text | No Management call. Do not buffer an unbounded body; stop at the first nonempty chunk. |
| Management credentials absent | Existing unconfigured 503 | No Management call |
| Foreign or missing issuer | 403 | No directory read and no job |
| Over the local verification quota | 429 and a positive integer `Retry-After` | No token, profile, or job call |
| Non-database subject, malformed `auth0\|` tail, extra delimiter, or M2M-style subject | 400, unsupported account | No Management call |
| Live record is a different user, or required fields are malformed | 502, generic dependency failure | No job |
| Live primary identity unsupported, or live address absent | 400 | The eligibility read only |
| Live database address already verified | 200, `outcome=already_verified` | Profile read only |
| Valid Auth0 201 | 202, `outcome=accepted`, message `Verification email request accepted. Check your inbox.` | Exactly one job POST |
| Token, profile, or job endpoint throttled | 503 and a positive integer `Retry-After` from the classified failure, including a shared token-cooldown remainder | No automatic second job POST |
| Other Auth0 error, missing account, malformed acknowledgment, timeout, or transport failure | 502, generic failure | No job retry, and no acceptance message |
| Caller cancelled | Cancellation propagates | No replacement attempt |

A later click by the user is a new request. It is not an automatic replay of an uncertain submission. The in-memory quota is per process: it resets on restart, bursts at a window boundary, and multiplies with replicas. It does not promise one pending job per account.

### 4.3 Provider operations

OFFICIAL, as cited by both plans:

- `POST https://{configured-domain}/api/v2/jobs/verification-email` with a server-selected `user_id` returns 201 when a job is created. Auth0 requires `identity` for primary social, enterprise, passwordless-email, and secondary identities. This PR stays database-primary-only and does not add that selector. [Verification-email job](https://auth0.com/docs/api/management/v2/jobs/post-verification-email). [Resend guidance](https://support.auth0.com/center/s/article/How-to-Resend-a-User-Verification-Email) names the `update:users` scope.
- `PATCH https://{configured-domain}/api/v2/users/{id}` accepts email and verification-control fields. Secondary-identity edits need a connection. This self-service route does not expose that targeting. [Update user](https://auth0.com/docs/api/management/v2/users/patch-users-by-id).

Deployment owners confirm `update:users` for the job and the email PATCH, and `read:users` for the live eligibility read, on the real M2M grant. Keep existing `delete:users` and password-reset grants. The verification template has to be enabled. Grants, connection policy, redirects, and delivery stay **UNVERIFIED** until the tenant check in section 15.

## 5. Resend transport

File: `backend/biosim_server/common/auth/auth0_management.py`.

1. `resend_auth0_verification_email(user_id)` owns the entire provider operation. It creates the only monotonic deadline from `_operation_deadline()` (`_MGMT_OPERATION_DEADLINE_SECONDS`, 25 seconds) before token acquisition, performs the live user lookup, and conditionally performs the job POST. The route's guards run before this helper is called; the route never sees or imports the private deadline machinery. Do not call public `get_auth0_user()` and then this helper, because two public helpers would create two budgets and permit about 50 seconds.
2. Add `_SIDE_EFFECT_RESERVE_SECONDS = _MGMT_HTTP_TIMEOUT_SECONDS` (10 seconds, one full HTTP phase). This is reserved within the existing 25-second budget, not added to it. The non-replayable POST must not inherit only a fraction of a second after a slow lookup and then time out after Auth0 may have accepted the job.
3. Headers come once from `_auth_headers(deadline=deadline)` and are used for both calls. Keep the existing token lock, cached failure, and cancellation behavior. Do not add another token cache. The client is `get_auth0_http_client()`; the helper must not open or close an `AsyncClient`, and shutdown remains its owner.
4. Extract the current user GET into a private `_get_user(client, headers, user_id, *, deadline)` used by both public `get_auth0_user()` and the resend helper. Public `get_auth0_user()` keeps its existing whole-operation deadline behavior for other callers. The resend helper calls `_get_user(..., deadline=deadline - _SIDE_EFFECT_RESERVE_SECONDS)`. The lookup may keep `main`'s bounded read retry, but it cannot spend the POST reserve.
5. A lookup failure sends no job. A lookup 429 remains `Auth0ManagementRateLimited` and becomes the route's 503 with `Retry-After`; other lookup failures become sanitized 502 responses. Validate the returned record before using it: its `user_id` must equal the verified subject, required fields must have the expected types, the primary identity must remain supported, and an address must be present. A live `email_verified is True` returns `ResendOutcome.ALREADY_VERIFIED` with zero job POSTs.
6. Before the job, compute `remaining = deadline - time.monotonic()`. If `remaining < _SIDE_EFFECT_RESERVE_SECONDS`, raise `Auth0ManagementUnavailable` and send nothing. This refusal is safe for the user to retry because no mail-producing request was made.
7. Body is `{"user_id": verified_subject}` on the configured tenant only. No `identity`. No caller-supplied client id. Add `AUTH0_VERIFICATION_CLIENT_ID` only if a reviewed application-specific link requires it, as server configuration, with tests. Do not reuse the password-reset client id by assumption.
8. The job POST does not use `_send_with_retry`. Make one POST with `follow_redirects=False`, wrapped in `asyncio.wait_for(..., timeout=remaining)`, with `_MGMT_HTTP_TIMEOUT_SECONDS` as the phase limit. The password-change ticket is the pattern: a timeout or connection loss after Auth0 accepted the job must not send a second job.
9. HTTP 429 raises `Auth0ManagementRateLimited(_rate_limit_hint(response))`. Timeout or transport failure raises a sanitized unavailable error. Cancellation propagates and does not POST again.
10. Success is HTTP 201 and a JSON object with a nonempty job id, `type=verification_email`, and a documented nonempty string status. Check the status type before set membership (P1-D1). Accept a documented pending or completed acknowledgment. Reject a reported failed job, an array/object status, a list or scalar body, invalid JSON, an empty id, and any other success status with `Auth0ManagementError`. Those are upstream failures, and they do not resubmit. Return `ResendOutcome.ACCEPTED` only after this validation; the route maps it to 202 and `outcome=accepted`.
11. Encode `user_id` as one path segment on the eligibility GET if the current helper does not. A reserved character in the subject must not change the path.
12. In `_retry_after_seconds`, retain the implemented rejection of non-finite values with `math.isfinite` before the ceiling. Use `math.ceil` rather than `int` for `_send_with_retry`'s exhausted lookup hint (P1-D2). Keep nonnegative validation, the positive minimum, and the existing fallback. Cover token cooldown, lookup, and job paths, including a lookup hint of 7.5 producing 8 rather than 7.

The candidate commit `6e45ac2` has a typed job model and a single-attempt sender worth reading. Copy only the hunks that match this contract. Do not take its newsletter code, its HTTPX logger suppression, or its generated OpenAPI file.

Acceptance: a database principal reaches the real helper; one pooled client and one token serve the lookup and job; the lookup cannot consume the 10-second POST reserve; a hanging lookup or a lookup that enters the reserve sends no job; an already-verified live record sends no job; an exhausted budget sends no job; a hanging job is cancelled inside the original 25-second budget; and an ambiguous outcome produces at most one job POST per call. Separate browser requests and other replicas are outside that promise.

## 6. Email updates

Files: `users/models.py`, `users/router.py`, and `auth0_management.py`, changed together.

1. Validate a supplied email as an email address and keep a public length bound. If the implementation uses Pydantic `EmailStr`, add `email-validator` explicitly and regenerate `backend/uv.lock` with `uv`. Do not depend on a transitive package.
2. Omitted email means unchanged. Explicit `null`, empty, whitespace-only, or malformed email is 422 before Auth0. Keep the current name and null behavior unless a test shows it must change.
3. The helper signature stays `update_auth0_user(user_id, *, name=None, email=None, verify_email=None)`. Do not restore `**fields`. Build the JSON from those fields.
4. When the address changes, the server sets `verify_email=True` and the unverified state. The request JSON cannot set verification. Confirm the outgoing fields against the official update API and, in section 15, against a disposable database account. A mock that auto-resets verification is not that confirmation.
5. Configured-tenant guard before any Management subject lookup. Database-account check before any write that includes email. A mixed name-plus-bad-email request writes nothing.
6. A definite duplicate address is a sanitized 409. A definite provider validation error is a stable client error. A credential or scope failure is an upstream 502 or 503, not the caller's tenant 403.
7. PATCH and GET rebuild the profile from Auth0 when enrichment succeeds, with JWT email only as the documented fallback. Do not mutate the authenticated principal. The same signed token still carries the old email claim; the GET shows the new address because Auth0's record wins. That is the September review's continuity check, and it stays in the suite.
8. `main` retries profile PATCH. An email PATCH with `verify_email=True` can send mail again on replay. Keep bounded retry for name-only edits. Email-changing edits use one attempt, still on the pooled client and the same deadline. Do not invent a provider idempotency key.
9. An empty PATCH makes no update call. A name-only social edit still works. Do not remove the tenant guard, and do not open the helper to arbitrary Management fields.

Acceptance: name-only, email-only, and combined edits hit the real helper with the right JSON; invalid input never reaches Auth0; a duplicate is distinguishable from downtime; one unchanged JWT sees the new address on PATCH then GET; an uncertain email-changing PATCH does not automatically PATCH again.

## 7. Tenant and provider authorization

Order for resend: authentication → reject unexpected input → Management configuration → configured-tenant eligibility → charge the verification quota → database-subject shape → one `resend_auth0_verification_email` call that owns the live read, conditional job, shared deadline, and POST reserve. No outbound work or deadline starts before the route guards pass.

A missing or invalid token stays 401 from the authentication dependency. An unconfigured Management API stays the existing 503 text.

Cases, in `tests/users/test_router.py` and the new verification modules in section 10:

- Configured tenant and a well-formed `auth0|...` subject can proceed.
- A second trusted issuer with the same subject is 403. Token acquisition, profile lookup, and job creation do not run.
- A missing issuer is 403. An issuer returned by `_configured_tenant_issuers` is accepted. An unrelated trusted issuer is not.
- Google, GitHub, enterprise/SAML, and passwordless-email subjects get the existing 400 for database-only email and resend operations, with no job and no partial profile write.
- An empty tail, a second `|`, or an `@clients` subject is not eligible just because the prefix is `auth0`. `_require_database_account` currently compares only the prefix from `split("|", 1)`. Tighten that check for these operations without weakening authentication elsewhere. Password reset already rejects those shapes; do not loosen it.
- Name-only edits for configured-tenant social principals still succeed.
- The security regression uses a real RS256 token from the foreign issuer through `get_current_user`, with the JWKS transport mocked. A hand-built principal does not replace that test. The composition suite also covers a valid local token, a bad signature, a wrong audience, an expired token, a missing `exp`, and a malformed Bearer header, without overriding `get_current_user`.

## 8. Failures, quota, and logs

Map `Auth0ManagementRateLimited` on the resend route before the generic handler, as PATCH and DELETE already do.

| Condition | Behavior |
| --- | --- |
| Missing or invalid access token | 401 from the existing dependency, including its challenge |
| Management not configured | 503, no outbound call |
| Foreign or missing issuer | 403, no directory or mutation |
| Unsupported provider, or a malformed database subject | 400, no partial write and no job |
| Malformed, blank, or explicit-null email; unknown PATCH field | 422 before outbound work |
| Definite duplicate email | Sanitized 409 |
| Auth0 resource or token endpoint throttled | 503 and a positive numeric `Retry-After` |
| Shared token-throttle cooldown | The same 503 class; the hint is the remaining cooldown |
| Local verification quota exhausted | 429 and `Retry-After`, before Auth0 |
| Timeout, transport error, malformed success, or provider configuration failure | Sanitized 502; no automatic replay of an uncertain mail-sending call |
| Job accepted | 202 as in section 4.2 |
| Already verified | 200 as in section 4.2 |

Resend logs may include an operation name, an error category, an HTTP status, and safe counts. They must not include the subject, the address, tokens, URLs, request or response bodies, or exception text that can carry those. The current `logger.error(f"... {user.sub}: {e}")` goes away. Inspect HTTPX logs as well; if redaction is required, keep it scoped so it does not change logging for other concurrent requests.

### Quota

In scope, patterned on `password_reset_rate_limit`. It does not replace the 503 mapping above.

| Variable | Default | Meaning |
| --- | --- | --- |
| `RATE_LIMIT_VERIFICATION_EMAIL_PER_WINDOW` | 3 | Admitted requests per principal, per window, per process |
| `RATE_LIMIT_VERIFICATION_EMAIL_WINDOW_SECONDS` | 600 | Fixed window |

`verification_email_rate_limit(request, user)` uses `_enforce_rate_limit`, a `verification-email` prefix, explicit window settings, and `scope_issuer=True`. Charge it after configuration and tenant checks, and before the database check and any Auth0 call. Do not refund an unsupported account, a no-op, or a failed attempt. Input, configuration, and foreign-issuer failures do not spend the bucket. A new token for the same issuer and subject does not reset it. `RATE_LIMIT_ENABLED=false` still disables this bucket with the others.

Show isolation from the workflow, compatibility, page, legacy, validation, and password-reset buckets. Document that the defaults are engineering choices, not a measured Auth0 capacity, and that effective capacity scales with workers.

## 9. Profile page and frontend profile state

Files: `frontend/app/pages/profile.vue`, `frontend/app/pages/verified.vue`, and a new `frontend/app/utils/profile-state.ts`. The email form stays. These are the changes:

- Make `GET /api/v1/me` authoritative for mutable account fields: `email`, `emailVerified`, `name`, and `provider`. The JSON field is `emailVerified`, not `email_verified`. The Auth0 SDK remains responsible for session and identity concerns: `isAuthenticated`, `sub`, avatar, and logout. Do not mix the SDK's stale mutable fields back into an authoritative loaded profile.
- Once the SDK reports an authenticated, ready session on mount, obtain the API token through the existing token-destination helper and load `/api/v1/me` into a `profile` ref. The existing Auth0 SPA `checkSession()` path uses the default token cache, so it is not a freshness mechanism for these fields; with local-storage caching, stale SDK data can survive a reload.
- After a successful name or email PATCH, replace `profile` with the typed PATCH response. No second GET is needed. Show the returned address and verification state, never success derived only from the address the form typed. Do not mutate the SDK user or its verified flag.
- Call resend with the existing API URL and token-destination helper. Set `retry: 0` and explicitly lock all resend entry points while one is in flight so a double click cannot send two jobs. Do not add a timer that retries a timeout, 502, or 503.
- Render 202 with `outcome=accepted` as accepted, check inbox. Render 200 with `outcome=already_verified` as already verified, then fetch `/api/v1/me` again and replace `profile`; do not merely set a local verified flag. Render 429 and 503 with the safe wait hint. Do not say that 202 delivered or redeemed a message.
- Decide UI eligibility with `profile.provider === "auth0"` for email editing and every resend entry point, including the unverified banner. A social account must not see resend. The backend remains the authority: render a 400 or 403 even if the page thought the user was eligible.
- Treat `emailVerified === null` as unknown because enrichment failed or is not configured. Hide the unverified banner and resend controls rather than showing "Verification Needed" or "Pending Verification" for an unknown state.
- If `/me` fails, show the available SDK values read-only with a "couldn't load latest" note. Disable name/email editing and resend; do not infer fresh eligibility or verification from stale SDK claims.
- Put the null/unknown, provider-eligibility, and read-only fallback decisions from the preceding three rules in small pure functions in `app/utils/profile-state.ts`, so they can be unit-tested with `node --test` in the same style as the verification utilities.
- A 422 from a stale client that posts an unknown field, or from an invalid address, must be visible. It must not be shown as a successful change.
- Optionally, after an email change, call `getAccessTokenSilently({ cacheMode: "off" })` in the background so navigation using SDK claims can catch up. This is not the profile page's source of truth. Whether the refreshed ID token contains the new email in the configured tenant is **UNVERIFIED** until section 15 checks it.
- Correct `verified.vue`'s assumption that `checkSession()` necessarily refreshes `email_verified`. Where that page displays current verification state, use `/api/v1/me`; do not describe a cached SDK refresh as proof of fresh verification.
- Add behavior and pure-state tests under `frontend/tests/` in the existing Node test style, and a `test:profile` script. `frontend-ci` runs named scripts; `test:auth` and `test:verification` will not discover the new file. Wire `npm run test:profile` into `.github/workflows/frontend-ci.yaml`.
- Manually exercise the page: keyboard use, disabled and loading state, database versus social controls, null/unknown verification, `/me` failure fallback, a successful edit's displayed address, and already-verified resend refresh. Record what was observed.

Acceptance: mutable account fields come from `/me` or its PATCH response; no path claims an email change or delivery that the response did not establish; null and failed-profile states fail closed to read-only/no resend; and an unsupported account is not offered email editing or resend.

## 10. Tests

Router mocks still map status codes. At least one test per feature calls the real `_auth_headers` and the real update or resend helper through `httpx.MockTransport`. An unrestricted `AsyncMock` for `_auth_headers` must not be the only request-shape test; that is what hid the missing `deadline` argument.

Use synthetic credentials. Reset `_reset_management_state()` and rate-limit state. Restore dependency overrides and close clients the test opened. Stub clocks. Do not sleep through backoff. Routine tests do not call a tenant or send mail.

| Group | Assertions | Location |
| --- | --- | --- |
| Resend success | Real token helper; one token and pooled client for lookup and job; domain, URLs, Bearer header, and `user_id`; no caller `identity` or address; 201 validated; helper returns accepted outcome and platform returns 202 with `outcome=accepted` | `tests/common/test_auth0_management.py` and the route module |
| Pool and lifecycle | The shared client is reused and not closed by the helper; application shutdown closes it | `tests/common/test_auth0_management_reliability.py` |
| Deadline | Shrink the deadline/reserve constants and stub clocks rather than sleeping. Lock wait and token acquisition stay bounded; a hanging lookup sends no POST and finishes inside 25 seconds; a lookup that runs into the reserve sends no POST; a slow token fetch either leaves the POST its full reserve or refuses before sending; an already-verified user gets zero POSTs; a hanging job stays inside the original budget | Same reliability module |
| Concurrency and cancellation | Concurrent cold calls share one token fetch; a refresh failure does not stampede token POSTs; cancel during token wait, profile read, and job POST starts no replacement POST and does not stick the lock | Same reliability module |
| Job side effects | Timeout, transport loss, 5xx, and a malformed success body produce one job POST | Same reliability module |
| Acknowledgment | Invalid JSON, non-object, wrong type, missing or empty id, failed state, array/object status (P1-D1), 200, 204, redirect; malformed acknowledgments raise the typed error after one job POST | `tests/common/test_auth0_management.py` |
| Update shape | Real helper; name-only, email-only and combined paths; only approved fields; verification flags are server-owned | `tests/users/test_profile_updates.py` |
| Update retry split | Name-only retries remain; an uncertain email-changing PATCH does not replay; shared deadline, cancellation and no PATCH after budget exhaustion | `tests/users/test_profile_updates.py` plus existing shared retry tests |
| Profile continuity | One real signed JWT with old email unchanged; real token/PATCH/GET helpers over mocked HTTP show the new provider address; original signed-token regression retained | `tests/users/test_profile_updates.py` and `tests/users/test_router.py` |
| Email rejection | Email-only and mixed requests with a bad email are 422 or 400 with no write; extra identity, role, and verification fields are 422 with no write; social name-only still works | `tests/users/test_profile_updates.py` and `tests/users/test_router.py` |
| Live resend state | Real helper validates live state: already verified returns `ResendOutcome.ALREADY_VERIFIED` and sends no job; stale token versus live verified and the reverse; returned user id mismatch; missing or non-boolean `email_verified`; absent address; missing account. Route maps outcomes to 200/202 without performing its own lookup | Management HTTP tests plus `tests/users/test_verification_email.py` |
| Authorization | Local, foreign, issuerless, custom-domain, supported and unsupported providers, malformed database ids, no outbound work when denied; real RS256 composition | `tests/users/test_verification_email.py` and `tests/users/test_verification_email_composition.py` |
| Throttle and quota | Local 429 before Auth0; token, profile, and job 429 become 503; absent, malformed, negative, fractional, NaN, infinite, and oversized hints; issuer plus subject key; token rotation does not reset; no-op and failure still consume the bucket; isolation from other budgets; global disable | `tests/users/test_verification_email.py` (quota, isolation, configuration, and route throttling) plus the hint tests in `tests/common/test_auth0_management.py` and `tests/common/test_auth0_management_retry.py`. `tests/common/test_ratelimit.py` is unchanged. |
| Privacy | `no-store` on success and on 400, 401, 403, 422, 429, 502, and 503; `WWW-Authenticate` and `Retry-After` preserved; logs omit addresses, subjects, credentials, and job bodies | Route tests |
| OpenAPI | Route, operation id, auth, empty body, `outcome` plus `message`, documented statuses and headers, artifact equality | `tests/api/test_openapi_endpoints.py` |
| Regression | GET, PATCH, DELETE, password reset, issuer, token, and existing rate-limit suites stay green | Existing suites |
| Frontend | `/me` is loaded into authoritative state and PATCH replaces it without another GET; already-verified resend triggers one GET; `emailVerified` null hides verification actions; provider gates email edit and every resend control; `/me` failure is read-only with a notice; eligible, ineligible, and loading states; accepted, already-verified, error, and throttle copy; retry disabled; double-click suppressed; no false email-change success; token destination; `verified.vue` does not rely on cached SDK freshness | `frontend/tests/`, including pure `profile-state` tests, script `test:profile` |

Where it is practical, show the new test failing on the old behavior for a specific reason: missing deadline, a foreign issuer reaching Management, a second POST after an ambiguous job, or a 422 that used to be an ignored field. A failure that is only a missing symbol does not count as that proof.

Provider-case names that already exist for database success and for Google, GitHub, SAML, and passwordless rejection should remain, or the PR body should name the replacement.

## 11. Files

| Path | Change |
| --- | --- |
| `backend/biosim_server/common/auth/auth0_management.py` | Pooled resend with helper-owned live lookup and conditional single-attempt job, one shared deadline, 10-second POST reserve, shared private `_get_user`, typed `ResendOutcome`, job validation, typed errors, finite `Retry-After`, email PATCH single-attempt split. |
| `backend/biosim_server/common/ratelimit.py`, `backend/biosim_server/config.py` | Verification-email bucket and settings only. |
| `backend/biosim_server/users/models.py` | Email validation, `extra=forbid`, resend response model with `outcome` and `message`, existing aliases and `PasswordResetResponse`. |
| `backend/biosim_server/users/router.py` | Tenant and provider gates, helper-outcome to 202/200 mapping, 503 on throttle, sanitized logs, no partial email write; no route-owned live lookup or deadline. |
| `backend/tests/common/test_auth0_management.py` | Real helper shape and acknowledgment failures. |
| `backend/tests/common/test_auth0_management_reliability.py` | Deadline, cancel, pool, cooldown. |
| `backend/tests/common/test_auth0_management_retry.py` | Name-only/shared retry regressions retained; email-changing replay coverage lives in `test_profile_updates.py`. |
| `backend/tests/common/test_ratelimit.py` | Unchanged. The verification-email quota's isolation and configuration tests are in `tests/users/test_verification_email.py`; `Retry-After` parsing tests are in `tests/common/test_auth0_management.py` and `tests/common/test_auth0_management_retry.py`. |
| `backend/tests/users/test_router.py` | Reconciled regressions plus the original continuity and rejection cases. |
| `backend/tests/users/test_profile_updates.py` | Real-helper strict-PATCH payloads, validation, typed errors, retry split, deadline/cancellation, provider guards, OpenAPI shape, and same-token continuity. |
| `backend/tests/users/test_verification_email.py` | Route cases. |
| `backend/tests/users/test_verification_email_composition.py` | Signed-token composition. |
| `backend/tests/api/test_openapi_endpoints.py` | Inventory and header assertions. |
| `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` | Regenerated by `python -m scripts.generate_openapi` after the final schema change. Do not hand-edit it and do not copy it from `6e45ac2`. |
| `backend/docs/auth0-tokens-claims-endpoints.md`, `backend/CLAUDE.md`, `backend/.env.example`, `backend/README.md`, `kustomize/README-config.md` | Endpoint, grants, quota, strict PATCH, unsupported providers, and enablement. Do not drop password-reset or verification-listing documentation. |
| `docs/profile-email-api.md`, `docs/verification-email-api.md` | Committed public request/response, validation, error, retry, and acceptance-boundary documentation for Phases 2 and 3. |
| `backend/pyproject.toml`, `backend/uv.lock` | Explicit `email-validator` runtime dependency and lock update for the adopted `EmailStr` validation. |
| `frontend/app/pages/profile.vue`, `frontend/app/pages/verified.vue`, `frontend/app/utils/profile-state.ts`, `frontend/tests/…`, `frontend/package.json`, `.github/workflows/frontend-ci.yaml` | Section 9: authoritative `/me` state, pure eligibility/fallback utilities, corrected cached-SDK assumptions, behavior tests, and CI wiring. |

Do not add newsletter enrollment, a new identity store, linked-account editing, a password-reset redesign, a generic retry framework, or a distributed queue. Do not change version files or deploy overlays in this PR.

## 12. Order of work

**Phase 0 is complete locally:** reconciliation and baseline checks passed; obsolete-plan deletion is committed. Refresh refs again before publication.

1. **Phase 1 complete and committed:** resend helper plus P1-D1, P1-D2 and P1-T1 in `dd850f0`.
2. **Phase 2 complete and committed in `e1809fb`:** quota, route eligibility, 202/200 mapping, privacy, signed-token and real-helper route tests, API documentation and OpenAPI. See the Phase 2 status and committed-head revalidation.
3. **Phase 3 complete and committed in `e1809fb`:** email validation, strict PATCH, typed errors, single-attempt email PATCH, name-only retry, and same-token real-helper continuity. Independently revalidated at the commit; see the Phase 3 status.
4. **Phase 4 complete and committed in `267651c`:** `tests/users/test_verification_email_composition.py`. No guard change. See the Phase 4 status.
5. Frontend behavior, `test:profile`, and the CI script.
6. **Phase 6 backend docs complete and committed in `887a91d` (pushed):** OpenAPI was regenerated with no diff. Ruff, mypy, and the full backend suite passed. Frontend checks, live acceptance, and review-thread closure remain.

Tests land in the same change as the behavior they cover. Keep `114ccbd` as the merge commit. Do not add a knowingly broken commit in order to keep phases separate.

## 13. Commands and recorded measurements

From `backend/`, on the final head:

```bash
uv sync --frozen --all-groups
uv run python -m pytest -q tests/users tests/common/test_auth0_management.py tests/common/test_auth0_management_reliability.py tests/common/test_auth0_management_retry.py tests/common/test_ratelimit.py tests/common/test_auth0_token_contract.py tests/api/test_openapi_endpoints.py
uv run python -m scripts.generate_openapi
uv run python -m pytest -q tests/api/test_openapi_endpoints.py
uv run ruff check .
uv run ruff format --check biosim_server/common/auth/auth0_management.py biosim_server/common/ratelimit.py biosim_server/config.py biosim_server/users tests/users tests/common/test_auth0_management.py tests/common/test_auth0_management_reliability.py tests/common/test_auth0_management_retry.py tests/common/test_ratelimit.py
uv run mypy biosim_server tests
uv run python -m pytest -m "not integration"
```

Extend the format check to every Python file this work changes. Compare any pre-existing lint or type failure with a clean `main` baseline. Do not reformat unrelated files.

`uv run python -m pytest` without the marker is the full suite. Backend CI (`.github/workflows/ci.yaml`) installs a pinned Temporal CLI and runs that suite. A local run that fails on Docker or a Temporal download is not a green full suite. Credential-dependent integration tests that cannot run locally stay listed, with CI or an external result. `not integration` does not close the full-suite gate. Testcontainers can still need Docker.

From `frontend/`, Node **24**:

```bash
npm ci
npm run test:auth
npm run test:verification
npm run test:profile
npm run lint
npm run typecheck
npm run build
```

`test:profile` does not exist until section 9 adds it. Do not treat a skipped script as a pass, and do not describe a build that did not run as passing.

From the repository root, after a fresh fetch:

```bash
git fetch origin
git merge-base --is-ancestor origin/main HEAD
git merge-base --is-ancestor 05b5370093a1363c264d1ea6725bad0fe1448fa8 HEAD
git diff --name-only --diff-filter=U
git diff --check origin/main...HEAD
git status --short --branch
```

Both ancestry checks must succeed, and the unmerged list must be empty.

### Historical synchronization measurements

These measurements predate the Phase 1 fixes. Later results are in the phase status sections: Phase 1 at `dd850f0` (1,630 passed, 17 skipped) and Phases 2 and 3 at `e1809fb` (1,751 passed, 17 skipped). The 1,616-test run predates the P1-D1 and P1-D2 fixes and does not describe the current head.

The docs plan measured these on the synchronization tree (around `114ccbd`), with the local interpreter. They are not evidence for `20b9eaa` or for a later head.

| Check | Recorded result |
| --- | --- |
| Focused suite on unchanged `c7c95a4` | 262 passed, same interpreter, users plus Management reliability, rate-limit, and API suites |
| Focused suite on the resolved tree | 280 passed |
| Ruff on the backend | Passed |
| Strict mypy | Passed, 199 source files |
| OpenAPI generation | Regenerated; contract equality passed in the focused suite |
| `git diff --check origin/main` | Passed for the feature diff. A comparison that includes an ancestor can still show a blank-line warning in `frontend/app/models/combine-api.ts` that already exists on `main` |
| Full backend suite on the resolved tree | 1 failed, 1,472 passed, 15 skipped, 8 deselected, 86 errors. The failure was an external simulator DNS error. Errors included Docker and Temporal-download fixtures |
| Same full suite on unchanged `c7c95a4` | 1 failed, 1,454 passed, 15 skipped, 8 deselected, 86 errors. Failing and errored nodes matched; the merged tree added 18 passes |
| Frontend, exact-head CI, live Auth0 | Frontend checks still not run locally. CI on the pushed heads `887a91d` and `5c6fbd8` passed (GitGuardian, Snyk, backend `build`, frontend `build`, `smoke`). Live Auth0 and browser acceptance remain not run. |

Do not change fixture policy to hide environment failures. Retain this table as provenance for the synchronization tree. Final-head CI is still a separate gate; the local 1,751-test run is evidence for `e1809fb`, and the 1,772-test run has since been reproduced at `267651c`, at `887a91d`, and at `5c6fbd8`. The review-correction commit's own counts are in the Phase 6 status. Neither substitutes for CI on a later commit.

## 14. Merge gates

Each gate needs a command, a URL, or a recorded observation tied to the final head and the `origin/main` that was current then. Historical counts in section 13 do not close them.

- [ ] **G1 — Implementation.** Sections 5–9 meet their acceptance criteria. The per-request resend client and the catch-all 502 are gone. Email validation, `extra=forbid`, and the email-PATCH retry split are in place.
- [ ] **G2 — Security.** Local, foreign, issuerless, and custom-domain cases pass, including a real signed foreign token. A denied request does not call the configured tenant. Malformed database subjects do not pass on prefix alone.
- [ ] **G3 — Compatibility.** Real-helper resend and email paths use the current signatures and payloads. PATCH then GET with one unchanged JWT shows the new address. Arbitrary fields do not reach Management.
- [ ] **G4 — Reliability.** P1-D1, P1-D2 and P1-T1 are closed with regressions. One helper-owned deadline, the 10-second POST reserve, cooldown, pooling, cancellation, typed 429, positive `Retry-After`, finite hint parsing, and no ambiguous replay all pass. A hanging/reserve-consuming lookup and an already-verified record produce zero job POSTs. Password reset and name-only retry stay intact. The verification quota is isolated.
- [ ] **G5 — Contracts.** OpenAPI inventory and the generated artifact match. `outcome` plus `message`, statuses, headers, the 202 limitation, the already-verified 200, and the strict PATCH are documented. Unrelated routes remain in the artifact.
- [ ] **G6 — Local verification.** Ruff and strict mypy pass. The full backend suite passes in a capable environment, with each skip named. Frontend lint, typecheck, `test:auth`, `test:verification`, `test:profile`, and build pass on Node 24.
- [ ] **G7 — Publication.** When authorized, push with an ordinary fast-forward: `git push origin HEAD:feature/auth0-backend-verification`. If it is rejected, fetch and reconcile. Do not force-push. Fast-forward the primary checkout only when its tracked files are clean. Leave untracked `.agents/` and `.perch/` alone. The PR head on GitHub matches the tested commit. Update the title and body so they describe resend, the `main` merge, the strict PATCH, and which September tests were kept. A removed email-edit capability is not part of this plan; the body must not describe one.
- [ ] **G8 — CI.** Backend, frontend (including `test:profile`), and smoke pass on that head, plus the security checks the repository actually requires. Backend CI runs pytest. A green backend workflow is not local ruff or mypy evidence; those are G6.
- [ ] **G9 — Fresh target.** Immediately before approval, `origin/main` is an ancestor of the tested head. GitHub reports the PR mergeable.
- [ ] **G10 — Review.** September and October threads, plus items 1–4, have current code and evidence. Reply and re-request review only when authorized. This document is not an approval. Resolve a thread only after its fix is on the head.
- [ ] **G11 — Provider and browser.** Section 15 is filled in, or an owner has written that those checks are a release gate after merge. A mock suite does not close G11.

Also record the final SHAs, CI URLs, skips, and accepted limits in `.agents/` after the work. That report is separate from this plan.

Optional review aid: run CodeRabbit against the recorded `main` SHA after confirming the diff has no secrets. A clean review does not replace G6, G8, or G11.

## 15. Tenant and browser acceptance

Separate merge readiness from production enablement. Mocked HTTP does not show that a tenant can deliver mail. This PR does not deploy itself.

An authorized operator, on a disposable database account and a test inbox:

1. Record issuer and domain, audience, the database connection, M2M grants for user read and the verification job, the enabled template, and the mail provider. No credentials in the notes.
2. Log in, read `/api/v1/me`, and hold the same access token for the next step.
3. PATCH to another controlled inbox. The PATCH body and a GET with that same token show the new address and the unverified state. The token's email claim is still the old address.
4. Confirm one verification message for that address change. Separately call resend, record API acceptance, inbox arrival, and link redemption as three observations. A later resend on the verified account returns `outcome=already_verified` and does not create another job; the page then refreshes `/me` rather than setting a local SDK verified flag.
5. Sign out and back in. Confirm the profile page loads mutable fields from `/me`, a successful PATCH updates them without a second GET, null `emailVerified` hides verification actions, a simulated `/me` failure produces the read-only fallback, and social-account email/resend controls are hidden. Name-only behavior on that social account follows the connection. If the optional `cacheMode: "off"` background refresh is implemented, inspect the refreshed ID token and record whether this tenant includes the new email; do not claim that navbar freshness without this observation. Cross-tenant collisions stay in the local signed-token tests.
6. Throttling and transport failures stay on the local mock unless fault injection on a staging tenant is explicitly approved. The UI must honor `Retry-After` and must not replay an ambiguous email-sending call by itself.
7. Confirm a stale frontend that still posts an unknown field receives 422 rather than a false success, if that client still exists during rollout. Confirm the new page treats a missing resend route as unavailable.
8. Restore or retire the disposable account. Record the date, environment, commit, operator, and the three mail observations. Leave tokens, ticket URLs, and message contents out of the repository.

Set the quota for the real worker count. Overlay and image bumps are a later GitOps change: publish the image before the overlay moves. A code revert does not unsend mail or undo an address Auth0 already stored.

## 16. Completion

- [x] **Phase 0 reconciliation:** current remote main `c7c95a4` and original PR head `05b5370` are ancestors of `887a91d` and again of `5c6fbd8` (rechecked 2026-10-28); no unmerged paths; `git diff --check origin/main...HEAD` passes. Obsolete-plan deletion is committed. Recheck main again before publication and approval.
- [x] **Phase 2 local regression gate:** password reset, deletion, name-only social edits, and existing quotas pass the full backend suite. Repeat after later phases.
- [x] **Phase 1 helper closure, committed:** P1-D1/P1-D2/P1-T1 verified in `dd850f0`; 345 focused and 1,630 full-suite tests passed, 17 skipped.
- [x] **Phase 2 local:** Configured-tenant and primary-database checks fail closed before a job.
- [x] **Phase 2 local:** Verification quota and Auth0 503 mapping pass, including a non-finite `Retry-After`.
- [x] **Phase 2 local:** 202 `outcome=accepted`, already-verified 200 `outcome=already_verified`, `message`, job validation, and `no-store` match OpenAPI.
- [x] **Phase 3 committed in `e1809fb`:** database email editing, `EmailStr` validation, explicit-null and extra-field 422s, sanitized 409, provider-validation 422, and one-attempt email PATCH pass the real-helper tests and the 1,751-test full suite. Verification is recorded in the Phase 3 section.
- [x] **Phase 3 committed in `e1809fb`:** the original unchanged-JWT regression in `tests/users/test_router.py` is retained. `test_same_signed_token_reads_new_email_through_real_helpers` uses one signed JWT with the real token, update, and read helpers.
- [x] **Phase 4 committed in `267651c`:** signed-token composition without a `get_current_user` override covers custom-domain acceptance, foreign same-subject denial, malformed and non-database subjects, and rejected credentials. 21 new tests passed; 255 in `tests/users`; 1,772 in the full suite with 17 skipped; Ruff and mypy passed at that commit.
- [ ] Profile state: `/me` owns mutable fields; PATCH replaces state without another GET; already-verified resend refreshes `/me`; null and failed-profile states fail closed; database-only email/resend controls, honest copy, `retry: 0`, and no false success pass. `verified.vue` no longer assumes cached SDK freshness. `test:profile` runs in frontend CI and the pages were exercised in a browser.
- [x] **Phase 6 backend docs, committed in `887a91d` (pushed):** OpenAPI already matches the app (regeneration produced no diff). `.env.example`, `backend/CLAUDE.md`, the token guide, the kustomize config guide, and `backend/README.md` now name the resend contract, `read:users`, and the verification-email quota. Ruff, mypy, and 1,772 passed / 17 skipped are recorded in the Phase 6 status. No version bump.
- [x] **Review corrections after `5c6fbd8`, committed locally:** non-201 job responses are typed (5xx unavailable; redirects and others `Auth0ManagementError`); the redirect, missing-account, and token-versus-live-state regressions are added; this plan and `backend/README.md` are corrected. Local results are in the Phase 6 status. Not yet pushed, so no CI evidence for that commit.
- [ ] G6 frontend half: lint, typecheck, `test:profile`, and build on Node 24. Backend ruff, mypy, and the full suite passed locally. The frontend CI job is green, but it does not yet run `test:profile`, because that script arrives with Phase 5.
- [x] **G8 — CI, backend and current frontend:** on pushed head `887a91d`, GitGuardian, Snyk, backend `build`, frontend `build`, and `smoke` all passed, and passed again on `5c6fbd8`. The review-correction commit needs the same checks once it is pushed. The frontend job does not yet run `test:profile`; that script arrives with Phase 5, so this does not close the Phase 5 clause.
- [ ] G7 remainder, G9, and G10: the branch is pushed through `5c6fbd8`; the review-correction commit still needs an authorized push. Refreshing `origin/main`, updating the PR title and body (including an accurate description of `5c6fbd8`), and closing review threads still need authorization.
- [ ] Section 15 is evidenced or explicitly left unverified with an owner.

The plan is fulfilled when those gates have evidence for the final head. Phases 0–4 are committed through `267651c`. The Phase 6 backend documentation is complete and committed in `887a91d`. `5c6fbd8` added this plan and is the pushed PR head, with CI green there. The review corrections after `5c6fbd8` are committed locally and not yet pushed. Phase 5, the frontend half of the local gates, and live Auth0 acceptance remain open.
