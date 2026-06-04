# Simulation Workflows & Activities — Architecture

A high-level design reference for the Temporal workflows and activities that
back the **verification** (`/verify/*`) and **simulation-run** (`/simulations/*`)
subsystems, including how they relate, what has changed during the run-listing
convergence work, and what's proposed next.

Companion docs:
- [`simulation-runs-api-plan.md`](simulation-runs-api-plan.md) — the original `/simulations/runs` plan.
- [`simulation-runs-convergence-plan.md`](simulation-runs-convergence-plan.md) — sequenced PR plan (PR0–PR3) and external-consumer TODO.

---

## Domain primitives

**Temporal model.** Workflows are deterministic, replayable orchestrators that
must do **no I/O** (no DB, no network, no clocks, no random). Activities are the
**side-effect boundary** — they own all I/O (HTTP, MongoDB, GCS, files, time).
Any database write in this system happens inside an activity.

**The per-run unit.** "Submit one OMEX archive to biosimulations.org for one
simulator, poll until terminal, fetch HDF5 metadata, and persist the result" is
the reusable unit. It is implemented today by **`OmexSimWorkflow`** (a child
workflow) backed by **`submit_biosim_simulation_run_activity`** (a monolithic
activity that bundles submit + poll + HDF5 + save). PR2 makes this unit shared
by both `OmexVerifyWorkflow` and `SimulationRunWorkflow`.

**Two storage entities for "a simulation":**
- `BiosimulatorWorkflowRun` (collection `BiosimSims`) — the **computation/artifact** record.
  Keyed by `(file_hash_md5, image_digest, cache_buster)`. Many submissions can
  map to one of these (cache deduplication).
- `SimulationRunRecord` (collection `BiosimSimulationRuns`) — the **submission ledger**.
  One per `(user submission × simulator)`. Carries user-facing identity (`name`,
  `email`, `purpose`, timestamps, display status) and a reference to the
  computation via `biosimulations_run_id`.

---

## Verification workflows (current state, unchanged by the convergence)

Two entry points, both producing pairwise NxN comparison statistics:

| Endpoint | Workflow | Per-run mechanism |
|---|---|---|
| `POST /verify/omex` | `OmexVerifyWorkflow` | starts N `OmexSimWorkflow` children (one per simulator) |
| `POST /verify/runs` | `RunsVerifyWorkflow` | imports N pre-existing biosimulations runs via `get_existing_biosim_simulation_run_activity` |

```mermaid
flowchart LR
    subgraph "API"
        EP1["POST /verify/omex"]
        EP2["POST /verify/runs"]
    end
    subgraph "Temporal: parent workflows"
        OVW["OmexVerifyWorkflow"]
        RVW["RunsVerifyWorkflow"]
    end
    subgraph "Temporal: child per-run unit"
        OSW["OmexSimWorkflow x N"]
    end
    subgraph "Temporal: activities"
        SBA["submit_biosim_simulation_run_activity<br/>(monolithic: cache-check → submit → poll → HDF5 → save)"]
        GERA["get_existing_biosim_simulation_run_activity<br/>(import existing run)"]
        GSA["generate_statistics_activity<br/>(NxN pairwise comparison)"]
    end
    subgraph "Storage / External"
        DB_OMEX[("BiosimOmex")]
        DB_SIMS[("BiosimSims")]
        EXT(["biosimulations.org<br/>simdata API"])
    end
    EP1 --> OVW
    EP2 --> RVW
    OVW -- "start_child_workflow x N" --> OSW
    OSW -- "execute_activity" --> SBA
    RVW -- "execute_activity x N" --> GERA
    OVW -- "execute_activity" --> GSA
    RVW -- "execute_activity" --> GSA
    SBA -- "HTTP submit/poll/HDF5" --> EXT
    SBA -- "read cache, write result" --> DB_SIMS
    SBA -- "read OMEX path" --> DB_OMEX
    GERA -- "HTTP get run" --> EXT
    GERA -- "read/write" --> DB_SIMS
```

Key properties:
- `OmexSimWorkflow` (`biosim_runs/workflows.py`) is the **only place** that
  starts a fresh biosimulations.org run. It exposes a `@workflow.query` for its
  output but, today, the run_id is not populated on the queryable state until
  the monolithic activity returns at the end.
- `generate_statistics_activity` is shared by both verify workflows.
- `BiosimSims` is a **shared cache** — a workflow that finds a `SUCCEEDED`
  record for the same `(file_hash, image_digest, cache_buster)` reuses it
  instead of re-submitting.

---

## Simulation-run workflow — evolution

A user-facing path: the webapp submits an OMEX archive against one or more
simulators and later browses the runs in a table. This subsystem has gone
through three shapes; the convergence work is bringing it onto the verification
path's primitives.

### Shape A — original (pre-convergence)

`SimulationRunWorkflow` had its **own** submit/poll/save activity pair
(`submit_simulation_activity`, `poll_simulation_activity` in
`simulations/activities.py`) that duplicated the verify path's monolithic logic.

```mermaid
flowchart LR
    subgraph "API"
        EP["POST /simulations/run"]
        EPL["POST /simulations/runs<br/>(listing)"]
        EPS["GET /simulations/{id}<br/>(status)"]
    end
    subgraph "Temporal"
        SRW["SimulationRunWorkflow"]
        SS["submit_simulation_activity x N<br/>(DUPLICATE submit half)"]
        PS["poll_simulation_activity x N<br/>(DUPLICATE poll + save half)"]
        URS["update_run_status_activity"]
    end
    subgraph "Storage"
        DBR[("BiosimSimulationRuns<br/>(submission ledger)")]
        DB_SIMS[("BiosimSims<br/>(shared cache)")]
        EXT(["biosimulations.org"])
    end
    EP -- "start workflow" --> SRW
    EP -- "insert N records (status=CREATED)" --> DBR
    SRW -- "phase 1: x N parallel" --> SS
    SRW -- "phase 2: x N parallel" --> PS
    SS -- "HTTP submit" --> EXT
    SS -- "read cache" --> DB_SIMS
    PS -- "HTTP poll/HDF5" --> EXT
    PS -- "write result" --> DB_SIMS
    SRW -- "after both phases x N" --> URS
    URS -- "set SUCCEEDED/FAILED + biosim_run_id" --> DBR
    EPS -- "query workflow" --> SRW
    EPL -- "read & filter" --> DBR
```

The duplication was the core liability: two implementations of the same logic
(cache lookup, HTTP submit, polling loop, HDF5-404 retry, BiosimulatorWorkflowRun
insertion) drifting against each other.

### Shape B — PR2 / [#51](https://github.com/biosimulations/platform/pull/51) (open)

`SimulationRunWorkflow` now runs each simulator via a child **`OmexSimWorkflow`** —
the same per-run unit the verify path uses. The duplicate
`submit_simulation_activity` / `poll_simulation_activity` are **deleted**. The
monolithic `submit_biosim_simulation_run_activity` becomes the single shared
implementation.

```mermaid
flowchart LR
    subgraph "API"
        EP["POST /simulations/run"]
        EPL["POST /simulations/runs"]
        EPS["GET /simulations/{id}"]
    end
    subgraph "Temporal: parent"
        SRW["SimulationRunWorkflow"]
    end
    subgraph "Temporal: child per-run unit (SHARED with verify)"
        OSW["OmexSimWorkflow x N"]
    end
    subgraph "Temporal: activities"
        SBA["submit_biosim_simulation_run_activity<br/>(monolithic, SHARED)"]
        URS["update_run_status_activity"]
    end
    subgraph "Storage"
        DBR[("BiosimSimulationRuns")]
        DB_SIMS[("BiosimSims")]
        EXT(["biosimulations.org"])
    end
    EP --> SRW
    EP -- "insert N records (CREATED)" --> DBR
    SRW -- "start_child_workflow x N" --> OSW
    OSW -- "execute_activity" --> SBA
    SBA -- "HTTP" --> EXT
    SBA -- "read/write" --> DB_SIMS
    SRW -- "after children complete" --> URS
    URS -- "set SUCCEEDED/FAILED + biosim_run_id" --> DBR
    EPS -- "query workflow" --> SRW
    EPL -- "read & filter" --> DBR
```

What changed and what didn't:
- ✅ One orchestration unit (`OmexSimWorkflow`) for both paths.
- ✅ Duplicate submit/poll activities deleted.
- ✅ **Verification production path entirely untouched** — PR2's diff is only
  `simulations/*`, `worker_main.py`, `temporal_fixtures.py`.
- ⚠️ `biosimulations_run_id` only surfaces in the live `GET /simulations/{id}`
  status when each child completes (the monolithic activity bundles submit +
  poll, so the workflow learns the run_id only at the end). The listing isn't
  affected — it gets the id at completion either way.

### Shape C — proposed PR2.5 (activity split + early DB write)

The observation that motivates PR2.5: **activities are the canonical place to
write to a database in Temporal.** Once you split the monolithic activity, the
workflow has a moment between "I know the run_id" and "I'm done polling" — and
in that moment it can fire a small DB-write activity to record the run_id
**early**, persistently, in `SimulationRunRecord`. No child→parent signal
needed; just an activity doing what activities are for.

```mermaid
flowchart LR
    subgraph "API"
        EP["POST /simulations/run"]
        EPS["GET /simulations/{id}<br/>(hybrid read)"]
    end
    subgraph "Temporal: parent"
        SRW["SimulationRunWorkflow"]
    end
    subgraph "Temporal: child per-run unit (SHARED)"
        OSW["OmexSimWorkflow x N<br/>(now takes optional submission_run_id)"]
    end
    subgraph "Temporal: activities"
        SUB["submit_biosim_run_activity<br/>(NEW — submit half of split)"]
        URS["update_run_status_activity<br/>(EARLY when submission_run_id is set)"]
        POLL["poll_biosim_run_activity<br/>(NEW — poll + HDF5 + save half)"]
    end
    subgraph "Storage"
        DBR[("BiosimSimulationRuns")]
        DB_SIMS[("BiosimSims")]
        EXT(["biosimulations.org"])
    end
    EP --> SRW
    EP -- "insert N records (CREATED)" --> DBR
    SRW -- "start child x N (pass job_id as submission_run_id)" --> OSW
    OSW -- "1. execute" --> SUB
    SUB -- "HTTP submit" --> EXT
    SUB -- "read cache" --> DB_SIMS
    OSW -- "2. execute (if submission_run_id)" --> URS
    URS -- "write biosim_run_id (EARLY)" --> DBR
    OSW -- "3. execute" --> POLL
    POLL -- "HTTP poll + HDF5" --> EXT
    POLL -- "write result" --> DB_SIMS
    SRW -- "after children complete" --> URS
    EPS -- "query workflow (status)<br/>+ read DBR (biosim_run_id)" --> SRW
    EPS -- "(DB read)" --> DBR
```

Key properties:
- `OmexSimWorkflowInput` gains an optional `submission_run_id` (= the per-job
  UUID = `SimulationRunRecord.run_id`). The verify path passes `None` →
  step 2 above is skipped → **zero behavior change to verification**.
- The status endpoint becomes a hybrid read: workflow query for live status,
  DB record for run_id (and any other submission metadata).
- The monolithic activity is replaced by the split pair; `OmexSimWorkflow`,
  `OmexVerifyWorkflow`, and `SimulationRunWorkflow` all benefit from the cleaner
  decomposition.

### Sequence — the run lifecycle after PR2.5

```mermaid
sequenceDiagram
    autonumber
    participant U as Webapp
    participant API as FastAPI router
    participant DBR as BiosimSimulationRuns
    participant SRW as SimulationRunWorkflow
    participant OSW as OmexSimWorkflow<br/>(child, one per simulator)
    participant SUB as submit_biosim_run_activity
    participant URS as update_run_status_activity
    participant POLL as poll_biosim_run_activity
    participant DBS as BiosimSims
    participant EXT as biosimulations.org

    U->>API: POST /simulations/run (omex_id, name, simulators[], email, cache_buster?)
    API->>DBR: insert N records (status=CREATED, run_id=job_id)
    API->>SRW: start workflow (job_ids[], cache_buster)
    API-->>U: 202 { processing_id, jobs: [{run_id, status=processing}, ...] }

    par per simulator
        SRW->>OSW: start child (submission_run_id=job_id)
        OSW->>SUB: execute submit (cache_buster, simulator)
        SUB->>DBS: read cache
        alt cache hit (SUCCEEDED)
            SUB-->>OSW: cached biosimulations_run_id
        else cache miss
            SUB->>EXT: POST /runs (submit)
            EXT-->>SUB: new biosimulations_run_id
        end
        OSW->>URS: write biosim_run_id (EARLY, while still running)
        URS->>DBR: set run_id (status still CREATED)
        OSW->>POLL: execute poll (run_id)
        POLL->>EXT: GET /runs/{id} (poll until terminal)
        POLL->>EXT: GET HDF5 metadata
        POLL->>DBS: insert BiosimulatorWorkflowRun
        POLL-->>OSW: full record
        OSW-->>SRW: OmexSimWorkflowOutput (status, run record)
    end

    SRW->>URS: write terminal status (SUCCEEDED or FAILED)
    URS->>DBR: update status (+ runtime, etc.)

    loop polling
        U->>API: GET /simulations/{processing_id}
        API->>SRW: query get_status
        API->>DBR: read run_ids by processing_id
        API-->>U: ConglomerateStatus (status from workflow, run_ids from DB)
    end

    U->>API: POST /simulations/runs (listing query)
    API->>DBR: filter/sort/paginate
    API-->>U: { runs[], pagination }
```

Steps 4–6 (the early DB write) and step 12 (hybrid status read) are the PR2.5
delta. Everything above the dashed band is in #51 today.

---

## Storage layer

```mermaid
erDiagram
    BiosimOmex {
        string file_hash_md5 PK
        string omex_gcs_path
        string uploaded_filename
        int    file_size
    }
    BiosimSims {
        string file_hash_md5
        string image_digest
        string cache_buster
        object omex_file
        object simulator_version
        object biosim_run "embeds BiosimSimulationRun (id, status, cpus, memory, runtime, ...)"
        object hdf5_file
    }
    BiosimSimulationRuns {
        string run_id PK "= per-(submission × simulator) job UUID"
        string processing_id "= parent workflow id"
        string biosimulations_run_id FK "ref BiosimSims.biosim_run.id (manual)"
        string name "user-supplied label"
        string simulator
        string simulator_version
        string email
        string purpose
        string status "CREATED | SUCCEEDED | FAILED"
        string cache_buster
        date   submitted
        date   updated
    }
    BiosimCompare {
        string workflow_id PK
        object comparison_statistics
    }
    BiosimSims ||--o| BiosimOmex : "file_hash_md5"
    BiosimSimulationRuns }o--|| BiosimSims : "biosimulations_run_id → biosim_run.id (many-to-one; cache dedup)"
```

Two intentional choices to call out:

1. **`BiosimSims` is a deduplicated *computation/artifact* table.** With caching
   on, many submissions of the same `(omex, simulator, cache_buster)` map to
   one `BiosimulatorWorkflowRun`. That's the result-provenance + cache role.
2. **`BiosimSimulationRuns` is the *submission ledger.*** One per user action,
   even on a cache hit. It carries identity (`email`, user-chosen `name`,
   `purpose`), submission timestamps, and a *display* status
   (`CREATED/SUCCEEDED/FAILED`) — three categories the listing UI actually uses.

In MongoDB the relationship is a **manual reference**, not a foreign-key
constraint (Mongo has no FKs). Orphans (a submission with no completed
computation yet) are normal mid-flight states — an app-side left join yields
`null` computed fields and the row renders as "still running."

---

## PR3 preview — normalize the submission ledger

PR3 will slim `SimulationRunRecord` to submission-owned fields plus the
`biosimulations_run_id` FK, and have the listing **app-side join** to
`BiosimulatorWorkflowRun` for computed/derived fields (digest, cpus, sizes,
runtime, …). The current denormalization (copying those columns onto every
submission row and writing them back via `update_run_status_activity`) is the
real "two-table awkwardness" — the *existence* of two tables is correct; the
*duplicated columns* are the smell.

```mermaid
flowchart LR
    subgraph "after PR3"
        SUB["SimulationRunRecord<br/>(slim: run_id, processing_id, name, email,<br/>purpose, status, submitted, updated,<br/>cache_buster, biosimulations_run_id)"]
        COMP["BiosimulatorWorkflowRun<br/>(computation + artifacts +<br/>biosim_run metadata: cpus, memory,<br/>runtime, projectSize, ...)"]
        LIST["POST /simulations/runs"]
    end
    LIST -- "1. filter/sort/paginate submissions" --> SUB
    LIST -- "2. batch fetch by biosimulations_run_id $in [...]" --> COMP
    LIST -- "3. stitch into SimulationRun DTO" --> LIST
```

The frontend's filterable/sortable columns are all submission-owned, so the
listing UX needs no DB-side joins — a clean two-query app-side join is enough.

---

## Open items

- **biosim-client impact analysis.** The Python client at
  `../biosim-client` (https://github.com/biosimulations/biosim-client) is an
  external consumer of this platform's API surface and may be affected by
  PR1's `BiosimSimulationRun` additions and PR3's listing reshape. Inventory →
  diff → versioning/contract → consider monorepo absorption. See the TODO
  section in [`simulation-runs-convergence-plan.md`](simulation-runs-convergence-plan.md);
  do the inventory before PR3 lands.
- **Hybrid status read for PR2.5.** Enrich
  `GET /simulations/{processing_id}` to read `SimulationRunRecord`s for the
  processing_id and fill `biosimulations_run_id` per job — small router change,
  no contract change.
- **No real auth.** Owner-scoped queries (`type=user` + `user=<email>`) trust
  the supplied email today. Out of scope for the convergence work; flagged so
  it doesn't get lost.
