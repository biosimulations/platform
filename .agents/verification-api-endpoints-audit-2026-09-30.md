# Verification API implementation audit — 2026-09-30

> Follow-up: both P2 findings below are now fixed. This is the historical audit; see the execution report’s P2 corrections section for implementation and regression evidence.

## Original audit verdict

All ten implementation areas are present, including the later shared submission helper, early service checks, and query-time history-expiry fallback. However, **100% correctness/completion cannot be confirmed**: two reproducible failure paths invalidate the plan's unconditional guarantees. The earlier completion report was too strong.

Audited `chore/verify-workflow-id`, HEAD `d9559a0`, including staged, unstaged and untracked implementation files. Production code and the existing plan/checklist were not changed during this audit. This report is the only new repository artifact.

## Findings

### P2 — terminal executions can still return 503 indefinitely

Location: `backend/biosim_server/api/main.py:538–549` (query exception handling, before status reconciliation).

Reproduction against an actual ephemeral local Temporal server:

1. Start the real `RunsVerifyWorkflow` on a task queue with no worker.
2. Terminate it before its first workflow task starts.
3. `describe()` correctly reports `TERMINATED`.
4. Call `get_verify_output` with an authenticated user and the real Temporal client.
5. Temporal rejects the query with `Workflow execution closed before WorkflowTaskStarted event`; the API returns **503, Temporal service unavailable**.

The workflow has retained history and Temporal is healthy. The handler never reaches `_reconcile_terminal_status` because it requires a successful query first. This directly contradicts §13's checked terminal-state criterion and can also violate its retained-listed-ID criterion for a normal API-created workflow terminated before a worker picks it up. Pollers are told to retry a permanent state.

The current parametrized terminal-state tests always supply a successful mocked query. The real Temporal tests cover failure after worker initialization, not this case.

Required follow-up: support terminal-state responses when no queryable workflow state exists. Preserve authorization using durable ownership and recover/persist enough original input to construct the unchanged response contract. Add a real Temporal regression for termination before the first workflow task. Do not bypass owner checks to manufacture a response.

### P2 — an ambiguous start failure deletes a running workflow's ledger row

Location: `backend/biosim_server/api/main.py:434–442` (`_persist_and_start_verify`).

Every exception from `start_workflow` triggers ledger deletion. An RPC error does not establish that the server rejected the start: the server may have accepted the workflow and lost the reply.

Controlled fault-injection reproduction:

1. Use an actual ephemeral local Temporal server and the real `RunsVerifyWorkflow`.
2. Supply a start callable that awaits the real successful start, then raises `RPCError(UNAVAILABLE)` to simulate loss of the response.
3. Run it through `_persist_and_start_verify` with a mocked ledger.
4. Observed: HTTP **503**; real Temporal `describe()` reports **RUNNING**; `ledger.delete_verification(workflow_id)` was awaited.

This removes the only listing record for work that really started, so the user cannot discover it via `/verification_ids`; a retry may create duplicate work. This is distinct from the accepted D5a cleanup-failure orphan. Here cleanup succeeds and loses the row for an existing execution.

The implementation follows Step 4 literally; the plan's assumption that any start exception means no workflow exists needs correction too. Required follow-up: distinguish definitive rejection from ambiguous transport failure, reconcile using the same workflow ID, and retain the ledger record while the outcome is unknown. Add a regression for accepted start plus lost reply.

## Requirement coverage

| Steps | Audit result |
| --- | --- |
| 1–3: models, Mongo ledger, DI | Present; database tests pass; startup indexes and shutdown reset wired. |
| 4: POST persistence/start | Present; failure handling has the ambiguous-outcome defect above. |
| 5: GET hardening | Present; pre-initialization terminal execution defect above. |
| 6–7: ID listing, auth, tests | Present; owner/admin and deterministic listing tests pass. |
| 8: missing-dataset tolerance | Present; activity tests pass for disjoint, overlapping and identical data. |
| 9: metadata preflight | Present; cache preference, missing metadata, dataset-label intersection and D7 validation implemented. Dataset overlap is not proof of identical models, as the plan explicitly acknowledges. |
| 10: docs/OpenAPI | Present and consistent with runtime; original workflow DTO schemas preserved. |

The plan's complete acceptance checklist should not be treated as conclusive until the findings above are resolved. D7 remains validation-only, not observable filtering, as explicitly documented in the amended plan. Backfill, result persistence after retention, and pagination remain deliberate exclusions.

## Fresh validation

- `uv run ruff check .`: passed.
- `uv run mypy biosim_server tests`: passed, strict configuration, 172 files.
- `uv run pytest -m "not integration"`: **888 passed, 15 skipped, 8 deselected**, 108.07 seconds; exit 0.
- `git diff --check`: passed.
- Compared `VerifyWorkflowOutput` and `VerifyWorkflowStatus` schemas generated from HEAD source and working-tree source: identical.
- Checked generated YAML against runtime `app.openapi()`: identical.
- CodeRabbit 0.8.2 review, `--agent --uncommitted --include-untracked --fresh`: **review_completed**, 22 files reviewed, zero findings. This does not override the directly reproduced issues above.

The two reproductions were ad hoc audit probes; no regression tests were added to the repository. The terminal-query failure used a real Temporal response. The lost-start-reply failure was deliberately injected after a real server-accepted start; ledger deletion was observed via an AsyncMock. No production workflows were created, terminated or changed.

Session logs: `/tmp/platform-verification-audit-tests.log`, `/tmp/platform-verification-coderabbit-audit.ndjson`.

## Unverified operational scope

Fifteen credential-dependent tests remained skipped; eight integration tests were excluded by the requested test marker. Release/version bump, image publishing and deployment have not been executed. GKE retention was verified in the prior execution report, not rechecked during this audit; RKE retention remains unverified. These limits must remain separate from local implementation coverage.
