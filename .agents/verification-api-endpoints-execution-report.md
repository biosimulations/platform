# Verification API execution report

Date: 2026-09-29. Scope: all ten repository implementation steps, including compatibility steps 8–9 and decisions D7/D9.

## Starting state

The worktree already contained most production changes for steps 1–10 and partial tests. These changes were preserved and completed. The initial plan still said “plan only”. Initial lint/type checks passed, but the full suite exposed unregistered Mongo fixtures, stale Temporal mocks, and a `mongo:latest` startup failure on this host's Linux kernel.

## Step ledger

| Step | Final implementation and evidence |
| --- | --- |
| 1 | `biosim_verify/models.py`: additive ledger/type/list DTOs. Original workflow output/status models preserved. |
| 2 | `biosim_verify/database.py`: unique workflow ID, owner filtering, deterministic newest-first ordering, lookup/delete/index lifecycle; real Mongo tests. |
| 3 | `dependencies.py`: lazy import, shared Motor client, startup indexes, setter/getter, shutdown reset; registered test fixture. |
| 4 | Both POST handlers insert before Temporal start, sanitize errors and delete rows only after definitive invalid-request rejection; ambiguous outcomes retain the row and reconcile the same ID; unavailable Temporal returns 503 before persistence. Tests cover both handlers, cleanup failure, and real Mongo insert-before-start/listing. |
| 5 | Describe/type check, owner/admin check, terminal failure reconciliation, sanitized query/describe errors, owner-aware expired-ID fallback. Query timeouts now return 503. Real Temporal completed/failed/missing-run round trips pass. |
| 6 | `/verification_ids` returns owner-scoped IDs or all IDs for admins, with empty/single/multiple responses and sanitized 503s. |
| 7 | OpenAPI inventories/auth probes registered. Database fixture registered. Original ownership test mocks updated to include describe. |
| 8 | Missing datasets yield error cells instead of dictionary exceptions. Activity tests exercise disjoint, partially shared and identical datasets with real comparison math. Existing valid diagonal self-comparisons remain successful. |
| 9 | Cache-first typed HDF5 metadata loading, unavailable metadata skipped, disjoint/label mismatch rejection before writes, observable intersection validation. Repeated IDs fetch metadata once. |
| 10 | Backend guide, overview, auth contract, workflow architecture and generated OpenAPI synchronized; compatibility notes below. |

## Decisions adopted

D1–D6: Mongo ledger; ~~owner-scoped/admin-all visibility~~ **public listing of every ID (D2 revised 2026-09-30; see below)**; wrapper response; no pagination; expired IDs remain 404 with distinct owner/admin detail; rare cleanup-failure orphan accepted; no model identity/warnings fields.

D7: reject when none of the requested observables are in common dataset labels (partial observable overlap remains allowed, as specified in Step 9). `observables` is validated only; no workflow or activity reads it, so it does not filter results. The plan's original D7 rationale assumed otherwise and has been corrected (§14).

D8: backfill and retained result persistence remain explicitly outside core scope.

D9: reject `/` in either POST's workflow prefix with documented query validation (422). Encoded slash in a GET path still returns routing 404.

## Test coverage mapping

| Plan cases | Evidence |
| --- | --- |
| 1–6 | `tests/biosim_verify/test_verification_database.py`; real Mongo, including mixed verification types and unique index. |
| 7–9 | `test_verify_status_reconcile.py` plus parametrized API execution-status tests. |
| 10–16 | `tests/api/test_main.py`; real single-ID POST/list round trips in `test_verification_completion.py`; meta-test auth inventory. |
| 17–26 | Both API test files; real Temporal completed/failed/missing output queries; explicit retained/expired ownership, timeout, service outage, wrong type, and encoded slash cases. |
| 27–30 | Both POSTs covered for successful persistence order, insert/start/cleanup failures, absent ledger and absent Temporal; real Mongo-backed listing. |
| 31–35 | `test_compatibility.py`. |
| 36–38 | `test_generate_statistics.py`, using Temporal ActivityEnvironment, mocked downloads and real comparison calculations. |
| 39–43 | API tests cover early rejection, overlap, one missing run out of two/three, cache hit, observables and no side effects on rejection. |

External GCS-backed verification tests were updated to wire the ledger and assert listing. They remain credential-dependent. New local integration tests use an actual Temporal server, actual Mongo, the real RunsVerifyWorkflow and real statistics activity, with external run retrieval/download stubbed.

## Validation

Final results on the completed source:

- `uv run ruff check .` — passed.
- `uv run mypy biosim_server tests` — passed, 172 source files (strict configuration).
- `uv run pytest -m "not integration"` — **883 passed, 15 skipped, 8 deselected**, 109.63 seconds; exit status 0. Includes real Mongo, local Temporal and Keycloak container tests.
- Focused verification/activity/local-integration run — **67 passed, 4 credential-dependent skips**.
- `git diff --check` — passed.
- Generated OpenAPI equality and before/after source schema comparison — passed.

The 15 full-suite skips require GCS or SSH/Slurm credentials; the 8 deselections follow the requested non-integration marker. These are not claimed as passed. Existing library deprecation warnings remain (3,747 warnings). Full final test output is available for this session at `/tmp/platform-verification-confirmed.log`.

Schema comparison: schemas regenerated from `HEAD`'s model source and final model source are byte-identical for `VerifyWorkflowOutput` and `VerifyWorkflowStatus`. The checked-in baseline spec was stale: it omitted `owner_sub`, which was already present in baseline source. Regeneration corrects that discrepancy and reflects the current default-disabled demo router. The generated YAML equals `app.openapi()`.

Mongo fixture: pinned to `mongo:7`, matching `compose.yaml`. The previous floating `mongo:latest` exited with an explicit kernel-incompatibility message; the real Mongo tests now run successfully.

## Post-audit corrections (2026-09-30)

An independent audit found the implementation functionally complete, with four gaps against the plan's wording. All are fixed:

| Finding | Fix | Evidence |
| --- | --- | --- |
| D7 rationale was wrong (`observables` is never consumed) | D7 kept as validation-only. Plan §14 / Step 9, `docs/workflows-architecture.md` and the `POST /verify/runs` `observables` description corrected; OpenAPI spec regenerated. | `test_preflight_requested_observables` |
| Insert/start/cleanup code duplicated in both POSTs, against Step 4 | Extracted `_require_verification_ledger()` and `_persist_and_start_verify(...)` beside `_VerifyOwnership`. | existing POST ledger tests, unchanged |
| OMEX handler checked the ledger after the upload | Both handlers check the ledger right after the Temporal client, before the upload / preflight. | `test_missing_ledger_rejects_before_upload_or_upstream_calls` (runs + omex) |
| `_ledger_fallback_404` typed as returning output; unnecessary local import; query NOT_FOUND after describe → 503 | Return type is `NoReturn`; `find_common_datasets` imported at module level; query NOT_FOUND now uses the ledger fallback (404). | `test_history_purged_between_describe_and_query_is_404` (owner / other owner / legacy row) |

`test_verify_omex_unknown_simulator` now stubs the ledger, because the early check would otherwise return 503 first. It also asserts that nothing is persisted or started on the 400. The `mongo:7` test-container pin is an intentional out-of-plan change, matching `compose.yaml`.

Re-validation after the corrections: `ruff` passed; `mypy` (strict, 172 files) passed; `pytest -m "not integration"` gave **888 passed, 15 skipped, 8 deselected** (up from 883 because of the 5 new cases); `git diff --check` was clean. The regenerated OpenAPI spec differs only in the `observables` description on `verify-runs`.

## Operational verification and compatibility notes

Read-only GKE check: context `gke_biosimulations_us-central1-c_biosimulations`, namespace `temporal`, existing admin-tools pod. `temporal operator namespace describe default` returned retention `24h0m0s`, history and visibility archival disabled. RKE was not inspected.

Client-visible changes: Temporal outages now return 503; failed Temporal executions no longer poll indefinitely as IN_PROGRESS; POST requires the Mongo ledger; incompatible run outputs/observables return 400; invalid workflow prefixes return 422. Ledger records survive Temporal retention, but results do not. Legacy workflows are retrievable under existing ownership rules but not backfilled into the listing. Dataset overlap does not prove model identity.

The recommended release-PR boundary in §12 is operational follow-up, not a repository acceptance test: version bump/tag, image publication and deploy PRs have not been executed. Both API and worker images need rebuilding because the statistics activity changed. Workflow definitions and payload contracts were not changed. No commits, pushes, tags or deployments were made.

## P2 corrections implemented (2026-09-30)

Both findings in `verification-api-endpoints-audit-2026-09-30.md` have now been addressed:

1. Unqueryable terminal workflows: read the first retained history event using the configured data converter, bound to the Run ID from describe. Recover original comparison settings and owner; enforce owner/admin authorization (including legacy ownerless compatibility); return FAILED with a sanitized terminal-status message. This avoids inventing settings and changes neither Temporal workflow definitions nor published DTOs.
2. Lost start response: delete only a definitive INVALID_ARGUMENT rejection. For uncertain outcomes, describe the same ID and compare the original input and owner. Return the existing handle when confirmed, otherwise retain the ledger row and return sanitized 503 with the ID for polling. No second start request is issued. A follow-up NOT_FOUND is treated as uncertain because the original request may still complete.

Regressions cover both POST types with real Mongo and Temporal, lost replies injected after server acceptance, termination before the first task, original settings, stranger denial/admin access after ledger deletion, legacy ownerless history, all four failed terminal states, history unavailability/purge/malformed payload, ambiguous follow-up NOT_FOUND and UNAVAILABLE, and mismatched recovered input.

Final validation after both P2 fixes:

- Ruff passed.
- Strict mypy passed (172 files).
- Full `uv run pytest -m "not integration"`: **903 passed, 15 skipped, 8 deselected**, 107.06 seconds, exit 0.
- Focused verification API regressions: **59 passed**.
- `VerifyWorkflowOutput` and `VerifyWorkflowStatus` schemas remain identical to HEAD-source schemas; generated OpenAPI still equals runtime OpenAPI. No regeneration was necessary because neither response schemas nor route parameters changed.
- `git diff --check` passed.

Full run: `/tmp/verification-fixes-full.log`; focused run: `/tmp/verification-fixes-regressions.log`. The existing credential-dependent skips and release/deployment boundaries still apply.

Post-fix CodeRabbit review completed across all 23 changed/untracked files. The first pass reported one minor validation-placeholder issue, now corrected. Follow-up review completed with **zero findings** (`/tmp/verification-fixes-review-followup.ndjson`).

## D2 revised: public `GET /verification_ids` (2026-09-30)

Owner decision: the legacy API has no authentication, so the listing takes no token and returns every verification ID. Any token sent is ignored.

- `main.py`: `list_verification_ids` has no `get_current_user` dependency and no 401 response, and always lists unfiltered. The `VerificationIdsResponse` description and the generated OpenAPI were updated; `security` and 401 were removed from the operation.
- Meta-test: `list-verification-ids` is `AuthMode.NONE` with an anonymous probe (ledger `None` → 503).
- Tests: anonymous caller gets every ID (multiple, single and empty); a sent token, valid or invalid, is not validated or used for scoping; 503 paths are unchanged.
- Docs: `backend/CLAUDE.md`, `backend/docs/auth0-tokens-claims-endpoints.md`, `docs/workflows-architecture.md` and the plan (D2 in §14, §3, Step 6, Step 7, §5, cases 13–15, §11, §13).
- Unchanged: `GET /verify/{workflow_id}` and both POSTs still require a token. Results remain owner-or-admin. The ledger's `owner_sub` filter parameter is kept, so scoping can be restored without a schema change.
- Exposure: every ID, including its caller-chosen `workflow_id_prefix`, and the total verification count are now public.

## D10: anonymous `/verify/*` (2026-09-30)

Owner decision: the endpoints serve the legacy API, which has no authentication, so none of them require a token.

- `main.py`: `verify_omex`, `verify_runs` and `get_verify_output` depend on `get_optional_user`. With no token, `owner_sub = None`, so the verification is ownerless and an uploaded OMEX is stored `public` through the existing `_visibility_for_owner`. The new `_authorize_verification_read` makes ownerless verifications public, and owned ones owner-or-admin (anonymous 401, other user 403). It is used on the query path and the terminal-recovery path; the ledger fallback applies the same rule. `get_optional_user` still rejects an invalid token with 401. Rate limiting is unchanged. The OpenAPI responses gained 401 descriptions; the operations keep the optional `HTTPBearer` security entry.
- Tests: the meta-test moves the three operations to `AuthMode.OPTIONAL`, with anonymous probes. The old "requires authentication" tests were replaced by anonymous-start tests (OMEX stored public/ownerless; runs ownerless), an anonymous read-ownerless-only test (200 vs 401), and an invalid-token-is-401 test for both POSTs. The real-Mongo POST/list/GET round trip is parametrized for anonymous callers. Tests that authenticate now override `get_optional_user` as well. The GCS integration tests use a local `authenticated_verify_user` fixture; the shared `authenticated_user` fixture is unchanged.
- Docs: `backend/CLAUDE.md`, `backend/docs/auth0-tokens-claims-endpoints.md`, the `VerificationRecord` comment, and the plan (D10 in §14, D2 notes, §13).

