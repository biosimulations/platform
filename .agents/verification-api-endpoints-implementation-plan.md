# Verification API Endpoints Implementation Plan

> Status: **implementation steps 1–10 completed locally; final validation recorded in the [execution report](verification-api-endpoints-execution-report.md).**
> The findings below describe the original baseline. Execution adopted the recommendations in §14, including D7 and D9, except **D2, which was revised on 2026-09-30: `GET /verification_ids` is public and lists every ID**, and **D10 (2026-09-30): `/verify/*` accept anonymous callers** (see §14); release/deployment remain separate operational work.
> Written 2026-09-29 against `main` @ `d9559a0` (backend `0.10.0`).
> Paths are repo-relative. Backend paths assume `backend/` unless they start with `docs/`, `kustomize/`, or `.agents/`.

Legend used throughout:

- **[REPO]** — confirmed by reading repository code/config/tests.
- **[DOCS]** — confirmed from the deployed OpenAPI document (`https://api.biosim.biosimulations.org/openapi.json`, fetched 2026-09-29).
- **[CONCLUSION]** — engineering conclusion drawn from [REPO]/[DOCS] evidence.
- **[ASSUMPTION]** — not verifiable from this repo; must be confirmed.
- **[DECISION]** — needs a product/owner call; recommendation given. All are collected in §14.

---

## 1. Objective

1. Add **`GET /verification_ids`**. It lets a client discover the `workflow_id` values it can pass to `GET /verify/{workflow_id}`.
2. Validate and complete **`GET /verify/{workflow_id}`**. It already exists. The work is to fix its incorrect failure-state and error-status behavior and connect it to the new ID source.
3. Determine whether the system can tell, **before** comparing, that the runs being verified are of different models. Where it can, reject the request early through the existing error conventions.

---

## 2. Current-State Findings

### Backend Architecture

| Concern | Finding |
|---|---|
| Framework | **[REPO]** FastAPI (Python 3.13+, Pydantic v2). App object: `biosim_server/api/main.py:162`. |
| Orchestration | **[REPO]** Temporal (`temporalio` 1.30.0 installed, `>=1.10,<2` pinned in `pyproject.toml:20`). Task queue `verification_tasks`. Worker: `biosim_server/worker/worker_main.py:34-36`. |
| Persistence | **[REPO]** MongoDB via Motor. Each domain has an ABC + `*Mongo` implementation (`biosim_runs/database.py`, `biosim_omex/database.py`, `simulations/database.py`). |
| Routing | **[REPO]** Most domains use an `APIRouter` included in `main.py:173-177`. **All three `/verify/*` routes are defined directly on `app` in `main.py` (lines 259-456).** No verify router module exists. |
| DI / state | **[REPO]** Module-level singletons with `set_*/get_*` in `biosim_server/dependencies.py`. They are initialized in `init_standalone()` (`dependencies.py:153-191`) and exposed as FastAPI `Depends(get_*)` for OpenAPI. Handlers call `get_*()` directly. Tests `patch("biosim_server.api.main.get_*")`. |
| Import-cycle convention | **[REPO]** Services that would cause cycles are imported under `TYPE_CHECKING` and loaded lazily inside `init_standalone()` (`dependencies.py:13-17, 165-166`). |
| Auth | **[REPO]** `get_current_user` (required) / `get_optional_user`. Ownership uses `require_owner_or_admin` (`common/auth/roles.py:163-186`). `ADMIN_ROLE = "admin"` (`roles.py:26`). |
| Error convention | **[REPO]** `HTTPException` with `detail: str`. Service dependency `None` → **503** (`simulations/router.py:107-108, 152-153, 161-165`). Unknown resource → **404**. Semantically invalid input → **400** (`main.py:310`, `simulations/router.py:130-133`). Documented extra responses go through `responses={...}` on the decorator (`simulations/router.py:67-70`). |
| Logging | **[REPO]** `logging.getLogger(__name__)`. `%s`-style for non-sensitive correlation fields. Never log whole `OmexFile` or raw `sub` (`main.py:321-328`). |
| Tests | **[REPO]** pytest + `pytest-asyncio`. API tests use `TestClient` + `patch(...)` + `app.dependency_overrides[get_current_user]` (`tests/api/test_main.py:93-108`). Mongo tests use testcontainers (`tests/fixtures/database_fixtures.py`). Workflow tests use `WorkflowEnvironment.start_time_skipping()` (`tests/fixtures/temporal_fixtures.py:21-28`). **Meta-test** `tests/api/test_openapi_endpoints.py:377-383` fails if any documented `operationId` is missing from its tables. |
| Quality gates | **[REPO]** `uv run ruff check .`, `uv run mypy biosim_server tests` (strict), `uv run pytest -m "not integration"` (`backend/CLAUDE.md` → Verification). |

### Existing Verification Flow

**[REPO]** Two POST entry points start one of two Temporal workflow types. Both expose the same query:

| Endpoint | Handler | Workflow type | Default ID prefix | Code |
|---|---|---|---|---|
| `POST /verify/omex` | `verify_omex` | `OmexVerifyWorkflow` | `omex-verification-` | `main.py:259-349`, `biosim_verify/omex_verify_workflow.py` |
| `POST /verify/runs` | `verify_runs` | `RunsVerifyWorkflow` | `runs-verification-` | `main.py:404-456`, `biosim_verify/runs_verify_workflow.py` |
| `GET /verify/{workflow_id}` | `get_verify_output` | *(queries either)* | — | `main.py:365-401` |

End-to-end:

1. The POST handler builds `workflow_id = f"{workflow_id_prefix}{uuid.uuid4()}"` (`main.py:312`, `:427`). **`workflow_id_prefix` is a caller-controlled query parameter**, so the prefix cannot identify a verification workflow.
2. `temporal_client.start_workflow(..., id=workflow_id, task_queue="verification_tasks")`. The handler returns a synthetic `VerifyWorkflowOutput(workflow_status=PENDING, owner_sub=user.sub)`.
3. `@workflow.init` sets `self.verify_output` with `workflow_status=IN_PROGRESS` and `owner_sub` (`omex_verify_workflow.py:31-41`, `runs_verify_workflow.py:29-36`).
4. OMEX path: N child `OmexSimWorkflow`s run the **same uploaded archive** on N simulators. Runs path: `get_existing_biosim_simulation_run_activity` imports N existing biosimulations.org runs.
5. `generate_statistics_activity` (`biosim_verify/activities.py:23-134`) builds an NxN comparison per dataset. It sets `workflow_results` and `COMPLETED`.
6. `@workflow.query(name="get_output")` returns `self.verify_output`. **This query is the only read path.**

**Persistence [REPO]:**

- **No verification-level record is persisted anywhere.** `VerifyWorkflowOutput` lives only in Temporal workflow state and history.
- `BiosimSims` (`biosim_runs/database.py`) stores per-simulation `BiosimulatorWorkflowRun` rows. Its `workflow_id` field is **not** a verification ID:
  - On the OMEX path it is the child `OmexSimWorkflow` ID.
  - On the runs path it is the verify workflow ID, but only on first import.
  - Cache hits write nothing (`biosim_runs/activities.py:40-44, 143-149`).
  - Only `SUCCEEDED` rows are written (`activities.py:251-257`).
- `config.py:336` defines `mongodb_collection_compare = "BiosimCompare"`, and every overlay sets it (`kustomize/config/biosim-{gke,rke,local}/shared.env:10`). **Nothing reads or writes it.** `backend/OVERVIEW.md:210` marks it "(future use)". `docs/workflows-architecture.md:365-368` diagrams it as `BiosimCompare { workflow_id PK, comparison_statistics }`.

### Identifier Semantics

| Identifier | What it is | Origin | Relationship |
|---|---|---|---|
| **`workflow_id`** (verify) | Temporal **Workflow ID** of an `OmexVerifyWorkflow` / `RunsVerifyWorkflow` | `main.py:312`, `:427` (`prefix + uuid4`) | **The value `GET /verify/{workflow_id}` accepts.** |
| **"verification ID"** | Not a distinct concept in the codebase. `grep` finds no `verification_id` symbol. | — | **[CONCLUSION]** Same value as the verify `workflow_id`. `/verification_ids` must return verify workflow IDs. |
| `workflow_run_id` | Temporal **Run ID** of one execution | `workflow_handle.run_id` / `workflow.info().run_id` | Different value. Not accepted by `GET /verify`. Must not be returned. |
| `BiosimulatorWorkflowRun.workflow_id` | Child sim-workflow ID (OMEX path) or verify ID (runs path, first import only) | `biosim_runs/activities.py:89, 235` | **Not a verification ID source.** Incomplete and ambiguous. |
| `processing_id` (`sim-run-…`) | `SimulationRunWorkflow` ID | `simulations/router.py:138` | Different subsystem. `GET /verify/{processing_id}` 404s because the `get_output` query is undefined there. |
| `biosimulations_run_ids` | biosimulations.org run IDs (24-hex ObjectId-like) | `POST /verify/runs` input | Inputs, not verification IDs. |

**[CONCLUSION]** The endpoint name `verification_ids` doesn't match the path parameter name `workflow_id`, but it names the same data. The response should state this explicitly (see §5).

### Existing `/verify/{workflow_id}` Behavior

**[REPO]** `main.py:365-401`, operationId `get-verify-output`, tag `Verification`, `response_model=VerifyWorkflowOutput`:

1. Requires a bearer token (`Depends(get_current_user)`).
2. `temporal_client.get_workflow_handle(workflow_id).query("get_output", rpc_timeout=60s)`.
3. If `workflow_output.owner_sub is not None` → `require_owner_or_admin` (403 for non-owner non-admin). Legacy payloads with `owner_sub=None` are readable by any authenticated caller.
4. `except HTTPException: raise`. **Any other exception → 404** with `detail="error retrieving verification job output with id: {id}: {exc}"`.

Defects found by reading the code:

| # | Defect | Evidence | Effect |
|---|---|---|---|
| D1 | **A failed workflow reports `IN_PROGRESS` forever.** Neither workflow catches exceptions from `generate_statistics` / child workflows. When the workflow fails, `verify_output.workflow_status` is never updated. Temporal still answers queries on closed workflows while history is retained, so the query returns the last in-memory state. | `omex_verify_workflow.py:66-86`, `runs_verify_workflow.py:74-78, 94-109` | Callers poll forever. `FAILED` is only emitted by the runs path's explicit early return (`runs_verify_workflow.py:54-69`). |
| D2 | **Temporal unavailable → 404.** `assert temporal_client is not None` is *inside* the `try`, so `AssertionError` becomes 404. RPC timeouts and connection errors also become 404. | `main.py:379-401` | Outages look like "not found". This differs from the 503 convention in `simulations/router.py:152-153`. |
| D3 | **Exception text leaks into `detail`.** | `main.py:398-401` | Temporal error strings reach clients. |
| D4 | **Results vanish after Temporal retention.** Nothing is persisted, so once history is purged the ID returns 404, identical to an ID that never existed. | see Persistence above | Clients can't tell "expired" from "wrong ID". |
| D5 | The 403 text says "…this simulation run" because it is shared with the simulations router. | `roles.py:184-185` | Cosmetic. **Out of scope.** |

`PENDING` is only returned by the POST handlers. `GET` never returns it because `@workflow.init` sets `IN_PROGRESS`.

### Relevant External API Contract

**[DOCS]** The deployed API reports `version 0.10.0`. The `Verification` tag contains exactly:

| Method | Path | operationId | Documented responses |
|---|---|---|---|
| POST | `/verify/omex` | `verify-omex` | 200, 422 |
| GET | `/verify/{workflow_id}` | `get-verify-output` | **200, 422 only** |
| POST | `/verify/runs` | `verify-runs` | 200, 422 |

- `workflow_id`: path, `type: string`, no pattern or format.
- 200 body: `VerifyWorkflowOutput` with required `workflow_id`, `compare_settings`, `workflow_status`, `timestamp`, and nullable `workflow_run_id`, `workflow_error`, `workflow_results`.
- `VerifyWorkflowStatus` enum: `PENDING | IN_PROGRESS | COMPLETED | FAILED | RUN_ID_NOT_FOUND`.
- **No `/verification_ids` or equivalent listing operation exists.**
- **The docs say nothing about model-equivalence assumptions**, and document neither 404 nor 401/403 on `GET /verify/{workflow_id}`.
- **Drift between deployed and `main`:** the deployed spec has **no `security`** on the verify operations and **no `owner_sub`** field. `main` has both (auth work in PR #98). `main` is ahead of production. The committed `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` should be regenerated as part of this work (§10).

---

## 3. Proposed Design

### `GET /verification_ids`

**Source of truth — [DECISION D1], recommendation: a Mongo verification ledger in the reserved `BiosimCompare` collection.**

Two viable sources were evaluated:

| | **A. Temporal visibility** (`client.list_workflows("WorkflowType='OmexVerifyWorkflow' OR WorkflowType='RunsVerifyWorkflow'")`) | **B. Mongo ledger `BiosimCompare`** (row written by the POST handlers) — **recommended** |
|---|---|---|
| New persistence | none | one collection, already configured in every overlay |
| Historical workflows | all still in retention | only those created after deploy (gap closes after one retention period; optional backfill) |
| Every listed ID resolvable by `GET /verify` | yes (same store) | only within Temporal retention. Step 5 makes expired IDs explicit instead of indistinguishable 404s. |
| **Owner scoping** (matches `GET /verify`'s owner-or-admin rule) | **Not possible without a custom `OwnerSub` search attribute.** Registering it is a cluster-admin change outside this repo, and `start_workflow` with an unregistered attribute fails, which would break `POST /verify/*`. | trivial `{"owner_sub": sub}` filter |
| Test support | the time-skipping test server has limited visibility support. Needs the `start_local` dev server or mocks. | testcontainers Mongo + mocks, same as `tests/simulations/test_runs_query.py` |
| Consistency | eventually consistent (a just-started workflow can be missing) | read-your-writes (inserted before `start_workflow`) |
| Repo precedent | none (`list_workflows` is unused) | **exact precedent:** `POST /simulations/run` persists a `SimulationRunRecord` ledger *before* `start_workflow` (`simulations/router.py:155-208`) because "a run with no durable owner/visibility row cannot be authorized later." |

**[CONCLUSION]** Option B fits the repo's established pattern and the access-control posture already applied to `GET /verify/{workflow_id}`. Option A is only preferable if D2 resolves to "admin-only listing" **and** the team accepts retention-bounded results. §14 records the trade-off.

**Visibility scope — [DECISION D2], recommendation:** a non-admin gets **their own** IDs (`owner_sub == user.sub`). A caller with `ADMIN_ROLE` gets **all** IDs. This mirrors `GET /verify` (`main.py:388-393`). Returning every user's IDs to any authenticated caller would let anyone enumerate every legacy `owner_sub=None` workflow, which `GET` then serves to them. The requirement "returns all verification IDs" is read as "all IDs the caller may retrieve". Confirm this reading.

> **Revised (2026-09-30, owner decision):** the legacy API has no authentication, so `GET /verification_ids` is **public** and returns **every** ID, with no token. This is the literal reading of "returns all verification IDs". What becomes public is each ID, including its caller-chosen `workflow_id_prefix`, and the number of verifications. **Superseded in part by D10:** the `/verify/*` endpoints are now optional-auth, so anonymous (ownerless) verifications are listed *and* publicly readable. Only verifications started with a token remain owner-or-admin.

**Shape — [DECISION D3], recommendation:** a wrapper object, not a bare array, so pagination or metadata can be added later without breaking clients:

```json
{ "verification_ids": ["runs-verification-5b0c…", "omex-verification-91fe…"] }
```

- Order: `created` descending, tiebreak `workflow_id` ascending (deterministic).
- Unique: guaranteed by a unique index on `workflow_id`.
- Includes every state (in progress, completed, failed, run-not-found). All of them are retrievable via `GET /verify`, so none are filtered.
- No pagination and no filters. Neither is required. The list is public and unscoped (revised D2), so its volume grows unbounded, which is why pagination is an open item (**[DECISION D4]**).

### `GET /verify/{workflow_id}`

Keep the path, operationId, response model, and success semantics unchanged. Fix D1–D4 with **API-only** changes. The workflows are not modified, so no worker drain is needed.

1. Temporal client `None` → **503** (fixes D2).
2. `handle.describe()`:
   - `RPCError` with `status == RPCStatusCode.NOT_FOUND` → go to the ledger fallback (step 5).
   - Other `RPCError` / timeout → **503**.
   - `description.workflow_type not in {"OmexVerifyWorkflow", "RunsVerifyWorkflow"}` → **404**. This rejects `sim-run-*` and child IDs explicitly instead of relying on a query-name miss.
3. `handle.query("get_output", ...)` as today → `VerifyWorkflowOutput`.
4. Ownership check, unchanged.
5. **Reconcile terminal state (D1):** if `not output.workflow_status.is_done` and `description.status` ∈ {`FAILED`, `TERMINATED`, `TIMED_OUT`, `CANCELED`}, return a copy with:
   - `workflow_status=FAILED`
   - `workflow_error=f"Verification workflow ended with status {description.status.name}"`

   Do not echo the Temporal failure message; it may contain internal detail.
6. **Ledger fallback on NOT_FOUND (D4):**
   - If a ledger row exists **and** the caller is owner or admin → **404** with a distinct detail: `"Verification {id} exists but its results are no longer retained."`
   - Otherwise → **404** `"Verification not found: {id}"`.
   - Status code **[DECISION D5]**: 404 keeps the existing contract. 410 is the more precise alternative.
7. Replace `detail=f"...{exc_message}"` with a fixed message and keep the exception in `logger.error(..., exc_info=...)` (fixes D3).

### Verification ID / Workflow ID Data Flow

```text
POST /verify/omex|runs
  ├─ workflow_id = prefix + uuid4                             (unchanged)
  ├─ ledger.insert(VerificationRecord(workflow_id, verify_type, owner_sub, created))   NEW
  │     └─ failure → 503, workflow NOT started                (mirrors simulations/router.py:198-208)
  ├─ temporal.start_workflow(id=workflow_id)                  (unchanged)
  │     ├─ definitive rejection → ledger.delete(workflow_id); 503
  │     └─ uncertain outcome → reconcile same ID; retain ledger; recovered 200 or 503
  └─ 200 VerifyWorkflowOutput(PENDING)                        (unchanged)

GET /verification_ids
  └─ ledger.list_verification_ids(owner_sub = None if admin else user.sub)
        └─ { verification_ids: [...] }  ──┐
                                           ▼
GET /verify/{workflow_id}
  ├─ temporal.describe → type check / NOT_FOUND → ledger fallback (404 w/ distinct detail)
  ├─ temporal.query("get_output")
  ├─ owner-or-admin check
  └─ reconcile terminal status → 200 VerifyWorkflowOutput
```

### Model Compatibility Preflight Check

Full analysis in §6. Summary:

- **[CONCLUSION]** The OMEX path runs **one uploaded archive** on every simulator, so model identity is the same by construction. No preflight is needed.
- **[CONCLUSION]** The runs path accepts **arbitrary** biosimulations run IDs, so it can mix models. **No single reliable model identifier exists in the repo.** A reliable preflight is still possible for the property that actually matters, *meaningful overlap*: the intersection of `(dataset_name, sedml labels)` across the runs' HDF5 metadata. This is exactly the basis `generate_statistics_activity` compares on.
- **Today an incompatible-model request never yields a clean error:**
  1. `generate_statistics_activity` hits a `KeyError` (`activities.py:68-69`) whenever a dataset exists in one run but not another.
  2. The activity is retried up to **100 times**, re-downloading all HDF5 data on each attempt (`runs_verify_workflow.py:104-105`).
  3. The workflow then fails, and `GET /verify` reports `IN_PROGRESS` forever (D1).
- Recommendation:
  - **(a)** Make the activity tolerate missing datasets. Activity-only change, replay-safe.
  - **(b)** Add an API-side preflight on `POST /verify/runs` that returns **400** when there is no overlap.
  - Both are separable from the ID endpoints and are ordered last (§12). Archive-hash comparison stays advisory (§6).

---

## 4. Detailed Implementation Steps

Steps 1–7 are the **core scope** (ID listing + `GET /verify` completion). Steps 8–9 are **model compatibility** and can ship as a separate PR. Step 10 is docs.

### Step 1 — Ledger model and response model

**Files:**
- `backend/biosim_server/biosim_verify/models.py`

**Symbols (new):**
- `VerificationType(StrEnum)`: `OMEX = "omex"`, `RUNS = "runs"`
- `VerificationRecord(BaseModel)`:
  - `workflow_id: str`
  - `verify_type: VerificationType`
  - `owner_sub: str | None = None` (Optional so the model matches `VerifyWorkflowOutput.owner_sub` semantics. New rows always have it because both POSTs require auth.)
  - `created: datetime` (UTC)
- `VerificationIdsResponse(BaseModel)`: `verification_ids: list[str]`, with `Field(description=...)` stating each value is a `workflow_id` accepted by `GET /verify/{workflow_id}`.

**Changes:** additive only. Do **not** modify `VerifyWorkflowOutput` or `VerifyWorkflowStatus`. Both are serialized into Temporal histories and returned by the published contract.

**Rationale:** `biosim_verify/models.py` already holds all verify DTOs. `verify_type` is the one field beyond ID/owner/time. It costs nothing at write time and avoids a Temporal `describe` per row if a type filter is ever needed. Drop it if reviewers want the strict minimum.

*Optional, pending D6:* `omex_file_hash_md5: str | None` and `biosimulations_run_ids: list[str] | None`, both known at POST time. Only add them if Step 9's follow-up (hash advisory) is approved.

### Step 2 — Ledger database service

**Files:**
- `backend/biosim_server/biosim_verify/database.py` (**new**, justified: every Mongo-backed domain has its own `database.py`)

**Symbols (new):**
- `class VerificationDatabaseService(ABC)`:
  - `insert_verification(record: VerificationRecord) -> VerificationRecord`
  - `list_verification_ids(owner_sub: str | None) -> list[str]`. `None` means no owner filter (admin).
  - `get_verification(workflow_id: str) -> VerificationRecord | None`
  - `delete_verification(workflow_id: str) -> None`
  - `delete_all_verifications() -> None` (test support, mirrors `delete_all_simulation_runs`)
  - `ensure_indexes() -> None`
  - `close() -> None`
- `class VerificationDatabaseServiceMongo(VerificationDatabaseService)`:
  - Collection: `get_settings().mongodb_collection_compare`.
  - `list_verification_ids`: `find(filter, projection={"workflow_id": 1, "_id": 0}).sort([("created", DESCENDING), ("workflow_id", ASCENDING)])`, then `to_list(length=None)`. See D4.
  - `ensure_indexes`: `create_index("workflow_id", unique=True)`, `create_index([("owner_sub", ASCENDING), ("created", DESCENDING)])`, `create_index([("created", DESCENDING)])`.

**Rationale:** mirrors `simulations/database.py:170-324` (ABC + Mongo implementation + `ensure_indexes`). Reuses the collection name that is already configured and documented as reserved for comparison results. No kustomize change is needed.

### Step 3 — Dependency wiring

**Files:**
- `backend/biosim_server/dependencies.py`

**Symbols:**
- `global_verification_database_service`, `set_verification_database_service`, `get_verification_database_service` (new)
- `init_standalone` (modify)
- `TYPE_CHECKING` block (modify)

**Changes:**
- Add the `VerificationDatabaseService` type import under `TYPE_CHECKING`, as done for `SimulationRunDatabaseService` (`dependencies.py:13-17`). `biosim_verify.models` → `biosim_runs` → `biosim_runs.activities` → `dependencies` would otherwise form a new top-level cycle.
- In `init_standalone`:
  - Import `VerificationDatabaseServiceMongo` locally (next to `dependencies.py:166`).
  - Construct it with the shared `motor_client`.
  - `await ...ensure_indexes()`.
  - `set_verification_database_service(...)`.

**Rationale:** this is the established DI seam, and it keeps the API patchable in tests.

### Step 4 — Write ledger rows from both POST handlers

**Files:**
- `backend/biosim_server/api/main.py`

**Symbols:**
- `verify_omex` (`:259-349`)
- `verify_runs` (`:404-456`)
- new private helper `_start_verify_workflow(...)` or inline code

**Changes (both handlers, after `workflow_id` is computed and before `start_workflow`):**
1. `ledger = get_verification_database_service()`. If `None` → `HTTPException(503, "Verification database service not available")`.
2. `await ledger.insert_verification(VerificationRecord(workflow_id=..., verify_type=..., owner_sub=user.sub, created=datetime.now(UTC)))`. On exception: log and return **503** `"Failed to persist verification record"`. The workflow is **not** started.
3. Wrap `temporal_client.start_workflow(...)`. Delete the ledger row only for a definitive `RPCStatusCode.INVALID_ARGUMENT` rejection (best-effort cleanup, sanitized 503). For other exceptions, describe the same workflow ID and read its first history event using the configured payload converter. If type, original input and owner match, return the recovered handle without starting again. Otherwise retain the row and return a sanitized 503 containing the workflow ID for polling. A follow-up NOT_FOUND does not prove rejection: the start may still be in flight.
4. Add `Depends(get_verification_database_service)` to each decorator's `dependencies=[...]` list, as existing handlers do for their services. This is OpenAPI-neutral.

Keep a single helper if both handlers would otherwise duplicate steps 1–3. Keep it in `main.py`, beside `_VerifyOwnership`.

*As implemented:* `_require_verification_ledger()` (step 1) runs at the top of each handler, right after the Temporal-client check. That puts it before the OMEX upload and before the runs preflight, so an unavailable ledger costs no storage writes or upstream calls. `_persist_and_start_verify(...)` (steps 2–3) takes the ledger, ID, type, owner, Temporal client, original workflow input and a zero-argument `start_workflow` callable, and returns the workflow handle.

**Rationale:** insert-before-start copies `simulations/router.py:155-226`. It guarantees every started verification is listable (read-your-writes). Cleanup applies only to definitive rejection. An ambiguous start keeps its ledger row so a successfully accepted workflow cannot disappear from the listing. The simulations path marks such rows `FAILED` instead, because its rows are user-visible history. A retained uncertain row may temporarily have no matching workflow; this is safer than deleting the only discoverable ID for a delayed accepted start.

**Behavior change:** Mongo outage now makes `POST /verify/*` return 503 where it previously succeeded. This is intended (it matches `POST /simulations/run`) but is listed in §11.

### Step 5 — Harden `GET /verify/{workflow_id}`

**Files:**
- `backend/biosim_server/api/main.py`

**Symbols:**
- `get_verify_output` (`:365-401`)
- new module constant `_VERIFY_WORKFLOW_TYPES = frozenset({"OmexVerifyWorkflow", "RunsVerifyWorkflow"})`
- new helper `_reconcile_terminal_status(output, execution_status) -> VerifyWorkflowOutput`

**Changes:** implement the flow in §3 → `GET /verify/{workflow_id}`:

- `temporal_client is None` → 503, checked **before** any `try`.
- `from temporalio.service import RPCError, RPCStatusCode` and `from temporalio.client import WorkflowExecutionStatus`.
- `desc = await handle.describe(rpc_timeout=timedelta(seconds=60))`:
  - `RPCError` NOT_FOUND → ledger fallback.
  - Any other `RPCError` or `asyncio.TimeoutError` → 503.
- Type check against `desc.workflow_type` → 404.
- `query("get_output", ...)`:
  - If query fails and describe reports FAILED, TERMINATED, TIMED_OUT or CANCELED, `_recover_terminal_verification` reads the original input from the first retained history event, bound to the described Run ID. It checks the original owner/admin authorization before returning FAILED with a sanitized status message and the original comparison settings. This also supports executions closed before the first worker task and legacy ownerless workflows. No workflow payload or ledger schema change is needed.
  - For other execution states, `WorkflowQueryFailedError` / `WorkflowQueryRejectedError` → 404. Recovery history NOT_FOUND uses the existing ledger fallback; unavailable or invalid history returns sanitized 503.
  - `RPCError` NOT_FOUND → ledger fallback, as for `describe`. This covers history purged between `describe` and `query`.
  - Any other `RPCError` → 503.
- Ownership check (unchanged).
- `_reconcile_terminal_status` for D1:
  - Only overrides when `not output.workflow_status.is_done`.
  - Maps `FAILED`/`TERMINATED`/`TIMED_OUT`/`CANCELED` → `VerifyWorkflowStatus.FAILED`.
  - Uses `output.model_copy(update=...)`.
  - Leaves `COMPLETED` and `RUNNING` untouched. Does **not** override a `FAILED`/`RUN_ID_NOT_FOUND` the workflow already set.
- Ledger fallback:
  - `ledger.get_verification(workflow_id)`.
  - If the row exists and (caller is admin **or** `row.owner_sub in (None, user.sub)`) → 404 with the "no longer retained" detail.
  - Otherwise the generic 404. Don't reveal that someone else's ID exists.
  - If the ledger service is `None` or raises, degrade to the generic 404 and log a warning. The GET must not become ledger-dependent.
- Fixed `detail` strings. Raw exceptions go to `logger.error(..., exc_info=...)` only.
- Decorator: add `responses={401:…, 403:…, 404:…, 503:…}` with descriptions, following `simulations/router.py:67-70`.

**Rationale:**
- One extra `describe` RPC per GET buys a correct terminal status and a correct 404/503 split. The alternative (catching failures inside the workflows) is a workflow code change that needs `workflow.patched` or a worker drain (`docs/workflows-architecture.md` → Deploy considerations).
- This step is API image only.

**Dependency:** Step 3 (for the ledger fallback). The rest of Step 5 is independent and could land first.

### Step 6 — Add `GET /verification_ids`

**Files:**
- `backend/biosim_server/api/main.py`

**Symbols (new):**
- `list_verification_ids` handler

**Changes:**

```python
@app.get(
    "/verification_ids",
    response_model=VerificationIdsResponse,
    operation_id="list-verification-ids",
    tags=["Verification"],
    dependencies=[Depends(get_verification_database_service)],
    summary="List verification workflow IDs retrievable via GET /verify/{workflow_id}",
    responses={503: {"description": "Verification database unavailable."}},
)
async def list_verification_ids(
    user: AuthenticatedUser = Depends(get_current_user),
) -> VerificationIdsResponse:
```

*As implemented after the revised D2:* the handler takes no `user` parameter, has no `get_current_user` dependency or 401 response, and always calls `list_verification_ids(None)`. The bullets below describe the original owner-scoped design.

- `ledger is None` → 503.
- `owner_filter = None if ADMIN_ROLE in user.roles else user.sub`.
- `ids = await ledger.list_verification_ids(owner_filter)`. Exceptions → log + 503 `"Failed to list verification IDs"`.
- Return `VerificationIdsResponse(verification_ids=ids)`. The empty case is `{"verification_ids": []}` with status 200.
- Import `ADMIN_ROLE` from `biosim_server.common.auth.roles`. `require_owner_or_admin` is already imported there.
- Log `"listing verification ids (admin=%s, count=%d)"`. Do **not** log `sub`.

**Route placement:** `/verification_ids` is a sibling of `/verify`, so it can't collide with `/verify/{workflow_id}`. **Do not** use `/verify/ids`: it would conflict with the path-parameter route and depend on registration order.

**Rationale:** the path is fixed by the requirement. The handler lives in `main.py` because every verify route lives there. Extracting a verify router would be an unrelated refactor.

### Step 7 — OpenAPI meta-test registration and core tests

**Files:**
- `backend/tests/api/test_openapi_endpoints.py`
- `backend/tests/api/test_main.py`
- `backend/tests/biosim_verify/test_verification_database.py` (**new**)
- `backend/tests/fixtures/database_fixtures.py`

**Changes:**
- `test_openapi_endpoints.py`:
  - `_CORE_PATHS` += `"/verification_ids"`
  - *As implemented after the revised D2:* `AUTH_MODE["list-verification-ids"] = AuthMode.NONE`, not listed in `_REQUIRED_AUTH_OPERATION_IDS`, with an anonymous probe runner (`_probe_list_verification_ids`, ledger `None` → 503) and `VALIDATION_SKIP["list-verification-ids"] = "no parameters"`.

  Without these, `test_every_operation_id_is_accounted_for` (`:377-383`) fails.
- `database_fixtures.py`: add a `verification_database_service_mongo` fixture on `mongo_test_client`, modeled on `simulation_run_database_service_mongo` (`:72-86`). It calls `ensure_indexes()` and `delete_all_verifications()` on teardown.
- Test cases: §9.

### Step 8 — (Compatibility) Make `generate_statistics_activity` tolerate missing datasets

**Files:**
- `backend/biosim_server/biosim_verify/activities.py`

**Symbols:**
- `generate_statistics_activity` (`:62-124`)

**Changes:**
- Resolve `labels_i`/`labels_j`/`data_i` with `.get()` / `in` checks.
- Move the existing "dataset not found" guard (currently `:93-97`) **before** the first dictionary access (`:68-69`, `:79`).
- Record `ComparisonStatistics(error_message="Dataset {name} not found in results for …")` for the pair instead of raising.
- `array_i` must only be built when run i has the dataset. Restructure the i-loop so a missing dataset for run i produces an error cell for every j.

**Rationale:**
- Turns "different models" from a 100-attempt retry storm ending in a stuck `IN_PROGRESS` into a `COMPLETED` report whose cells say why they couldn't be compared. That is the behavior the existing guard at `:93` was clearly written to provide.
- **Activity-only change → replay-safe.** Activity results are recorded in history, and the workflow's command sequence doesn't change. No drain is required, but it needs a worker image release.
- It also fixes the OMEX path when one simulator emits fewer datasets.

### Step 9 — (Compatibility) Preflight on `POST /verify/runs`

**Files:**
- `backend/biosim_server/biosim_verify/compatibility.py` (**new**: pure functions, no I/O)
- `backend/biosim_server/api/main.py` (`verify_runs`)

**Symbols (new):**
- `find_common_datasets(hdf5_files: Mapping[str, HDF5File]) -> dict[str, list[str]]`: dataset names present in **every** run, with identical `sedml_labels`.
- `async def _load_hdf5_metadata_for_preflight(run_ids) -> dict[str, HDF5File]` in `main.py`:
  - Prefer the Mongo cache: `get_database_service().get_biosimulator_workflow_runs_by_biosim_runid(id)` → `.hdf5_file`.
  - Otherwise `get_biosim_service().get_hdf5_metadata(id)`.
  - A run whose metadata can't be fetched (404, run not finished, `aiohttp.ClientResponseError`) is **skipped**. The workflow already reports `RUN_ID_NOT_FOUND`/`FAILED` for it (`runs_verify_workflow.py:54-69`), and the preflight must not duplicate that logic.

**Changes in `verify_runs`**, before the ledger insert:
- If `len(biosimulations_run_ids) < 2` → no preflight.
- Load metadata. If **≥ 2** runs have metadata and `find_common_datasets(...)` is empty → `HTTPException(400, detail=...)`. The detail lists, per run ID, its dataset names. Example: `"Runs have no datasets in common; comparison requires the same model/outputs. 67817a…: [...]; 67817b…: [...]"`.
- If `compare_settings.observables` is set, also reject when none of the requested observables appear in the common labels. **[DECISION D7]**: adopted. Note that `observables` is validated here but is not consumed by any workflow or activity, so it does not filter results (see D7 in §14).
- Add `Depends(get_biosim_service)` to the decorator's dependencies.

**Rationale:**
- Returns **400**, the repo's code for semantically invalid POST input (`main.py:310`, `simulations/router.py:130-133`), **before** a workflow is started or a ledger row written.
- Pure function, so it is unit-testable without I/O.
- Costs N metadata GETs, the same calls the workflow already makes. Typical N is 2–5.

### Step 10 — Documentation and generated spec

See §10.

---

## 5. API Behavior

### `GET /verification_ids`

- **Request:** `GET /verification_ids`, `Authorization: Bearer <access token>`. No parameters.
- **Success (200):**

  ```json
  { "verification_ids": ["runs-verification-5b0c…", "omex-verification-91fe…"] }
  ```

  Newest first. **Public (revised D2):** no token is required and every ID is returned. A token, if sent, is ignored.
- **Empty (200):** `{ "verification_ids": [] }`
- **Errors:**
  - `503`: ledger service not initialized, or a Mongo error.

### `GET /verify/{workflow_id}`

- **Request:** unchanged. `workflow_id` path string, bearer token required.
- **Success (200):** unchanged `VerifyWorkflowOutput`. `workflow_status` is `IN_PROGRESS`, `COMPLETED`, `FAILED`, or `RUN_ID_NOT_FOUND`. **New:** a workflow that failed, was terminated, timed out, or was canceled now reports `FAILED` with a non-null `workflow_error` instead of `IN_PROGRESS`.
- **Missing workflow:**
  - `404 "Verification not found: {id}"`
  - or, for a ledger-known ID whose Temporal history has expired and the caller is owner/admin, `404 "Verification {id} exists but its results are no longer retained."` (D5)
- **Not a verification workflow** (e.g. `sim-run-…`, a child sim workflow ID): `404`.
- **Missing verification result:** a running workflow → 200 `IN_PROGRESS` with `workflow_results=null` (unchanged). There is no "exists but has no result" terminal state other than `FAILED`/`RUN_ID_NOT_FOUND`.
- **Errors:**
  - `401`: no token.
  - `403`: `owner_sub` set and caller is neither owner nor admin (unchanged).
  - `503`: Temporal client `None`, unreachable, or timed out. **Changed from 404.**

### `POST /verify/runs` (Step 9 only)

- **New `400`:** ≥ 2 runs with retrievable HDF5 metadata share no dataset with identical labels. Returned before any workflow starts.

---

## 6. Model Compatibility Handling

### Available Model Identity Data

| Data | Where | Reliable as "same model"? |
|---|---|---|
| `BiosimSimulationRun.name` | upstream run metadata (`biosim_runs/models.py:83`) | **No.** User-chosen label. Explicitly rejected. |
| `BiosimulatorWorkflowRun.file_hash_md5` | MD5 of the full archive (`biosim_runs/activities.py:73-90`) | **Sufficient, not necessary.** Equal hash ⇒ identical archive ⇒ same model. Different hash does **not** mean a different model: re-zipping, metadata or thumbnail edits, or different SED-ML all change it. |
| `OmexContent.model_formats[].location/format_uri` | `compatibility/omex_parser.py:93` | **No.** Describes format and file path, not model content. |
| Per-model-file content hash | *not computed anywhere* | Would be the most precise "same model" signal, but needs new code (download archive → parse manifest → hash model file(s)). Follow-up only. |
| HDF5 metadata: dataset names + `sedmlDataSetLabels` | `HDF5File.datasets` / `HDF5Dataset.sedml_labels` (`biosim_runs/models.py:21-48`), fetched via `BiosimService.get_hdf5_metadata` | **Not model identity**, but it is **exactly the comparison basis** used by `generate_statistics_activity`. Empty overlap ⇒ nothing meaningful to compare. |
| `RunSummary.metadata[].encodes` | `summaries/models.py:31` | **No.** Biological annotations only. |
| Link from run → published project/model ID | *absent from `BiosimSimulationRun`* | — |

**[CONCLUSION]** The repo can't establish reliable model *identity*. It **can** reliably establish **comparability**: a non-empty intersection of `(dataset_name, labels)`. That is the operational meaning of "meaningful overlap" in the requirement.

### Proposed Validation Point

| Path | Needs a check? | Where |
|---|---|---|
| `POST /verify/omex` | **No.** One archive → same model by construction (`omex_verify_workflow.py:55-62`). | — |
| `POST /verify/runs` | **Yes.** | **API layer, before ledger insert and `start_workflow`** (Step 9). This is the earliest point where the run IDs are known and the only one that gives a synchronous error. |
| Inside the workflow | Defense in depth only. | Step 8 (activity). A workflow-level early exit is **not** recommended now: it changes the command sequence and would need `workflow.patched` or a drain. |

### Incompatible-Model Behavior

- **Preflight detects no overlap:** `400` with per-run dataset lists. No workflow, no ledger row.
- **Preflight can't see metadata for some runs** (not finished, 404, upstream error): check the remaining runs if ≥ 2. Otherwise let the workflow proceed. Step 8 guarantees the result is a `COMPLETED` report with per-cell `error_message`, not a stuck workflow.
- **Partial overlap:** allowed. Non-overlapping datasets appear as error cells (Step 8).
- **Different archive hash but overlapping outputs:** allowed. Surfacing this as an advisory needs a response field such as `warnings` on `VerifyWorkflowOutput`. That is a Temporal-payload schema change and is **not** proposed (D6).

### Limitations / Follow-up Work

1. The overlap check is necessary, not sufficient. Two different models that happen to emit a dataset with identical labels pass. Only a model-content hash would close that gap (follow-up, D6).
2. The preflight adds N upstream HTTP calls to `POST /verify/runs` latency on cache misses.
3. A standalone "check before submit" endpoint (analogous to `POST /compatibility/check`) is **not** proposed. The requirement is met by failing the POST early.

---

## 7. Data and Persistence Considerations

- **Collection:** `BiosimCompare` (`settings.mongodb_collection_compare`). It is already set in all three `kustomize/config/*/shared.env`, so there are **no kustomize or ConfigMap changes**.
- **Document:** `{ workflow_id, verify_type, owner_sub, created }`, with the optional fields in Step 1.
- **Indexes:** unique `workflow_id`; `(owner_sub, created desc)`; `(created desc)`. Created idempotently at startup by `ensure_indexes()`, matching the existing pattern.
- **Migration:** none. The collection is new and empty.
- **Historical gap:** verifications started before deploy aren't listed.
  - **[VERIFIED 2026-09-29]** GKE Temporal `default` namespace retention is 24h, confirmed through the existing admin-tools pod with `temporal operator namespace describe default`. RKE was not checked. The original assumption was the `temporalio/auto-setup` default (24h). `kustomize/cluster/base/temporal-deployment.yaml` doesn't set `DEFAULT_NAMESPACE_RETENTION`, and the live GKE Temporal is managed outside this repo (`UCHHPC/k8s-config`). Confirm with `temporal operator namespace describe default`.
  - If retention is short, the gap closes on its own within one retention period.
  - Optional one-off backfill: a script using `client.list_workflows("WorkflowType='OmexVerifyWorkflow' OR WorkflowType='RunsVerifyWorkflow'")`, querying `get_output` for `owner_sub`, and inserting rows. **Not in core scope** (D8).
- **Ledger rows outlive Temporal history.** That is intended. Step 5 turns an expired ID into an explicit 404 detail.
- **Persisting results** (`workflow_results` into `BiosimCompare`, so `GET` survives retention) is the natural next step. It needs either a workflow-side activity (worker drain / `workflow.patched`) or API write-through on first terminal `GET`. **Out of scope** (D8).
- **Deletion:** no delete endpoint exists for verifications, and none is added.

---

## 8. Error Handling and Edge Cases

| Case | Behavior |
|---|---|
| Caller-supplied `workflow_id_prefix` containing `/` | The resulting ID can't be addressed by `GET /verify/{workflow_id}`: FastAPI path params don't match `/`. **Pre-existing.** Optionally validate the prefix (reject `/`) in both POSTs. Listed as D9, not core scope. |
| Duplicate insert (same `workflow_id`) | Practically impossible (uuid4). A `DuplicateKeyError` still maps to the POST's 503 path. |
| Ledger insert OK, `start_workflow` raises | Delete only on definitive INVALID_ARGUMENT rejection. Reconcile all ambiguous outcomes by the same workflow ID and original input; retain the row even if the follow-up cannot find the workflow. Matching accepted start → 200; unresolved outcome → sanitized 503 with the ID. No blind resubmission. |
| Execution closes before its first worker task | On query failure, reconstruct FAILED from the first history event and described terminal state, checking the original owner/admin rule. |
| Ledger unavailable on `GET /verify` | GET works as before; only the expired-vs-unknown distinction is lost. |
| Ledger unavailable on `GET /verification_ids` | 503. |
| Ledger unavailable on `POST /verify/*` | 503 (new; §11). Checked before the OMEX upload / runs preflight, so no storage writes or upstream calls happen. |
| History purged between `describe` and `query` | `query` raises `RPCError` NOT_FOUND → same ledger fallback 404 as a `describe` miss (not 503). |
| Temporal visibility lag | N/A. The ledger is read-your-writes. |
| Legacy `owner_sub=None` workflows | Not in the ledger (pre-deploy), so not listed. Still retrievable by ID as today. |
| Admin caller | Sees all IDs, and gets the "no longer retained" detail for anyone's expired ID. |
| Workflow completed but query fails on replay (non-determinism after a bad worker deploy) | `WorkflowQueryFailedError` → 404 (unchanged semantics). Logged with `exc_info`. |
| `describe` OK, workflow `RUNNING`, output `IN_PROGRESS` | 200 `IN_PROGRESS` (no reconciliation). |
| Workflow `COMPLETED`, output `FAILED`/`RUN_ID_NOT_FOUND` (runs path early return) | Returned as-is. |
| Preflight: one run ID only | Skipped. The workflow handles it; the comparison is a trivial 1×1. |

---

## 9. Testing Plan

Follow existing patterns:
- API tests: `TestClient` + `patch("biosim_server.api.main.get_*")` + `app.dependency_overrides[get_current_user]`, as in `tests/api/test_main.py:93-108`.
- Users: `make_authenticated_user()` from `tests/fixtures/auth_fixtures.py`.
- Mongo tests: testcontainers fixtures.

### Unit Tests

**`tests/biosim_verify/test_verification_database.py` (new, Mongo testcontainer):**
1. Insert 3 records with distinct `created` → `list_verification_ids(None)` returns all 3, newest first.
2. Same `created` on two rows → tiebreak by `workflow_id` ascending (deterministic).
3. `list_verification_ids("auth0|a")` returns only rows owned by `a`.
4. Empty collection → `[]`.
5. Inserting a duplicate `workflow_id` raises (unique index).
6. `get_verification` hit and miss; `delete_verification` removes the row.

**`tests/biosim_verify/test_verify_status_reconcile.py` (new, pure):** `_reconcile_terminal_status`
7. `IN_PROGRESS` + `FAILED` / `TERMINATED` / `TIMED_OUT` / `CANCELED` → `FAILED` with `workflow_error` set.
8. `IN_PROGRESS` + `RUNNING` → unchanged.
9. `COMPLETED` / `RUN_ID_NOT_FOUND` + any execution status → unchanged.

### API / Integration Tests

**`GET /verification_ids`** (in `tests/api/test_main.py`, ledger mocked with `AsyncMock`):
10. Multiple IDs → 200, exact list in ledger order.
11. Exactly one ID → 200, one-element list.
12. No IDs → 200 `{"verification_ids": []}`.
13. *(Revised D2)* Anonymous caller → 200, ledger called with `None` (every ID).
14. *(Revised D2)* A sent token, valid or not, is neither validated nor used to scope → ledger still called with `None`.
15. *(Revised D2)* No token → 200, not 401. Also covered by the meta-test anonymous probe (`AuthMode.NONE`).
16. Ledger `None` → 503. Ledger raises → 503, and the detail doesn't contain the exception text.

Records with failed/incomplete status are listed like any other: the ledger doesn't filter by status. Cover this with a DB test (case 1) where rows have different `verify_type`s. Status isn't stored.

**`GET /verify/{workflow_id}`** (Temporal handle mocked: `describe` → `MagicMock(workflow_type=..., status=WorkflowExecutionStatus.X)`, `query` → `VerifyWorkflowOutput`):
17. Owner, `COMPLETED` → 200, full body round-trips `VerifyWorkflowOutput.model_validate`.
18. Unknown ID: `describe` raises `RPCError(NOT_FOUND)`, ledger miss → 404 generic. **Update** existing `test_get_output_not_found` (`test_main.py:93-108`): it currently drives 404 via `query.side_effect = Exception(...)`, which now yields 503 or 404 depending on type. Make it raise the NOT_FOUND `RPCError` from `describe`.
19. Expired ID: NOT_FOUND + ledger row owned by caller → 404 "no longer retained".
20. Expired ID, ledger row owned by someone else, non-admin caller → generic 404 (no existence leak).
21. `describe` returns `workflow_type="SimulationRunWorkflow"` → 404.
22. Workflow execution `FAILED`, query returns `IN_PROGRESS` → 200 `FAILED` with `workflow_error`.
23. Running workflow → 200 `IN_PROGRESS`, `workflow_results is None`.
24. Temporal client `None` → 503. `describe` raises `RPCError(UNAVAILABLE)` → 503.
25. Non-owner non-admin, `owner_sub` set → 403 (regression of existing behavior).
26. Malformed identifier: `workflow_id` has no format constraint in the code or docs, so there is no 422 case. Add a case for an ID containing an encoded `/` (`%2F`) → 404 from routing, documenting the D9 limitation.

**`POST /verify/omex` and `POST /verify/runs`** (extend `_verify_omex_mocks`, `test_main.py:291`, and the verify-runs test in `test_openapi_endpoints.py:354-374`):
27. Success → `ledger.insert_verification` awaited once with `workflow_id == response.workflow_id`, `owner_sub == user.sub`, correct `verify_type`, **before** `start_workflow`. Assert call order with a shared `MagicMock` parent or side-effect list.
28. Ledger insert raises → 503, `start_workflow` **not** called.
29. Definitive INVALID_ARGUMENT start rejection → 503 and ledger deletion. Ambiguous timeout/unavailability → reconcile same ID; matching accepted workflow → 200 with retained row; unconfirmed outcome (including a follow-up NOT_FOUND) → 503 with retained row. Regression tests inject a lost reply after a real accepted Temporal start for both POSTs.
30. Ledger `None` → 503.

**Meta-tests:** `tests/api/test_openapi_endpoints.py` passes with the new table entries (Step 7).

**Integration (existing, `@pytest.mark.integration`, GCS creds):** extend `test_runs_verify_and_get_output` (`test_main.py:200-235`) with a real Mongo ledger. Assert the started ID appears in `GET /verification_ids`. Optional; this needs `set_verification_database_service` in the test's service setup.

### Model Compatibility Tests

**`tests/biosim_verify/test_compatibility.py` (new, pure, for `find_common_datasets`):**
31. Same datasets and labels → all common.
32. Disjoint dataset names → empty. This is the "different model" case.
33. Same dataset name, different labels → excluded.
34. Partial overlap → only the shared datasets.
35. A run with an `HDF5File` that has no groups → empty. This is the "missing identity metadata" case.

**`generate_statistics_activity` (Step 8):** add `tests/biosim_verify/test_generate_statistics.py`. Call the activity function directly with `ActivityEnvironment` (`temporalio.testing`) and a patched `BiosimServiceRest.get_hdf5_data`.
36. Two runs with disjoint datasets → returns (no raise). Every cell has `error_message` containing "not found".
37. Partial overlap → shared datasets have `score`/`is_close`; others have `error_message`.
38. Regression: identical datasets → same output as today.

**Preflight (`POST /verify/runs`, API tests):**
39. Disjoint metadata → 400, detail names both run IDs. `start_workflow` and ledger insert not called.
40. Overlapping metadata → 200.
41. Metadata fetch 404 for one of three runs, remaining two overlap → 200. For one of two → preflight skipped → 200.
42. Cache hit via `get_biosimulator_workflow_runs_by_biosim_runid` → `get_hdf5_metadata` not called.
43. (If D7 = include) observables not in the common labels → 400.

Ambiguous identity (different archive hash, overlapping outputs) has no dedicated behavior because it is allowed. Case 40 covers it with differing `file_hash_md5` in the fixtures.

---

## 10. Documentation / OpenAPI Updates

| File | Change |
|---|---|
| `backend/biosim_server/api/main.py` | `summary`, `responses={...}` on the new and modified routes (Steps 5–6). The `VerificationIdsResponse.verification_ids` field description states the IDs are `workflow_id`s for `GET /verify/{workflow_id}`. |
| `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` | Regenerate: `cd backend && uv run python -m biosim_server.api.openapi_spec`. |
| `backend/CLAUDE.md` | Directory Structure: add `biosim_verify/database.py` (and `compatibility.py` if Step 9). MongoDB collections: `BiosimCompare` → "Verification ledger (workflow_id, owner, created)". Authentication section: add `GET /verification_ids` (required token; owner-scoped, admin sees all). Also correct the stale `Version: 0.4.0` if the reviewer agrees; optional. |
| `backend/OVERVIEW.md:210` | `BiosimCompare` row: purpose "Verification ledger", key fields `workflow_id, owner_sub, created`. |
| `backend/docs/auth0-tokens-claims-endpoints.md:197-198` | Add a `GET /verification_ids` row. Update the `GET /verify/{workflow_id}` row with 503 on Temporal outage. |
| `docs/workflows-architecture.md:365-368` | Update the `BiosimCompare` ER entity to the actual fields. Note in "Verification workflows" that POST writes a ledger row. |

---

## 11. Backwards-Compatibility Considerations

| Change | Compatible? | Notes |
|---|---|---|
| New `GET /verification_ids` | Additive | New operationId `list-verification-ids`, **public** (no `security` in OpenAPI; revised D2). Generated clients gain a method. |
| `GET /verify` Temporal outage 404 → **503** | **Behavior change** | Clients treating 404 as "not ready yet, retry" will now see 503. Arguably more correct. The deployed spec documents neither code. Mention in the release notes. |
| `GET /verify` failed workflow `IN_PROGRESS` → **`FAILED`** | **Behavior change (bug fix)** | Uses an existing enum value. Pollers stop instead of looping. |
| `GET /verify` 404 `detail` text | Changed | Exception text no longer echoed. Nothing in the repo parses it except `test_get_output_not_found`, which checks the ID substring; keep the ID in the message. |
| `POST /verify/*` 503 on Mongo outage | **Behavior change** | Previously independent of Mongo on the runs path. The OMEX path already required Mongo for `get_cached_omex_file_from_upload`. |
| `POST /verify/runs` 400 on no overlap (Step 9) | **Behavior change** | Requests that used to hang or fail slowly now fail fast. |
| `VerifyWorkflowOutput`, `VerifyWorkflowStatus` | **Unchanged** | Deliberately. They are Temporal payloads and the published contract. |
| Workflow code | **Unchanged** | No `NonDeterministicWorkflowError` risk, no drain for Steps 1–7, 9. |
| Activity code (Step 8) | Replay-safe | Worker image rebuild; no drain. |
| Mongo | New collection only | No migration. |
| Frontend | Unaffected | **[REPO]** No `/verify` callers in `frontend/` (`backend/CLAUDE.md` → Authentication confirms). |
| Deployed prod (pre-auth) | Note | Prod doesn't yet enforce auth on `/verify/*` [DOCS]. The first deploy of this work also ships the already-merged auth changes. |

---

## 12. Implementation Order and Dependencies

1. **Step 1** models → **Step 2** DB service + DB tests (1–6) → **Step 3** DI wiring.
2. **Step 5** `GET /verify` hardening + tests 7–9, 17–26. The reconcile, 503, and type-check parts don't depend on 1–3 and can land first. The ledger fallback needs Step 3.
3. **Step 4** POST ledger writes + tests 27–30.
4. **Step 6** `GET /verification_ids` + **Step 7** meta-test registration + tests 10–16.
5. **Step 10** docs + regenerated spec.
6. *PR boundary (recommended).* Core scope ships as a backend **minor** bump. New endpoint, API image only: `backend/scripts/bump-backend.sh minor`, then the release and deploy PRs per root `CLAUDE.md`.
7. **Step 8** activity hardening + tests 36–38. Worker image; no drain.
8. **Step 9** preflight + tests 31–35, 39–43. Can share the PR with Step 8.

Run `uv run ruff check .`, `uv run mypy biosim_server tests`, and `uv run pytest -m "not integration"` after each step.

---

## 13. Acceptance Criteria

- [x] `GET /verification_ids` without a token returns `200` and `{"verification_ids": [...]}`: every verification `workflow_id` started after deploy, newest first (revised D2).
- [x] With no verifications, the response is `200 {"verification_ids": []}`.
- [x] Every ID returned by `POST /verify/omex` or `POST /verify/runs` appears in the next `GET /verification_ids`.
- [x] Every ID in `GET /verification_ids` whose Temporal history is retained returns `200` from `GET /verify/{id}`: for anyone if it is ownerless (anonymous start, D10), otherwise for its owner or an admin (anonymous 401, other user 403).
- [x] *(D10)* `POST /verify/omex`, `POST /verify/runs` and `GET /verify/{id}` work with no `Authorization` header: anonymous starts are ownerless (OMEX stored `public`), and a present-but-invalid token is 401, never anonymous.
- [x] `GET /verify/{id}` for a workflow whose Temporal execution failed, terminated, timed out, or was canceled returns `workflow_status: "FAILED"` with a non-null `workflow_error`.
- [x] `GET /verify/{id}` returns `503` (not 404) when Temporal is unreachable or the client is uninitialized.
- [x] `GET /verify/{id}` returns `404` for unknown IDs and for non-verification workflow IDs. For an owner's expired ID, the detail distinguishes "no longer retained".
- [x] No 404/503 `detail` contains raw exception text.
- [x] `POST /verify/*` returns `503` and starts no workflow when the ledger insert fails.
- [x] `tests/api/test_openapi_endpoints.py` passes with `list-verification-ids` registered as `AuthMode.NONE` (revised D2).
- [x] `VerifyWorkflowOutput` / `VerifyWorkflowStatus` schemas generated from baseline and final source are byte-identical. The original checked-in OpenAPI was stale (missing the already-existing `owner_sub`); regeneration corrects it. See the execution report.
- [x] *(Step 8)* `generate_statistics_activity` with runs that have disjoint datasets returns normally, with `error_message` on every affected cell.
- [x] *(Step 9)* `POST /verify/runs` with ≥ 2 runs whose HDF5 metadata share no identically-labelled dataset returns `400`, and neither the ledger nor Temporal is called.
- [x] A retained terminal execution closed before its first worker task returns FAILED with original settings and enforced ownership.
- [x] Lost start replies do not delete accepted workflows from the ledger or start a second workflow.
- [x] `ruff`, `mypy --strict` (source + tests), and `pytest -m "not integration"` all pass.

---

## 14. Open Questions / Decisions

| ID | Question | Recommendation | Impact if decided otherwise |
|---|---|---|---|
| **D1** | Source of truth for `/verification_ids`: Mongo ledger (`BiosimCompare`) or Temporal visibility? | **Ledger.** Enables owner scoping, follows repo precedent, testable. | Temporal visibility removes Steps 1–4 but needs an `OwnerSub` search attribute registered on every cluster (outside this repo) for scoping, and bounds the list by retention. |
| **D2** | Who sees which IDs? | ~~Owner-scoped; admins see all.~~ **Revised 2026-09-30 (owner decision): public, no token, every ID**, to match the legacy API, which has no authentication. | Exposes every ID (including caller-chosen prefixes) and the verification count to anyone. Results of token-started verifications stay owner-or-admin. Anonymous ones are public by design (D10). `VerificationDatabaseService.list_verification_ids(owner_sub)` keeps its filter parameter, so owner scoping can be restored without a schema change. |
| **D3** | Response shape | `{"verification_ids": [str]}` | A bare `list[str]` can't grow pagination or metadata without a breaking change. |
| **D4** | Pagination for `/verification_ids` | Not now (not a stated requirement). Revisit if admin lists get large. | If required now, reuse the `page` / `perPage` query-param style of `GET /projects` (`projects/router.py:100-101`) rather than inventing a new one. |
| **D5** | Status for an expired-but-known ID | 404 with a distinct `detail` (keeps the contract) | 410 Gone is more precise but is a new documented status. |
| D5a | Ledger rows for uncertain starts or failed rejection cleanup | Retain uncertain outcomes, reconcile by ID, and delete only definitive rejections. A retained row with no history can still receive the existing "no longer retained" detail; this does not prove a workflow started. | Explicit start-outcome persistence and background reconciliation remain follow-up work. |
| **D6** | Add model-identity fields (`omex_file_hash_md5`, `biosimulations_run_ids`) to the ledger and/or a `warnings` field to the response for hash mismatch? | No for now. Overlap preflight only. | A response `warnings` field changes the Temporal payload schema and the published contract. |
| D7 | Should the preflight also reject when requested `observables` aren't in the common labels? | **Adopted.** Reject only when *none* of the requested observables are among the common `sedmlDataSetLabels`. | **Correction (post-audit):** `CompareSettings.observables` is stored in the workflow input but read by no workflow or activity, so it does not filter results today. Without D7 the request would proceed and return the full comparison, silently ignoring `observables`. D7 therefore *validates* the parameter (catching requests for outputs the runs do not have) but does not *apply* it. The `POST /verify/runs` parameter description says so. Applying `observables` as a result filter is a separate follow-up (activity change → worker image). |
| **D10** | Must `/verify/*` require a token? | **Decided 2026-09-30 (owner): no.** The endpoints serve the legacy API, which has no authentication. `POST /verify/omex`, `POST /verify/runs` and `GET /verify/{workflow_id}` use `get_optional_user`, the same pattern as `POST /simulations/run` (P1 #9 Option B). No token means an ownerless verification (OMEX stored `public`) that anyone can read; a valid token means an owned one (owner-or-admin: anonymous 401, other user 403). An invalid token is 401, never anonymous. Rate limiting is unchanged (anonymous callers keyed on IP). | Anonymous results are public and enumerable through `GET /verification_ids`. Returning to required auth means switching back to `get_current_user`; stored rows need no migration. |
| D8 | Backfill pre-deploy workflows into the ledger / persist results so `GET` survives retention? | Out of scope; follow-up. | Backfill: script over `list_workflows`. Result persistence: an activity (needs a worker drain / `workflow.patched`) or API write-through. |
| D9 | Validate `workflow_id_prefix` (reject `/`) on both POSTs? | Yes, as a small separate change. | Unaddressable IDs can still be created and listed. |
| — | **[ASSUMPTION]** Temporal namespace retention on GKE/RKE | Confirm with `temporal operator namespace describe default` | Determines how long listed IDs stay resolvable and how fast the historical gap closes. |
