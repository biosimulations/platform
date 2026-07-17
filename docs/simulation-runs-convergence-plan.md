# Simulation Runs — Convergence Plan

How to converge the two parallel "run one simulation on biosimulations.org"
implementations and unify their storage, evolving from the first-cut
`POST /simulations/runs` feature (see `simulation-runs-api-plan.md`).

## Background: what's duplicated today

There are two implementations of "run one simulation on biosimulations.org and
persist it":

| | verify path (pre-existing) | run path (new) |
|---|---|---|
| Orchestrator | `OmexVerifyWorkflow` → N **child** `OmexSimWorkflow` | `SimulationRunWorkflow` → N inline activity-pairs |
| Per-run unit | `submit_biosim_simulation_run_activity` (monolithic: cache-check → submit → poll → fetch HDF5 → save) | `submit_simulation_activity` + `poll_simulation_activity` (same logic, split in two) |
| Persists | `BiosimulatorWorkflowRun` → `BiosimSims` | `BiosimulatorWorkflowRun` → `BiosimSims` (**same**) + `SimulationRunRecord` |
| `cache_buster` | plumbed end-to-end, exposed as a `/verify/omex` query param | **hardcoded `"0"`** in the router |

The poll loop, the HDF5-404-retry, the cache lookup, and the save are written
twice and already drifting. The two paths also already share the `BiosimSims`
cache by `(file_hash, image_digest, cache_buster)` — an accidental coupling that
should be made intentional.

The records relate as a clean **many-submissions-to-one-computation**:
`BiosimulatorWorkflowRun` is the dedup'd computation + artifacts; a submission is
one per user action (carries `email`, user-chosen `name`, `purpose`, timestamps,
display status) and survives cache hits. Two entities is correct; the smell in
the first cut is that `SimulationRunRecord` *denormalized the computation columns*
(simulator digest, cpus, sizes, runtime, …) onto every submission row.

## Invariants (hold across every PR)

- **The `SimulationRun` API DTO shape is frozen** — it is the frontend contract
  (`frontend/app/models/simulators.ts`). Every PR keeps the JSON identical. A
  golden-response regression test is added in PR0 and kept green throughout.
- **The verify path (`/verify/omex`, `/verify/runs`) must stay green** — the
  production risk surface, touched only by PR2. `tests/biosim_verify/` is the net.
- **No prod data in `BiosimSimulationRuns`** — the runs-api work is not yet
  merged/deployed, so PR3's schema change needs **no backfill**. If that stops
  being true before PR3, add a migration.
- Each PR passes the full `uv run pytest -m "not integration"` gate.

## Recommended ship order

The safest independently-shippable order is not strictly 1→2→3:

```
PR0  (#3)   re-expose cache_buster            — trivial, isolated
PR1  (#2a)  enrich BiosimSimulationRun parse  — additive, benefits both paths
PR2  (#1)   Temporal convergence              — internal refactor, riskiest
PR3  (#2b)  normalize + join-backed listing   — schema/listing, depends on PR1
```

`cache_buster` is trivial and isolated; the "deferred fields" only become real
once parsed upstream (prerequisite to the join paying off); the Temporal refactor
lands before the DB normalization so submission rows get their FK early.

---

## PR0 — Re-expose `cache_buster` on `/simulations/run` (#3)

**Goal:** restore the salt capability the verify path already has (`api/main.py`
exposes it as a query param; the run router hardcodes `"0"` at `router.py`).

**Changes**
- `simulations/models.py`: `RunSimulationRequest` gains `cache_buster: str | None = None`.
- `router.run_simulations`: `cache_buster=request.cache_buster or "0"` → into
  `SimulationRunWorkflowInput.cache_buster`. Default `"0"` preserves dedup.
- Persist `cache_buster` onto `SimulationRunRecord` (helps reconstruct the cache
  key for PR3's identity story).

**Ship/test gate:** endpoint test asserting an explicit `cache_buster` reaches
`start_workflow` args (mock the temporal client) and omitting it defaults to
`"0"`. Document the param in the endpoint summary + `backend/CLAUDE.md`.

**Depends on:** nothing. **Risk:** trivial.

**Product decision to surface:** with dedup default, two submissions of the same
OMEX+simulator share one biosimulations run id — fine for "my runs," but the UI
should expect it. For guaranteed-distinct runs, the UI passes a unique salt
(e.g. the `processing_id`).

---

## PR1 — Enrich `BiosimSimulationRun` parsing (#2a)

**Goal:** actually *source* the "deferred" fields. They are the commented-out
block on `BiosimSimulationRun` (`biosim_runs/models.py`) and biosimulations.org's
`/runs/{id}` almost certainly returns them — but `get_sim_run` / `run_biosim_sim`
(`biosim_service.py`) drop everything except `id/name/simulator_version/status`.

**Changes**
- **First task: confirm the wire shape.** Capture one real `/runs/{id}` response,
  save as a fixture in `tests/fixtures/data/`. De-risks field-name guesses
  (`cpus`, `memory`, `maxTime`, `envVars`, `purpose`, `projectSize`,
  `resultsSize`, `runtime`, `submitted`, `updated`, `email`).
- `biosim_runs/models.py`: uncomment those fields on `BiosimSimulationRun` as
  `Optional` with defaults (existing stored docs still validate).
- `biosim_service.py`: parse them with `res.get(...)` (robust to API variation)
  in both `get_sim_run` and `run_biosim_sim`.

**Ship/test gate:** unit test parsing the captured fixture → fields populate; all
existing tests still pass (additive/optional). Verify path and cache records
immediately carry richer data.

**Depends on:** nothing. **Risk:** low (additive). Without this, PR3's join would
surface zeros — hence it precedes PR3.

---

## PR2 — Temporal convergence: one reusable per-run unit (#1)

**Goal:** make `OmexSimWorkflow` the single "track one biosimulations.org run"
child workflow; delete `simulations/activities.py`'s duplicate
`submit_simulation_activity` + `poll_simulation_activity`.

**Changes**
1. **Canonicalize the submit/poll split in `biosim_runs/activities.py`.** Split
   the monolithic `submit_biosim_simulation_run_activity` into `submit` (returns
   `run_id`, owns the cache-check) and `poll` (poll → HDF5 → save
   `BiosimulatorWorkflowRun`) — mirroring what the simulations pair already does.
2. **`OmexSimWorkflow` surfaces `run_id` early.** Refactor `OmexSimWorkflow.run`
   to call submit → store `biosimulations_run_id` in `sim_output` → call poll.
   Its existing `@workflow.query` then exposes the run_id *before* completion —
   the exact capability the run endpoint needed (and the only reason it didn't
   reuse `OmexSimWorkflow`).
3. **`SimulationRunWorkflow` delegates to children.** Rewrite `workflow.py:run`
   to `start_child_workflow(OmexSimWorkflow, ...)` ×N — the pattern
   `OmexVerifyWorkflow` already uses. Parent keeps its own `job_statuses` (so the
   existing `get_status` → `ConglomerateStatus` query is unchanged), updating from
   child queries (early run_id) and child results (final status).
4. **`update_run_status_activity` stays in the simulations module** — it writes
   the *submission* record (a listing concern); the child workflow stays generic.
5. Drop the deleted activities from `worker_main.py` and `temporal_fixtures.py`;
   register the new biosim_runs submit/poll pair.

**Implemented (2026-05-29) — simpler than steps 1–2 above.** Splitting the activity
to surface `run_id` *early* turned out unnecessary and was skipped: a Temporal parent
cannot **query** a running child (queries are client-side, not workflow-to-workflow),
so "early run_id" would require a child→parent signal. Instead `SimulationRunWorkflow`
composes `OmexSimWorkflow` children with the existing **monolithic activity unchanged**,
and reads each child's `run_id`/status from its **output at completion**. This keeps the
verification production path 100% untouched (lowest risk) and still retires the duplicate
`submit_simulation_activity` / `poll_simulation_activity` — leaving the monolithic
`submit_biosim_simulation_run_activity` as the single shared per-run implementation. The
only behavior change: `biosimulations_run_id` appears in the live `/simulations/{id}`
status query once a run **completes** rather than right after submit (untested, minor;
recoverable later via a child→parent signal if a use case needs it).

**Follow-up "PR2.5" (implemented in a separate PR).** The monolithic activity was
eventually split — not for a signal, but to give `OmexSimWorkflow` a moment between
submit and poll where it can fire an `update_run_status_activity` to record the run id
on `SimulationRunRecord` directly. `OmexSimWorkflowInput` gained optional
`submission_run_id`; when set (only the run path sets it), the child writes
`biosimulations_run_id` early. `GET /simulations/{processing_id}` now does a hybrid
read (workflow query for live status, DB for the early run id). See `workflows-architecture.md`
"Shape C" for the diagram.

**API contract:** unchanged.

**Ship/test gate:**
- New `OmexSimWorkflow` test: with a mock that stays `RUNNING` briefly, assert
  `run_id` is queryable mid-run (proves early surfacing).
- `tests/biosim_verify/test_omex_verify_workflows.py` green (verify path intact).
- `integration_local/test_simulations_run.py` green (now via children) +
  run-endpoint unit tests unchanged.

**Depends on:** nothing functionally; recommend after PR0/PR1. **Risk:** highest
(touches the verify prod path) → land with full verify coverage.
**De-risk option:** add the split activities additively, migrate
`OmexSimWorkflow`, then delete the monolithic + simulations pair in a follow-up
commit for a smaller diff.

---

## PR3 — Normalize `SimulationRunRecord` + join-backed listing (#2b)

**Goal:** the submission row holds identity/lifecycle + a `biosimulations_run_id`
FK; the listing joins to `BiosimulatorWorkflowRun` for computed fields.

**Changes**
1. **Slim `SimulationRunRecord`** to submission-owned fields: `run_id,
   processing_id, name (user label), email, purpose, status, submitted, updated,
   cache_buster, biosimulations_run_id (FK)` — **plus `simulator` +
   `simulator_version`** (requested identity, needed for mid-flight rows before
   any computation exists). **Drop** the duplicated/computed columns:
   `simulator_digest, cpus, memory, max_time, env_vars, project_size,
   results_size, runtime`.
2. **Indexes:** unique on `run_id`; non-unique on `biosimulations_run_id`; and an
   index on `BiosimSims."biosim_run.id"` to back the existing
   `get_biosimulator_workflow_runs_by_biosim_runid` lookup.
3. **App-side join in `query_simulation_runs`:** filter/sort/paginate submissions
   → collect the page's `biosimulations_run_id`s → batch-fetch computations
   (`$in`) → stitch. (Prefer app-side join over `$lookup` — matches the codebase
   style, trivially testable, one extra round trip per page.)
4. **`SimulationRun.from_record` → `from_submission_and_computation(submission,
   computation | None)`** — assembles the *same* DTO; computed fields default to
   `0/[]` when `computation is None` (mid-flight). DTO shape unchanged.

**The crux tradeoff:** once computed fields leave the submission collection, you
cannot server-side filter/sort on them in a single query. **But the frontend's
actual sort/filter columns are all submission-owned** — `index.vue` columns are
`id, name, status, simulator, submitted`, and its `TableFilter.id` is only
`'createdAt' | 'simulator'`. So normalization fully supports today's UI with zero
functional loss. If a future requirement needs filtering on `runtime` /
`projectSize`, *then* reach for `$lookup` aggregation or selectively denormalize
those columns — don't pay that cost preemptively.

**Ship/test gate:**
- DB tests: query returns submission fields; join populates computed fields when a
  matching `BiosimulatorWorkflowRun` exists; mid-flight (no computation) →
  computed defaults, status from submission.
- Golden DTO regression test from PR0 still passes.
- `integration_local`: after completion, listing shows `SUCCEEDED` + *real*
  digest/sizes from the join (visible because PR1 parsed them).

**Depends on:** PR1 (else joined fields are empty); benefits from PR2 (early
run_id → mid-flight rows get their FK immediately). **Risk:** medium (schema +
listing rewrite), but no prod data to migrate.

---

## Net effect

One per-run Temporal unit (`OmexSimWorkflow`) composed by both
`OmexVerifyWorkflow` and `SimulationRunWorkflow`; one storage representation of a
biosimulations.org run + artifacts (`BiosimulatorWorkflowRun`); a thin submission
ledger that FKs into it; and `cache_buster` controllable per submission again.

## Notes on MongoDB references

MongoDB has no foreign-key *constraint* — `biosimulations_run_id` is a plain
manual reference, enforced only by application code. No existence check, no
cascade. The orphan case (a submission with no completed computation yet) is the
**mid-flight state**, not a bug: an app-side left-join yields `null` computed
fields → the row renders as "still running." A unique index on the computation
side makes the join target well-defined; any hard "referenced run must exist"
guarantee is an application check (optionally inside a transaction), never a
schema constraint.

---

## TODO: assess `biosim-client` impact (external API consumer)

`biosim-client` (`../biosim-client`, https://github.com/biosimulations/biosim-client)
is a Python client library for **this platform's** API — the original
`biosim.biosimulations.org`, now served at `api.biosim.biosimulations.org`. It is an
**external consumer** of the endpoints and response models this convergence work
touches, so changes here can break it:

- **PR1 (merged)** added fields to `BiosimSimulationRun`, which is embedded in the
  `/verify/*` responses — additive, so likely backward-compatible, but unverified
  against the client's deserialization.
- **PR3 (planned)** normalizes `SimulationRunRecord` and may reshape `/simulations/runs`
  response assembly — higher risk of a breaking change.
- **PR2 (this)** kept the API contract unchanged — no client impact expected.

**Plan:**
1. **Inventory** which endpoints/models `biosim-client` consumes (clone/read
   `../biosim-client`; grep its generated models / request code against our
   `api/main.py` + `simulations`/`biosim_verify` response models).
2. **Diff** that surface against the changes in PR1 (landed) and PR3 (planned);
   flag any removed/renamed/retyped fields. Additive optional fields are usually
   safe; field removals/renames and required-field changes are not.
3. **Versioning/contract:** decide whether to pin a client version, add a contract
   test (e.g. validate the client's models against our OpenAPI schema in CI), or
   coordinate a client release alongside breaking API changes.
4. **Consider monorepo absorption:** evaluate bringing `biosim-client` into this
   repo (e.g. `clients/python/`) so API + client version and test together, with
   the client's models generated from our OpenAPI schema. Weigh against its
   independent release cadence and external (non-platform) consumers.

Owner/when: TBD — do the inventory (step 1) before PR3 lands, since PR3 is the most
likely to break the client.
