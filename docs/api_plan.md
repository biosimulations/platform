# `ProjectSummary` extension — architecture plan

**Status:** implemented — see `backend/biosim_server/common/biosim_api/`, `backend/biosim_server/legacy_proxy/`, and the `ProjectDetail` aggregate in `projects/`.
**Scope:** extend the platform backend's representation of biosimulations.org project & run detail data across eight upstream endpoints.
**Repo facts in this document were traced from the working tree; everything labelled *recommendation* is a proposal.**

> **Correction to the framing of the request:** `ProjectSummary` is a **Pydantic v2 `BaseModel`, not a dataclass** (`backend/biosim_server/common/json_types.py:85`), and the file is currently **untracked** — brand-new, uncommitted work. There are no dataclasses in this repo's model layer. This plan stays on Pydantic.

---

## 1. Executive Summary

- **What `ProjectSummary` is today (fact):** a *wire-contract mirror* of the upstream `GET /projects/{id}/summary` envelope, with `extra="allow"` so unmodeled upstream keys round-trip untouched. It is not a domain model, not a cache, and not a view model. It is constructed in exactly one place (`BiosimServiceRest.get_project_summary`) and consumed in exactly one place (`projects/router.py:129`), where FastAPI serializes it back out `by_alias=True`. Its whole job is "validate the fields we care about, pass the rest through unharmed."

- **The single most important design decision: do not flatten.** `ProjectSummary` should stay a faithful typed mirror of *one* endpoint. The other seven endpoints get their own sibling response models and their own passthrough routes. An optional composition root (`ProjectDetail`) can assemble a subset of them for callers that want one round trip — but it must be a *separate* type, not a mutated `ProjectSummary`.

- **Rationale, from the repo, not taste:** the route is a proxy whose value proposition is *exact contract fidelity* with what the frontend already consumes (`docs/Biosimulations Platform Backend Study Guide.md:225` — "Keep the existing proxy"). Flattening `/logs`, `/specifications`, `/results` into the same object would (a) break the 1:1 correspondence with the upstream envelope that `extra="allow"` exists to preserve, (b) turn one HTTP call into 3 + N (one `/results` call *per SedPlot2D output*), and (c) attach potentially multi-megabyte float arrays to a metadata request.

- **The biggest concrete finding — one of the eight endpoints is redundant.** `GET /runs/{id}/summary` returns the *same object* that already sits at `GET /projects/{id}/summary` → `simulationRun`. Repo evidence: `frontend/app/pages/projects/[id].vue:87` assigns `run_summary.value = projSumm.simulationRun` and types it `SimulationRunSummary`, which is the identical type `frontend/app/pages/runs/[id].vue:127` assigns from `/runs/{id}/summary`. **In project context, `/runs/{id}/summary` should never be called.** It is the entry point only for the *run* page, where there is no project.

- **What belongs directly on `ProjectSummary`:** only what the upstream envelope actually carries — `id`, `created`, `updated`, and `simulationRun`. Today `created`/`updated` are *missing from the model* and survive only as `extra` (the frontend reads them at `projects/[id].vue:260-264`), and `simulationRun.id` / `.name` are likewise untyped extras even though `.id` is **the identifier every other endpoint keys off**. That is a live gap, not a hypothetical.

- **What belongs in supporting objects:** a shared `RunMetadata` (used by both `/projects/{id}/summary` and `/runs/{id}/summary` — same shape), a shared `LabeledIdentifier` (already exists), a shared `LogMessage` for the `{type, message}` pair that `skipReason` and `exception` both use at four nesting levels, and a shared `LogEntry` base for the `{status, algorithm, output, skipReason, exception}` quintuple repeated at run/document/task/output level. The log payload is the strongest case in the whole feature for reusable nested types — five near-identical structures collapse to two.

- **What stays lazy and separately fetched:** `/results/{id}/{outputId}?includeData=true` (unbounded numeric payload, fetched per-plot, and `useVisualizations.ts:98` already fires these individually after the page renders), and `/ontologies/KISAO/{id}` (per-algorithm, highly cacheable, and the repo already vendors a KISAO table). Neither should ever be eagerly folded into a project or run response.

- **KISAO has a trap.** `biosim_server/common/kisao_data.py` already ships all 561 terms — but only `name` and `ancestors`. The required surface asks for `url` and `description`, which the local table does not have. Worse, the local keys are `KISAO:0000019` while the log payload's `algorithm` field uses `KISAO_0000019` (proof: `LogAlgorithm.vue:16` does `kisaoId.replace('_', ':')`). Recommendation: proxy upstream with a TTL cache, fall back to the local table's `name` plus a derived OLS URL, and normalize the separator in one place.

- **Backwards compatibility is cheap here, if you keep one rule:** every field added to `ProjectSummary` and its children must be optional with a default. The existing test fixture (`tests/projects/test_project_summary.py:26`) has no `created`/`updated`, and real in-flight runs omit half the run block. There is already one latent landmine: `ProjectSummarySimulationRun.run` is **required** (`json_types.py:82`), so any upstream project lacking a `run` object 500s the route today. Fix that in the same pass.

- **Sequencing:** split the growing `json_types.py` into a `common/biosim_api/` package *first* (it is uncommitted, so the rename costs nothing), then widen `ProjectSummary`, then add one endpoint model + client method + route per phase. Every phase leaves the suite green and ships independent value.

---

## 2. Current Architecture Findings

### `ProjectSummary` definition — `backend/biosim_server/common/json_types.py`

Untracked/uncommitted. Contents (facts):

| Symbol | Line | Notes |
|---|---|---|
| `ModelFormat(StrEnum)` | 15 | **Unused anywhere.** Dead. |
| `Simulator(StrEnum)` | 22 | **Unused.** Also `VirtualCell = " virtual cell"` — leading space, almost certainly a typo. |
| `RunStatus(StrEnum)` | 33 | **Unused.** Duplicates `BiosimSimulationRunStatus` (`biosim_runs/models.py:68`) with a stated "avoid circular import" rationale. |
| `LabeledIdentifier` | 47 | `{uri, label}`, both `str \| None`. Used by metadata. |
| `ProjectSummaryMetadata` | 55 | `abstract`, `description`, `creators[]`, `keywords[]`, `thumbnails[str]`. **No `citations`, no `encodes`.** |
| `ProjectSummaryRun` | 67 | `project_size`/`results_size` only. **No `simulator`.** |
| `ProjectSummarySimulationRun` | 76 | `metadata[]` + `run` (**required**). **No `id`, no `name`.** |
| `ProjectSummary` | 85 | `id: str` + `simulation_run` (aliased `simulationRun`). **No `created`, no `updated`.** |

Every model carries `ConfigDict(populate_by_name=True, extra="allow")`.

### Construction sites

Exactly one: `BiosimServiceRest.get_project_summary` → `ProjectSummary.model_validate(payload)` (`biosim_runs/biosim_service.py:202`). Plus the test mock `tests/fixtures/biosim_service_mock.py:91`, which serves a preloaded dict.

### Consumption sites

Exactly one: `projects/router.py:129` `get_project_summary` — declared as the return annotation, so FastAPI uses it as the response model and serializes `by_alias=True`. Error mapping already handles 404 → 404, other 4xx → forwarded, 5xx → 502, transport → 502 (deliberately not echoing the upstream host), and `None` service → 503.

### API client — `biosim_runs/biosim_service.py`

`BiosimService` ABC + `BiosimServiceRest` + `BiosimServiceMock`. Existing methods relevant here:

| Method | Endpoint | Returns | Status |
|---|---|---|---|
| `get_sim_run` | `GET /runs/{id}` | `BiosimSimulationRun` | **Exists**, parses `id/name/status/simulator/simulatorVersion/submitted/updated/projectSize/resultsSize` (`_sim_run_from_response`, line 23) |
| `get_sim_run_logs` | `GET /logs/{id}` | `dict[str, Any]` | **Exists, untyped.** Consumed by `simulations/router.py:364` → `JobLogs.logs` |
| `get_project_summary` | `GET /projects/{id}/summary` | `ProjectSummary` | **Exists**, just typed in the working tree |
| `get_hdf5_metadata` / `get_hdf5_data` | simdata API | `HDF5File` / `Hdf5DataValues` | Exists — *different* API, not `/results` |
| `get_simulator_versions` | biosimulators API | `list[BiosimulatorVersion]` | Exists, `@cached(ttl=3600, SimpleMemoryCache)` — the caching precedent |

**No client method exists for `/runs/{id}/summary`, `/files/{id}`, `/specifications/{id}`, `/results/{id}/{outputId}`, or `/ontologies/KISAO/{id}`.** Those five are called *directly from the browser* against `legacy_api_url`.

### Where the required fields are parsed today

Not in Python — in Vue, against `https://api.biosimulations.org`:

| Endpoint | Frontend caller |
|---|---|
| `/runs/{id}` | `pages/runs/[id].vue:131` |
| `/runs/{id}/summary` | `pages/runs/[id].vue` (same block) |
| `/projects/{id}/summary` | `pages/projects/[id].vue:84` |
| `/files/{id}` | `pages/projects/[id].vue:90`, `pages/runs/[id].vue` |
| `/specifications/{id}` | `pages/projects/[id].vue:91` |
| `/logs/{id}` | `pages/runs/[id].vue`, rendered by `components/SimulationLogs.vue` |
| `/results/{id}/{outputId}?includeData=true` | `composables/useVisualizations.ts:98`, `components/DesignChart.vue:163` |
| `/ontologies/KISAO/{id}` | `components/LogAlgorithm.vue:36` |

**The authoritative shapes already exist as TypeScript** and should be mirrored, not reinvented: `frontend/app/models/simulation.ts` (`SimulationRunSummary`, `RunDetails`, `SimulatorDetails`, `ProjectFile`, `SimulationRunSedDocument`) and `frontend/app/models/sedml.ts` (`SedPlot2D`, `SedCurve`, `SedStyle`, `SedLineStyle`, `SedMarkerStyle`, `SedReport`, `SedDataSet`, `SedAxisScale`, and the `_type` literals).

### Adjacent platform data

`projects/database.py` and `projects/search.py` read the biosimulations-owned Mongo collections `Projects`, `Metadata`, `Specifications`, `Simulation Runs` directly. `_model_format` (`database.py:163`) and `_image_url` (`database.py:175`) already parse SED-ML model languages and thumbnail→URL, and `ProjectStub`'s docstring (`projects/models.py:46`) explicitly says `model_format`/`image_url` "require a join beyond `projectSummary` (Specifications / Files)". `biosim_server/common/kisao_data.py` vendors `KISAO_TERMS: dict[str, KisaoTermData]` with `name` + `ancestors` only.

### Conventions observed

- Pydantic v2 (`>=2.11`), `mypy --strict`, ruff. No dataclasses, no `TypedDict` in the model layer (one exception: `KisaoTermData`).
- `StrEnum` for closed vocabularies (`BiosimSimulationRunStatus`).
- **Two competing alias styles:** `json_types.py` uses `Field(alias=...)` + `populate_by_name=True`; `simulations/models.py` and `projects/models.py` use `Field(serialization_alias=...)`. For a *passthrough* model you need both directions, so `alias=` is correct here — but the divergence should be documented rather than left ambient.
- **Datetimes are strings on the wire** (`BiosimSimulationRun.submitted: Optional[str]`, `ProjectStub.created: str`) with an `_iso()` normalizer for Mongo-sourced values. `SimulationRunRecord` uses real `datetime` for platform-owned rows.
- `extra="allow"` on upstream mirrors; `extra="forbid"` on request bodies (`RunSimulationRequest`).
- Optional-with-default is the house style for anything upstream might omit, with a comment saying why.

### Tests

`tests/projects/test_project_summary.py` — 11 tests: route-level (typed body, camelCase serialization, anonymous access, no credential forwarding, 404/4xx/502/503 mapping, `/projects/stats` not shadowed) and client-level (upstream URL, id quoting, nested parse incl. a null/empty second metadata item). `_assert_parsed_summary` asserts exactly the seven currently-modeled nested paths. `tests/fixtures/biosim_service_mock.py` holds `project_summaries: dict[str, ProjectSummary]`.

---

## 3. Recommended Object Model

```text
common/biosim_api/                      (new package; replaces json_types.py)
│
├── common.py
│   ├── LabeledIdentifier               {uri, label}          [moved, unchanged]
│   └── LogMessage                      {type, message}       [new; shared by skipReason + exception]
│
├── projects.py
│   └── ProjectSummary                  GET /projects/{id}/summary        [WIDENED]
│       ├── id: str
│       ├── created: str | None                                [NEW - typed]
│       ├── updated: str | None                                [NEW - typed]
│       └── simulation_run: SimulationRunSummary               [RETYPED -> shared]
│
├── runs.py
│   ├── SimulationRunSummary            GET /runs/{id}/summary   == projects' simulationRun
│   │   ├── id: str | None                                     [NEW - typed]
│   │   ├── name: str | None                                   [NEW - typed]
│   │   ├── submitted / updated: str | None                    [NEW]
│   │   ├── metadata: list[RunMetadata]
│   │   └── run: RunDetails | None                             [NOW OPTIONAL]
│   ├── RunMetadata                     (was ProjectSummaryMetadata)
│   │   ├── abstract / description: str | None
│   │   ├── creators / keywords / citations / encodes: list[LabeledIdentifier]
│   │   └── thumbnails: list[str]
│   ├── RunDetails                      (was ProjectSummaryRun)
│   │   ├── project_size / results_size: int | None
│   │   └── simulator: SimulatorDetails | None                 [NEW]
│   ├── SimulatorDetails                {id, name, version, digest, url}   [NEW]
│   └── RunRecord                       GET /runs/{id}   -- flat, NOT the summary
│       └── {id, name, simulator, simulator_version, submitted, updated, status}
│
├── files.py
│   └── ProjectFile                     GET /files/{id} -> list[ProjectFile]
│       └── {format, location, size, url, ...}
│
├── sedml.py                            GET /specifications/{id}
│   ├── SedDocumentSpec  {id, tasks[], outputs[]}
│   ├── SedTaskSpec      {model: SedModelRef}
│   ├── SedModelRef      {language: SedModelLanguage}
│   ├── SedModelLanguage {acronym, name, sedml_urn}
│   ├── SedOutput = Annotated[SedReport | SedPlot2D | SedPlot3D, Discriminator("_type")]
│   ├── SedPlot2D        {id, name, x_scale, y_scale, curves[]}
│   ├── SedCurve         {id, name, x_data_generator, y_data_generator, style}
│   ├── SedStyle         {base, line: SedLineStyle, marker: SedMarkerStyle}
│   ├── SedLineStyle     {color, thickness, type}
│   ├── SedMarkerStyle   {type, size, fill_color, line_color, line_thickness}
│   └── SedReport        {id, name, data_sets[]: SedDataSet{id, label, name}}
│
├── logs.py                             GET /logs/{id}
│   ├── LogEntry (base)  {status, algorithm, output, skip_reason, exception}
│   ├── RunLog(LogEntry)          └── sed_documents: list[SedDocumentLog]
│   ├── SedDocumentLog(LogEntry)  ├── location
│   │                             ├── tasks:   list[SedTaskLog]
│   │                             └── outputs: list[SedOutputLog]
│   ├── SedTaskLog(LogEntry)      └── id
│   └── SedOutputLog(LogEntry)    ├── id
│                                 └── data_sets: Any | None
│
├── results.py                          GET /results/{id}/{outputId}?includeData=true
│   ├── OutputResults    {output_id, data: list[ResultDatum]}
│   └── ResultDatum      {id, label, values: list[float] | Any}
│
└── ontology.py                         GET /ontologies/KISAO/{id}
    └── KisaoTerm        {id, name, url, description}

projects/models.py
└── ProjectDetail                       [NEW, OPTIONAL - Phase 8 composition root]
    ├── summary:        ProjectSummary          (mandatory, eager)
    ├── files:          list[ProjectFile]       (best-effort, eager, [] on failure)
    ├── specification:  SedDocumentSpec | None  (best-effort, eager)
    └── log:            RunLog | None           (best-effort, conditional)
        # results and KISAO are NEVER embedded here
```

### Responsibilities

- **`ProjectSummary`** — sole mirror of the project-detail envelope. Owns nothing but `id`/`created`/`updated` and delegation to `SimulationRunSummary`.
- **`SimulationRunSummary`** — one type serving two endpoints (`/runs/{id}/summary` *and* the `simulationRun` slot). This is the single most valuable dedup in the design; it is justified by direct repo evidence, not inference.
- **`RunRecord`** — deliberately *separate* from `SimulationRunSummary`. `/runs/{id}` is flat with `simulator: str` (an id like `"copasi"`); the summary nests `run.simulator: SimulatorDetails` with a display `name`. Different semantics, different type. Note `BiosimSimulationRun` (`biosim_runs/models.py:80`) **already models `/runs/{id}`** — prefer extending it over adding `RunRecord` (see §5).
- **`LogEntry`** — the reuse win: `status/algorithm/output/skipReason/exception` appear identically at run, document, task, and output level in `SimulationLogs.vue`. One base class, four subclasses adding only their own discriminating fields.
- **`LogMessage`** — `skipReason` and `exception` are both `{type, message}` (`SimulationLogs.vue:17,21`). One model, two field names.
- **`ProjectDetail`** — pure composition, no logic of its own beyond partial-failure tolerance. Additive; the existing `/summary` route is untouched.

---

## 4. Complete Field Mapping Matrix

Type column: `str?` means `str | None = None`. `[T]` means `list[T] = Field(default_factory=list)`.

### `GET /runs/{id}` → `BiosimSimulationRun` (existing, extend)

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Overlaps | Notes |
|---|---|---|---|---|---|---|---|
| `/runs/{id}` | `id` | `id` | `str` | `BiosimSimulationRun` | required | run summary `.id` | **Already parsed** (`biosim_service.py:30`). Has a `field_validator` rejecting dashes. |
| `/runs/{id}` | `name` | `name` | `str` | `BiosimSimulationRun` | required | run summary `.name` | Already parsed. |
| `/runs/{id}` | `simulator` | `simulator_id` | `str?` | `BiosimSimulationRun` | **add** | `run.simulator.id` | Today consumed transiently (`biosim_service.py:100`) then discarded into `simulator_version`. Store it. |
| `/runs/{id}` | `simulatorVersion` | `simulator_version_string` | `str?` | `BiosimSimulationRun` | **add** | `run.simulator.version` | ⚠️ **Naming conflict:** `simulator_version` on this model is already a `BiosimulatorVersion` *object*. Do not reuse the name. |
| `/runs/{id}` | `submitted` | `submitted` | `str?` | `BiosimSimulationRun` | existing | run summary `.submitted` | Already parsed. ISO-8601 string, per the model's own comment. |
| `/runs/{id}` | `updated` | `updated` | `str?` | `BiosimSimulationRun` | existing | run summary `.updated`, project `.updated` | Already parsed. **Not** the project's `updated`. |
| `/runs/{id}` | `status` | `status` | `BiosimSimulationRunStatus` | `BiosimSimulationRun` | required | log `.status` | Already parsed. **Not** the log status. |

### `GET /runs/{id}/summary` → `SimulationRunSummary`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Overlaps | Notes |
|---|---|---|---|---|---|---|---|
| `/runs/{id}/summary` | `id` | `id` | `str?` | `SimulationRunSummary` | yes | `/runs/{id}.id`, project `simulationRun.id` | **The key identifier for every other endpoint.** Currently untyped extra. |
| `/runs/{id}/summary` | `name` | `name` | `str?` | `SimulationRunSummary` | yes | `/runs/{id}.name` | Currently untyped extra. |
| `/runs/{id}/summary` | `metadata[0].abstract` | `abstract` | `str?` | `RunMetadata` | yes | project path | Exists. Index `[0]` is a *consumer* convention — model the whole list. |
| `/runs/{id}/summary` | `metadata[0].creators[].label` | `creators[].label` | `[LabeledIdentifier]` | `RunMetadata` | list may be `[]` | project path | Exists. |
| `/runs/{id}/summary` | `metadata[0].description` | `description` | `str?` | `RunMetadata` | yes | project path | Exists. May contain HTML (`projects/[id].vue:71` strips tags). |
| `/runs/{id}/summary` | `metadata[0].keywords[].label` | `keywords[].label` | `[LabeledIdentifier]` | `RunMetadata` | `[]` | project path | Exists. |
| `/runs/{id}/summary` | `metadata[0].thumbnails[0]` | `thumbnails` | `[str]` | `RunMetadata` | `[]` | project path | Exists. Bare archive filename, **not** a URL — see §7 Thumbnail. |
| `/runs/{id}/summary` | `run.projectSize` | `project_size` | `int?` | `RunDetails` | yes | project path | Exists as `ProjectSummaryRun.project_size`. Bytes. |
| `/runs/{id}/summary` | `run.resultsSize` | `results_size` | `int?` | `RunDetails` | yes | project path | Exists. Bytes. Zero until the run completes. |

### `GET /projects/{id}/summary` → `ProjectSummary`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Overlaps | Notes |
|---|---|---|---|---|---|---|---|
| `/projects/{id}/summary` | `created` | `created` | `str?` | `ProjectSummary` | **add** | — | Project-catalog creation. Frontend reads it (`projects/[id].vue:260`); today survives only as `extra`. |
| `/projects/{id}/summary` | `updated` | `updated` | `str?` | `ProjectSummary` | **add** | run `.updated` | **Different subject** — project record, not run. Keep both. |
| `/projects/{id}/summary` | `simulationRun.id` | `simulation_run.id` | `str?` | `SimulationRunSummary` | **add** | `/runs/{id}.id` | Identifier source for files/specs/logs/results. |
| `/projects/{id}/summary` | `simulationRun.name` | `simulation_run.name` | `str?` | `SimulationRunSummary` | **add** | `/runs/{id}.name` | Page title (`projects/[id].vue:76`). |
| `/projects/{id}/summary` | `simulationRun.metadata[0].abstract` | `abstract` | `str?` | `RunMetadata` | exists | run summary | — |
| `/projects/{id}/summary` | `…metadata[0].citations[].label` | `citations[].label` | `[LabeledIdentifier]` | `RunMetadata` | **add** | — | Project-summary only in the required surface. |
| `/projects/{id}/summary` | `…metadata[0].citations[].uri` | `citations[].uri` | `[LabeledIdentifier]` | `RunMetadata` | **add** | — | Rendered as a link (`projects/[id].vue:155`). |
| `/projects/{id}/summary` | `…metadata[0].creators[].label` | `creators[].label` | `[LabeledIdentifier]` | `RunMetadata` | exists | run summary | — |
| `/projects/{id}/summary` | `…metadata[0].description` | `description` | `str?` | `RunMetadata` | exists | run summary | — |
| `/projects/{id}/summary` | `…metadata[0].encodes[].label` | `encodes[].label` | `[LabeledIdentifier]` | `RunMetadata` | **add** | — | Taxonomy/biology (`projects/[id].vue:169`). |
| `/projects/{id}/summary` | `…metadata[0].encodes[].uri` | `encodes[].uri` | `[LabeledIdentifier]` | `RunMetadata` | **add** | — | — |
| `/projects/{id}/summary` | `…metadata[0].keywords[].label` | `keywords[].label` | `[LabeledIdentifier]` | `RunMetadata` | exists | run summary | — |
| `/projects/{id}/summary` | `…metadata[0].thumbnails[0]` | `thumbnails` | `[str]` | `RunMetadata` | exists | run summary | — |
| `/projects/{id}/summary` | `simulationRun.run.simulator.name` | `simulator.name` | `str?` | `SimulatorDetails` | **add** | `/runs/{id}.simulator` | ⚠️ **Different semantics** — display name vs. id slug. See §5. |
| `/projects/{id}/summary` | `simulationRun.run.simulator.version` | `simulator.version` | `str?` | `SimulatorDetails` | **add** | `/runs/{id}.simulatorVersion` | Same string, different provenance. |
| `/projects/{id}/summary` | `simulationRun.run.projectSize` | `project_size` | `int?` | `RunDetails` | exists | run summary | Identical semantics. |
| `/projects/{id}/summary` | `simulationRun.run.resultsSize` | `results_size` | `int?` | `RunDetails` | exists | run summary | Identical semantics. |

### `GET /files/{id}` → `list[ProjectFile]`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Overlaps | Notes |
|---|---|---|---|---|---|---|---|
| `/files/{id}` | `[].format` | `format` | `str?` | `ProjectFile` | yes | — | A media-type URI, e.g. `http://purl.org/NET/mediatypes/…vega.v5+json` (`useVisualizations.ts:25-34`). Not an enum — open vocabulary. |
| `/files/{id}` | `[].location` | `location` | `str?` | `ProjectFile` | yes | — | Archive-relative path, may lead with `./` (stripped at `useVisualizations.ts:38`). Split on `/` to build the tree (`FilesOutputsTable.vue:33`). |
| `/files/{id}` | `[].size` | `size` | `int?` | `ProjectFile` | yes | run `projectSize` | Per-file bytes; **not** the aggregate. |
| `/files/{id}` | `[].url` | `url` | `str?` | `ProjectFile` | yes | — | Absolute download URL, fetched directly (`useVisualizations.ts:52`). |

`{id}` here is the **run** id, not a file id. Model the remaining `ProjectFile` keys (`id`, `name`, `master`, `simulationRun`, `created`, `updated`) as optional too — they exist upstream (`simulation.ts:112`) and `extra="allow"` would pass them anyway; typing them is free.

### `GET /specifications/{id}` → `SedDocumentSpec`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Overlaps | Notes |
|---|---|---|---|---|---|---|---|
| `/specifications/{id}` | `id` | `id` | `str?` | `SedDocumentSpec` | yes | — | The SED-ML doc **location**, may lead with `./` (`useVisualizations.ts:76`). Composed into output ids. |
| | `tasks[].model.language.acronym` | `acronym` | `str?` | `SedModelLanguage` | yes | `ProjectStub.model_format` | First choice for the display label (`projects/[id].vue:183`). |
| | `tasks[].model.language.name` | `name` | `str?` | `SedModelLanguage` | yes | — | Second choice. |
| | `tasks[].model.language.sedmlUrn` | `sedml_urn` | `str?` | `SedModelLanguage` | yes | — | Third choice. `database.py:151` already derives a format from this URN shape. |
| | `outputs[]._type` | `type_` | `Literal[...]` | discriminator | **required** | log `dataSets` sniff | ⚠️ `_type` is a reserved-ish leading underscore; alias it. Values seen: `SedReport`, `SedPlot2D`, `SedPlot3D`. |
| | `outputs[].id` | `id` | `str?` | each `SedOutput` | yes | log `outputs[].id` | — |
| | `outputs[].name` | `name` | `str?` | each `SedOutput` | yes | — | Falls back to `id` for display. |
| | `outputs[].xScale` | `x_scale` | `SedAxisScale?` | `SedPlot2D/3D` | yes | — | `linear` \| `log` (`sedml.ts:390`). Plot-only. |
| | `outputs[].yScale` | `y_scale` | `SedAxisScale?` | `SedPlot2D/3D` | yes | — | Same. |
| | `outputs[].curves[].id` | `id` | `str?` | `SedCurve` | yes | — | — |
| | `outputs[].curves[].name` | `name` | `str?` | `SedCurve` | yes | — | Falls back to `id` (`sed-plot-2d-visualization.ts:109`). |
| | `outputs[].curves[].xDataGenerator` | `x_data_generator` | `str \| SedDataGenerator?` | `SedCurve` | yes | results `data[].id` | ⚠️ **Union**: serialized form is an id string, expanded form is an object. `sed-plot-2d-visualization.ts:87` handles both. Model as `str \| SedDataGeneratorRef \| None`. |
| | `outputs[].curves[].yDataGenerator` | `y_data_generator` | same | `SedCurve` | yes | same | Same. |
| | `outputs[].curves[].style.base` | `base` | `str \| SedStyle?` | `SedStyle` | yes | — | ⚠️ Recursive + union (`sedml.ts:82` vs `:89`). Needs a forward ref. |
| | `outputs[].curves[].style.line.color` | `color` | `str?` | `SedLineStyle` | yes | — | Hex string. |
| | `outputs[].curves[].style.line.thickness` | `thickness` | `float?` | `SedLineStyle` | yes | — | — |
| | `outputs[].curves[].style.line.type` | `type_` | `str?` | `SedLineStyle` | yes | marker `.type` | Vocabulary at `sedml.ts:32`. **Recommend `str`, not enum** — see §7. |
| | `outputs[].curves[].style.marker.fillColor` | `fill_color` | `str?` | `SedMarkerStyle` | yes | — | — |
| | `outputs[].curves[].style.marker.lineColor` | `line_color` | `str?` | `SedMarkerStyle` | yes | line `.color` | Distinct field, distinct object. |
| | `outputs[].curves[].style.marker.lineThickness` | `line_thickness` | `float?` | `SedMarkerStyle` | yes | line `.thickness` | Distinct. |
| | `outputs[].curves[].style.marker.size` | `size` | `float?` | `SedMarkerStyle` | yes | — | — |
| | `outputs[].curves[].style.marker.type` | `type_` | `str?` | `SedMarkerStyle` | yes | line `.type` | Different vocabulary (`sedml.ts:48`) from line type. |
| | `outputs[].dataSets[].id` | `id` | `str?` | `SedDataSet` | yes | results `data[].id` | Report-only. |
| | `outputs[].dataSets[].label` | `label` | `str?` | `SedDataSet` | yes | results `data[].label` | Report-only. |
| | `outputs[].dataSets[].name` | `name` | `str?` | `SedDataSet` | yes | — | Report-only. |

### `GET /logs/{id}` → `RunLog`

`LogEntry` base = `{status, algorithm, output, skip_reason, exception}`. The table below lists each level once and notes the repetition.

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Notes |
|---|---|---|---|---|---|---|
| `/logs/{id}` | `status` | `status` | `str?` | `LogEntry` (on `RunLog`) | yes | ⚠️ **Not** `/runs/{id}.status`. Rendered by `statusColor()` (`SimulationLogs.vue:186`) — same vocabulary, different subject. Recommend `str`, not the run enum. |
| | `output` | `output` | `str?` | `LogEntry` | yes | Raw stdout **with ANSI escapes** (`Anser.ansiToHtml`, `SimulationLogs.vue:136`). Can be very large. |
| | `algorithm` | `algorithm` | `str?` | `LogEntry` | yes | A **KISAO id string** (`KISAO_0000019`), not an object. The only bridge to `/ontologies/KISAO/{id}`. |
| | `skipReason.message` | `skip_reason.message` | `LogMessage?` | `LogEntry` | yes | Whole object absent when not skipped. |
| | `skipReason.type` | `skip_reason.type_` | `LogMessage?` | `LogEntry` | yes | `type` shadows nothing in Pydantic but reads badly; alias it. |
| | `exception.message` | `exception.message` | `LogMessage?` | `LogEntry` | yes | Same `LogMessage` type. |
| | `exception.type` | `exception.type_` | `LogMessage?` | `LogEntry` | yes | Same. |
| | `sedDocuments[].location` | `location` | `str?` | `SedDocumentLog` | yes | Keys the doc; joins to `SedDocumentSpec.id`. |
| | `sedDocuments[].{status,algorithm,skipReason,exception,output}` | — | inherited | `SedDocumentLog(LogEntry)` | yes | **Identical five fields — inherited, not repeated.** |
| | `sedDocuments[].tasks[].id` | `id` | `str?` | `SedTaskLog` | yes | Joins to `SedDocumentSpec.tasks[].id`. |
| | `sedDocuments[].tasks[].{status,algorithm,skipReason,exception,output}` | — | inherited | `SedTaskLog(LogEntry)` | yes | **Inherited.** |
| | `sedDocuments[].outputs[].id` | `id` | `str?` | `SedOutputLog` | yes | Joins to `SedDocumentSpec.outputs[].id`. |
| | `sedDocuments[].outputs[].{status,algorithm,skipReason,exception,output}` | — | inherited | `SedOutputLog(LogEntry)` | yes | **Inherited.** |
| | `sedDocuments[].outputs[].dataSets` | `data_sets` | `Any \| None` | `SedOutputLog` | yes | ⚠️ Shape unspecified in the required surface. `SimulationLogs.vue:162` only tests **presence** (`'dataSets' in outputLog`) to split reports from plots. Keep as `Any \| None` with a comment; do not guess. |

### `GET /results/{id}/{outputId}?includeData=true` → `OutputResults`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Notes |
|---|---|---|---|---|---|---|
| `/results/…` | `outputId` | `output_id` | `str?` | `OutputResults` | yes | Composite: `{sedDocLocation}/{output.id}`, URL-encoded (`useVisualizations.ts:97`). |
| | `data[].id` | `id` | `str?` | `ResultDatum` | yes | The **data-generator** id — joined as `{loc}/{plotId}/{genId}` (`sed-plot-2d-visualization.ts:90`). Not the dataSet id. |
| | `data[].label` | `label` | `str?` | `ResultDatum` | yes | Matches `SedDataSet.label`. |
| | `data[].values` | `values` | `list[Any]` | `ResultDatum` | `[]` | ⚠️ **Nested arbitrarily deep** for repeated tasks (`flattenTaskResults`). `list[float]` is **wrong**. Use `list[Any]`. Potentially very large. |

### `GET /ontologies/KISAO/{kisaoId}` → `KisaoTerm`

| Endpoint | API JSON Path | Proposed Python Field | Type | Destination | Optional? | Notes |
|---|---|---|---|---|---|---|
| `/ontologies/KISAO/{id}` | `id` | `id` | `str?` | `KisaoTerm` | yes | ⚠️ Separator ambiguity: local table uses `KISAO:0000019`, logs use `KISAO_0000019`. Normalize once. |
| | `name` | `name` | `str?` | `KisaoTerm` | yes | **Available locally** in `KISAO_TERMS` — fallback source. |
| | `url` | `url` | `str?` | `KisaoTerm` | yes | **Not in the local table.** Derivable as the OLS4 URL (`LogAlgorithm.vue:16`). |
| | `description` | `description` | `str?` | `KisaoTerm` | yes | **Not in the local table.** No fallback. |

---

## 5. Duplicate and Overlapping Field Resolution

### Run ID
- **Endpoints:** `/runs/{id}.id`, `/runs/{id}/summary.id`, `/projects/{id}/summary.simulationRun.id`.
- **Semantics:** identical. The path param of the first two *is* the third's value.
- **Authoritative:** `ProjectSummary.simulation_run.id` in project context; the route path param in run context.
- **Fallback:** none needed — if `simulationRun.id` is absent, no dependent call can be made and the aggregate degrades to summary-only.
- **Retain both?** No duplication arises; there is one instance per response.
- **Action:** `BiosimServiceRest.get_sim_run` already asserts `res["id"] == simulation_run_id` (line 98) — mirror that discipline by *not* re-deriving the id anywhere else.

### Run name
- **Endpoints:** `/runs/{id}.name`, run summary `.name`, project summary `.simulationRun.name`.
- **Semantics:** identical — same underlying run document.
- **Authoritative:** whichever endpoint the context already fetched. In project context, `simulationRun.name` (the frontend already titles the page from it, `projects/[id].vue:76`).
- **Retain both?** No. Never call `/runs/{id}` just to get a name you already have.

### Simulator name — **the one genuine semantic split**
- **Endpoints:** `/runs/{id}.simulator` → `"copasi"` (a slug/identifier, used to look up `BiosimulatorVersion` by `.id` at `biosim_service.py:146`). `/projects/{id}/summary.simulationRun.run.simulator.name` → a display name inside a `SimulatorDetails` object (`simulation.ts:51`), rendered as `` `${name} v${version}` `` (`projects/[id].vue:179`).
- **Semantics: NOT the same field.** One is a machine id, one is a human label. They may coincide in value for some simulators; do not assume they always do.
- **Authoritative:** for display → `run.simulator.name`. For simulator matching / version lookup → `/runs/{id}.simulator`.
- **Fallback:** if `run.simulator.name` is absent, fall back to the id — but **only in the frontend/presentation layer**, never by writing the id into the `name` field.
- **Retain both?** **Yes**, and under distinct names: `SimulatorDetails.name` vs `BiosimSimulationRun.simulator_id`.

### Simulator version
- **Endpoints:** `/runs/{id}.simulatorVersion` (string), `run.simulator.version` (string).
- **Semantics:** same value, same meaning.
- **Authoritative:** context-local, as with name.
- **⚠️ Naming hazard:** `BiosimSimulationRun.simulator_version` is already a `BiosimulatorVersion` **object** (`models.py:84`). A new string field must be named differently (`simulator_version_string`) or the model becomes actively misleading. Flag this before implementation.

### Project vs run `updated`
- **Endpoints:** `/projects/{id}/summary.updated` vs `/runs/{id}.updated` / run summary `.updated`.
- **Semantics: different subjects.** Project-catalog record mtime vs run-lifecycle mtime. A project's metadata can be edited long after its run finished.
- **Authoritative:** neither — they answer different questions. `projects/[id].vue:264` shows the *project's*.
- **Retain both?** **Yes, mandatory.** Collapsing them is a correctness bug. Keep them on different objects (`ProjectSummary.updated` vs `SimulationRunSummary.updated`), which the nesting already enforces.

### Metadata block
- **Endpoints:** run summary `.metadata[]` and project summary `.simulationRun.metadata[]`.
- **Semantics:** the same array — the project summary embeds the run summary.
- **Difference:** the *required surface* asks for `citations`/`encodes` only from the project path. That is a **consumer** difference, not a schema difference (both are `SimulationRunMetadataSummary`, `simulation.ts:76`).
- **Authoritative:** whichever was fetched.
- **Action:** **one shared `RunMetadata` model** carrying the union of fields. Do not build two metadata models.

### `projectSize` / `resultsSize`
- **Endpoints:** run summary `.run.*` and project summary `.simulationRun.run.*`.
- **Semantics:** identical — literally the same `run` object.
- **Authoritative:** whichever was fetched; in project context, the project summary (saves a call).
- **Retain both?** No — one shared `RunDetails` model, one instance per response.
- **Note:** `ProjectFile.size` is per-file and **does not** overlap; do not sum files to reconstruct `projectSize`.

### `status` — three distinct fields sharing a vocabulary
- **Endpoints:** `/runs/{id}.status` (run lifecycle), `/logs/{id}.status` (execution log, run level), `sedDocuments[]/tasks[]/outputs[].status` (per-element execution).
- **Semantics: three different subjects.** A run can be `SUCCEEDED` while an individual task is `SKIPPED`.
- **Authoritative:** each in its own scope. Never reconcile them.
- **Typing:** `/runs/{id}.status` stays `BiosimSimulationRunStatus` (existing). Log statuses stay `str` — see §8.

---

## 6. API Request / Aggregation Flow

### Recommended default (no change to today's call graph)

Each endpoint gets its own thin proxy route. The client makes exactly the calls it needs, when it needs them. This is the **primary recommendation**; the aggregate below is opt-in.

```
GET /projects/{project_id}/summary   ── 1 upstream call ── unchanged behavior
GET /runs/{run_id}                   ── 1
GET /runs/{run_id}/summary           ── 1
GET /files/{run_id}                  ── 1
GET /specifications/{run_id}         ── 1
GET /logs/{run_id}                   ── 1
GET /results/{run_id}/{output_id}    ── 1   (lazy, per plot)
GET /ontologies/KISAO/{kisao_id}     ── 1   (cached, per algorithm)
```

### Optional aggregate — `GET /projects/{project_id}/detail`

```
[1] EAGER, MANDATORY
    GET /projects/{project_id}/summary
      └── yields: run_id  = summary.simulationRun.id      ← the ONLY id discovered
                  created, updated, metadata[], run sizes,
                  simulator name/version
      └── failure ⇒ whole request fails (404/4xx/502, existing mapping)

[2] EAGER, PARALLEL, BEST-EFFORT   (asyncio.gather(..., return_exceptions=True))
    ├── GET /files/{run_id}            → list[ProjectFile]     failure ⇒ []
    └── GET /specifications/{run_id}   → SedDocumentSpec       failure ⇒ None
    (mirrors frontend Promise.all + .catch, projects/[id].vue:89-92)

[3] CONDITIONAL, BEST-EFFORT
    GET /logs/{run_id}                 → RunLog                failure ⇒ None
    Include only when the caller passes ?include=log — logs are large
    (raw ANSI stdout) and the project page does not render them.
    NEVER on the project page by default.

[4] NEVER AGGREGATED — always separate routes
    GET /results/{run_id}/{output_id}?includeData=true
      depends on: run_id (step 1) AND output ids (step 2's spec)
      output_id = urlencode(f"{spec.id.removeprefix('./')}/{output.id}")
      one call PER SedPlot2D output ⇒ unbounded fan-out, unbounded payload
    GET /ontologies/KISAO/{kisao_id}
      depends on: log.algorithm (step 3), one per distinct algorithm
```

### Per-call classification

| Call | Eager/Lazy | Cached | Conditional | Fails the whole response? | Expensive? |
|---|---|---|---|---|---|
| `/projects/{id}/summary` | Eager | No | No | **Yes** | No |
| `/files/{run}` | Eager (parallel) | No | No | No → `[]` | No |
| `/specifications/{run}` | Eager (parallel) | No | No | No → `None` | Moderate |
| `/logs/{run}` | Eager if requested | No | `?include=log` | No → `None` | **Yes** (raw stdout) |
| `/results/{run}/{out}` | **Lazy, separate route** | No | Per-plot | N/A | **Yes** (numeric arrays) |
| `/ontologies/KISAO/{id}` | **Lazy, separate route** | **Yes, TTL** | Per-algorithm | N/A | No (but repeated) |
| `/runs/{id}/summary` | **Not called in project context** | — | — | — | Redundant |

**Failure isolation rule:** only the step-1 call is load-bearing. A `/files` or `/specifications` failure must degrade the response, never invalidate it — the frontend already behaves this way (`.catch(() => [])`), and matching it keeps the migration a no-op for users.

**Caching:** only KISAO. The precedent is `@cached(ttl=3600, cache=SimpleMemoryCache)` on `get_simulator_versions` (`biosim_service.py:205`), or the platform-owned `_TtlCache` in `projects/database.py:236`. Do **not** cache project summaries, files, specs, logs, or results — freshness is the proxy's entire value over the materialized `PlatformProjectSearch` collection, per the study guide's recommendation.

---

## 7. Detailed Model Decisions

### Run information
Two models, deliberately not merged. `BiosimSimulationRun` (existing, `biosim_runs/models.py:80`) already covers `/runs/{id}` — **extend it** with `simulator_id: str | None` and `simulator_version_string: str | None` rather than adding a parallel `RunRecord`. `SimulationRunSummary` (new) covers `/runs/{id}/summary` and doubles as the `simulationRun` slot. Ownership: `biosim_runs/models.py` keeps the flat run; `common/biosim_api/runs.py` owns the summary.

### Metadata
One `RunMetadata` model (rename of `ProjectSummaryMetadata` — the "ProjectSummary" prefix becomes wrong once it also serves `/runs/{id}/summary`). Owns `abstract`, `description`, `creators`, `keywords`, `citations`, `encodes`, `thumbnails`. Always a **list** on the parent, defaulting to `[]`. Consumers index `[0]`; the model must never assume an element exists.

### Citations
`list[LabeledIdentifier]`, default `[]`. Both `uri` and `label` optional — `LabeledIdentifier` already allows this. New field on `RunMetadata`. Rendered as a link when `uri` is present, plain text otherwise (`projects/[id].vue:284-290`).

### Encodes
`list[LabeledIdentifier]`, default `[]`. Identical shape to citations; **reuse the type, do not subclass**. Semantically taxonomy/biology annotations.

### Creators
`list[LabeledIdentifier]`, default `[]`. Existing. Consumers use `.label` only; keep `.uri` because the test fixture proves upstream sends `{"uri": None, "label": "..."}` and `extra="allow"` would otherwise be doing the work implicitly.

### Keywords
`list[LabeledIdentifier]`, default `[]`. Existing. Note `projects/database.py:64` (`_label_values`) documents that in the *Mongo* collections these are stored as either bare strings **or** `{uri,label}` — that variance is a Mongo concern, not an API-response concern. Do **not** import that union into the API model unless a real API sample shows it.

### Thumbnail
`thumbnails: list[str]`, default `[]`. **Values are bare archive filenames, not URLs** — `Figure2.jpg` in the fixture. The URL is constructed as `{api}/files/{run}/{quote(name)}/download?thumbnail=view`, and `projects/database.py:175` (`_image_url`) already implements exactly this, including the absolute-URL passthrough case. **Reuse `_image_url`**; do not write a second one. Consider exposing a derived `thumbnail_url` on the *aggregate*, never on the passthrough mirror (which must stay faithful to upstream).

### File information
`ProjectFile` in `common/biosim_api/files.py`. All fields optional. `format` is an open media-type URI — **not an enum**; `useVisualizations.ts:33` even does a substring match (`.includes('vega')`) because the vocabulary is not closed. Endpoint returns a bare JSON **array**, so the client method returns `list[ProjectFile]` and must handle a non-list body defensively.

### Specification
`SedDocumentSpec` in `common/biosim_api/sedml.py`. Note `/specifications/{id}` returns a **single document** in current usage (`projects/[id].vue:95` casts to `SimulationRunSedDocument`, singular), while `useVisualizations` accepts `T | T[] | undefined` — so the array form exists somewhere. **Model the singular; accept both at the client boundary** by normalizing to a list internally. Flag as an open question (§15).

### Model languages
`SedModelLanguage {acronym, name, sedml_urn}` — three optional strings, consumed as an ordered fallback chain (`projects/[id].vue:183`). This is a legitimate 3-field nested model, not a one-field wrapper. `projects/database.py:151` (`_format_from_language_urn`) already parses the URN form; reuse it if a derived acronym is ever needed.

### Outputs
A **discriminated union** on `_type`:

```python
SedOutput = Annotated[
    Union[SedReport, SedPlot2D, SedPlot3D],
    Field(discriminator="type_"),   # each member: type_: Literal[...] = Field(alias="_type")
]
```

This is the right call because `SimulationLogs.vue:162` and `useVisualizations.ts:84,126` **already branch on the type** — currently by sniffing for a `dataSets` key, which a discriminator makes explicit and safe. Add a permissive fallback member (or `Union[..., SedUnknownOutput]`) so an unrecognized `_type` does not 500 the route.

### Curves
`SedCurve {id, name, x_data_generator, y_data_generator, style}`. Both data-generator fields are `str | SedDataGeneratorRef | None` — the *serialized* API form is an id string (`SerializedSedCurve`, `sedml.ts:404`), the expanded form is an object, and `sed-plot-2d-visualization.ts:87` explicitly handles both at runtime. A union here is not over-engineering; it is matching observed behavior.

### Curve styles
`SedStyle {base, line, marker}`. `base` is `str | SedStyle | None` — **recursive**, requiring `model_rebuild()`. Same serialized/expanded split as data generators (`sedml.ts:88`). The required surface only asks for `style.base`, `style.line.*`, `style.marker.*` — do **not** add `fill` unless a sample shows it needed.

`SedLineStyle {color, thickness, type_}` and `SedMarkerStyle {type_, size, fill_color, line_color, line_thickness}` are separate models with **overlapping-but-distinct** semantics (`line.color` ≠ `marker.lineColor`). Do not unify them.

**Enum vs string for `type`:** use `str`. The vocabularies (`sedml.ts:32`, `:48`) are known, but `sed-plot-2d-visualization.ts:42,53` maps them through a lookup dict that returns `undefined` for unknowns — meaning the frontend already tolerates values outside the enum. A `StrEnum` would turn an unknown line style into a 500 on a *passthrough* route. Document the known values in a comment.

### Datasets
`SedDataSet {id, label, name}` — report-only, `list`, default `[]`. `label` falls back to `name` falls back to `id` (`useVisualizations.ts:138`), so all three optional.

### Execution logs
`RunLog(LogEntry)` with `sed_documents: list[SedDocumentLog]`. `LogEntry` is a plain `BaseModel` base contributing the five shared fields; the four concrete log types inherit it. This is the single largest structural win in the feature: **20 of the 33 requested log fields collapse into 5 inherited definitions.**

### Skip reasons
`skip_reason: LogMessage | None = None`, alias `skipReason`. `None` means "not skipped" — that is exactly how `SimulationLogs.vue:16` reads it (`v-if="docLog.skipReason"`). Never default to an empty `LogMessage()`; that would render a spurious "Skipped:" block.

### Exceptions
`exception: LogMessage | None = None`. Same `LogMessage` type as skip reason (`{type_, message}`), same `None`-means-absent contract (`SimulationLogs.vue:20`). Two field names, one model — do not create `SkipReason` and `Exception` classes. (`Exception` as a class name would also shadow the builtin.)

### SED documents
`SedDocumentLog(LogEntry)` adds `location: str | None`, `tasks: list[SedTaskLog]`, `outputs: list[SedOutputLog]`. `location` joins to `SedDocumentSpec.id`. Both lists default to `[]` — `SimulationLogs.vue:147,160` guards on presence, so an absent array and an empty one must behave identically.

### SED tasks
`SedTaskLog(LogEntry)` adds only `id: str | None`. A one-field subclass is justified here **because it inherits five fields** — it is not a one-field dataclass.

### SED outputs
`SedOutputLog(LogEntry)` adds `id: str | None` and `data_sets: Any | None`. The presence of `data_sets` is what distinguishes a report log from a plot log (`SimulationLogs.vue:162,177`). ⚠️ Because `Any | None` cannot distinguish "absent" from "present and null", use `data_sets: Any = None` plus `model_fields_set` if the report/plot split must be exact — or better, join to the spec's discriminated `_type` instead of sniffing.

### Results
`OutputResults {output_id, data: list[ResultDatum]}` and `ResultDatum {id, label, values: list[Any]}`. **Never embedded** in any summary or detail object. `values` must be `list[Any]`, not `list[float]`: `flattenTaskResults` in the frontend flattens nested arrays produced by repeated tasks, so the payload is ragged/nested. Note this is a *different* API from the existing `Hdf5DataValues` (simdata API, `shape` + flat `values`) — do not conflate them.

### KISAO ontology information
`KisaoTerm {id, name, url, description}`, all optional. **Resolution strategy, in order:** (1) TTL cache; (2) upstream `GET /ontologies/KISAO/{id}`; (3) local `KISAO_TERMS` for `name` + derived OLS4 URL, `description=None`. Resolved **separately and lazily**, never embedded in `RunLog` — the same algorithm id repeats across every document, task, and output, so embedding would multiply identical payloads. Normalize `KISAO_0000019` ↔ `KISAO:0000019` in exactly one helper.

---

## 8. Optionality and Error-State Strategy

| Situation | Recommendation |
|---|---|
| **Missing `metadata` key** | `metadata: list[RunMetadata] = Field(default_factory=list)` — already correct. |
| **Empty `metadata` array** | Same as missing. Consumers index `[0]`; the API layer must not synthesize a placeholder element. If the aggregate ever exposes a flattened convenience view, use `metadata[0] if metadata else None`. |
| **Missing `metadata[0].abstract` / null** | `str \| None = None`. The existing fixture proves upstream sends explicit `null` (`test_project_summary.py:46`). |
| **Missing `run` object** | ⚠️ **Currently a bug:** `ProjectSummarySimulationRun.run` is required (`json_types.py:82`), so an upstream project without `run` raises `ValidationError` → an unhandled 500 (the router catches only `ClientResponseError` and `aiohttp.ClientError`). **Make it `RunDetails \| None = None`** in Phase 1. |
| **Missing thumbnail** | `thumbnails: list[str] = []`. Consumers guard with `?.[0]` and render "No thumbnail available" (`projects/[id].vue:224`). Never emit a placeholder URL. |
| **Missing `simulator` in `run`** | `simulator: SimulatorDetails \| None = None`. The frontend already guards (`v-if="run_summary.run?.simulator"`, line 175). |
| **Missing style** | `style: SedStyle \| None = None` — the curve renders with Plotly defaults. |
| **Missing `line` or `marker`** | `SedLineStyle \| None` / `SedMarkerStyle \| None`. `sed-plot-2d-visualization.ts:120` computes `hasLine`/`hasMarker` from absence, so `None` is load-bearing — an empty object would change rendering. |
| **Empty `curves` / `dataSets`** | `[]`. A plot with no curves is valid-but-empty, not an error. |
| **Unknown output `_type`** | Fall through to a permissive member rather than raising. A passthrough must not 500 on a schema the upstream added. |
| **Missing logs entirely (404)** | The aggregate sets `log=None`; the standalone `/logs/{run}` route forwards 404. Logs legitimately do not exist before a run starts. |
| **Running simulation** | `status` = `RUNNING`/`QUEUED`; `results_size` may be `0` or absent; `/results` 404s; `/logs` may exist with partial `sedDocuments`. All handled by optionality — **no special-casing needed**, which is the point of the design. |
| **Failed simulation** | `exception` populated at whichever level failed; `output` holds the traceback. Nothing is special-cased. |
| **Skipped tasks** | `skip_reason` populated on that `SedTaskLog` only; siblings unaffected. |
| **Logs before results** | Expected and normal. Logs and results are independent endpoints with independent lifetimes — another reason not to aggregate them. |
| **Missing result data** | The standalone route forwards the upstream status. In `useVisualizations` terms, this surfaces as one broken plot, not a broken page. |
| **Failed ontology lookup** | Fall back to local `KISAO_TERMS` (`name` only) and the derived OLS4 URL; `description=None`. If the id is unknown locally too, return 404 from the standalone route — `LogAlgorithm.vue:38` already catches and renders a bare id link. |
| **Enum vs string, generally** | Enum only where the vocabulary is closed *and* the repo already enforces it: `BiosimSimulationRunStatus` for `/runs/{id}.status`. Everywhere else on a passthrough — log statuses, SED style types, file formats — use `str`. An unknown enum value on a proxy is a 500 the user cannot fix. |
| **Empty list vs `None`** | Lists → `Field(default_factory=list)`, never `None`. Nested objects → `None`, never an empty instance. This is the existing convention and it is correct. |
| **Tuples** | No. The repo uses `list` throughout, and Pydantic serializes tuples to arrays anyway with no benefit. |

---

## 9. Naming and Type Conventions

All aliases use `Field(alias="<camelCase>")` with `model_config = ConfigDict(populate_by_name=True, extra="allow")`, matching the existing `json_types.py` style. FastAPI serializes response models with `by_alias=True`, so `alias` covers both directions — **the wire shape is unchanged as long as each alias exactly matches the upstream key.**

| API name | Python field | Type | Object | Note |
|---|---|---|---|---|
| `simulator` (on `/runs/{id}`) | `simulator_id` | `str \| None` | `BiosimSimulationRun` | ⚠️ Renamed to avoid implying the nested `SimulatorDetails`. Document the mapping. |
| `simulatorVersion` | `simulator_version_string` | `str \| None` | `BiosimSimulationRun` | ⚠️ `simulator_version` is **taken** by a `BiosimulatorVersion` object on that model. Do not silently shadow it. |
| `projectSize` | `project_size` | `int \| None` | `RunDetails` | Bytes. Existing. |
| `resultsSize` | `results_size` | `int \| None` | `RunDetails` | Bytes. Existing. |
| `simulationRun` | `simulation_run` | `SimulationRunSummary` | `ProjectSummary` | Existing alias. |
| `sedmlUrn` | `sedml_urn` | `str \| None` | `SedModelLanguage` | Not `sed_ml_urn` — "sedml" is one token throughout the repo. |
| `xScale` / `yScale` | `x_scale` / `y_scale` | `str \| None` | `SedPlot2D/3D` | Values `linear` \| `log`. |
| `xDataGenerator` / `yDataGenerator` | `x_data_generator` / `y_data_generator` | `str \| SedDataGeneratorRef \| None` | `SedCurve` | Union is deliberate — see §7. |
| `fillColor` | `fill_color` | `str \| None` | `SedMarkerStyle` | Hex string. |
| `lineColor` | `line_color` | `str \| None` | `SedMarkerStyle` | ⚠️ Distinct from `SedLineStyle.color`. |
| `lineThickness` | `line_thickness` | `float \| None` | `SedMarkerStyle` | ⚠️ Distinct from `SedLineStyle.thickness`. |
| `thickness` | `thickness` | `float \| None` | `SedLineStyle` | — |
| `skipReason` | `skip_reason` | `LogMessage \| None` | `LogEntry` | `None` ⇒ not skipped. |
| `exception` | `exception` | `LogMessage \| None` | `LogEntry` | Field name only — no class named `Exception`. |
| `type` (inside skipReason/exception) | `type_` | `str \| None` | `LogMessage` | Trailing underscore; alias `"type"`. |
| `sedDocuments` | `sed_documents` | `list[SedDocumentLog]` | `RunLog` | — |
| `dataSets` (spec) | `data_sets` | `list[SedDataSet]` | `SedReport` | — |
| `dataSets` (log) | `data_sets` | `Any \| None` | `SedOutputLog` | ⚠️ Same alias, **different type**, different object. Shape unspecified — see §15. |
| `outputId` | `output_id` | `str \| None` | `OutputResults` | Composite `{docLocation}/{outputId}`, URL-encoded. |
| `_type` | `type_` | `Literal[...]` | SED output union | ⚠️ Leading underscore is a Pydantic private-attr prefix; **must** be aliased, not used directly. |
| `includeData` | (query param) | `bool` | route | Forward as a literal `true`; do not invent other query params. |
| `values` | `values` | `list[Any]` | `ResultDatum` | Not `list[float]` — nested for repeated tasks. |
| `url` (KISAO) | `url` | `str \| None` | `KisaoTerm` | Plain `str`; no `HttpUrl` — it would reject a malformed upstream value on a passthrough. |

**Timestamps:** keep as `str`, matching `BiosimSimulationRun.submitted/updated` and `ProjectStub.created/updated`. Parsing to `datetime` would re-serialize in a different format and silently change the wire contract for the frontend's `<NuxtTime>`.

---

## 10. Backwards Compatibility Analysis

| Risk | Assessment | Mitigation |
|---|---|---|
| **New required field breaks parsing** | Real. The test fixture (`test_project_summary.py:26`) has no `created`/`updated`; live in-flight runs omit run fields. | **Every added field optional with a default.** Non-negotiable. |
| **Positional construction breaks** | Not a risk. Pydantic `BaseModel.__init__` is keyword-only; there are no positional construction sites. | — |
| **`ProjectSummarySimulationRun.run` required** | **Pre-existing bug**, not introduced by this work. Any project lacking `run` → `ValidationError` → unhandled 500. | Make it optional in Phase 1; add a regression test. |
| **Serialization changes when an extra becomes a field** | Real and subtle. `created`/`updated`/`simulationRun.id`/`.name` currently ride through as `extra`. Once typed, they serialize via their alias. | Aliases must match the upstream key **byte for byte**. Add a round-trip test asserting `response.json()` retains every input key. |
| **Field/extra ordering in JSON** | Pydantic emits declared fields before extras. Key order changes. | Harmless for JSON consumers; note it so nobody writes an order-sensitive assertion. |
| **Equality** | `BaseModel.__eq__` compares fields **and** extras. `test_rest_client_requests_the_upstream_summary_url` asserts `summary == _SUMMARY`; both sides come from the same JSON, so it stays true. | No action. |
| **Hashing** | Not a risk — these models are unhashable (no `frozen=True`) and nothing hashes them. | — |
| **`BiosimService` ABC widening** | Adding abstract methods **breaks every implementer**, including `BiosimServiceMock` (`tests/fixtures/biosim_service_mock.py`). | Add mock implementations in the **same commit** as each new abstract method. Never split them across phases. |
| **Renaming `ProjectSummaryMetadata` → `RunMetadata`** | Zero external cost: `json_types.py` is **untracked**, and nothing outside it imports those names (verified). | Do it in Phase 0, before the file has any history. |
| **`json_types.py` → package split** | Same — zero cost now, meaningful cost after it ships. | Phase 0. |
| **OpenAPI schema churn** | The `/projects/{id}/summary` response schema gains properties. Additive; no client generation in this repo. | Note in the PR body. |
| **Frontend `ProjectSummary` interface** | `frontend/app/models/projects.ts:39` types `id: number` and `simulationRun: any`. Already wrong (ids are slugs — flagged in `docs/project-search-api-frontend-integration.md`). | Out of scope for the backend PR; flag for the frontend migration. |
| **`ProjectStub` / search path** | Untouched. `projects/database.py` and `search.py` read Mongo, not this model. | No action. |
| **`JobLogs.logs: dict[str, Any]`** | `simulations/router.py:367` passes the raw dict through. If `get_sim_run_logs` is retyped to `RunLog`, that breaks. | **Keep `get_sim_run_logs` returning `dict`**; add a *separate* `get_run_log() -> RunLog`. Do not retype the existing method. |

---

## 11. Files and Symbols Likely to Change

### Definitely affected

| Path | Symbol | Change | Reason |
|---|---|---|---|
| `backend/biosim_server/common/json_types.py` | whole module | **Split** into `common/biosim_api/{common,projects,runs,files,sedml,logs,results,ontology}.py` | Will 10× in size; untracked, so free to reorganize now. |
| `common/biosim_api/common.py` | `LabeledIdentifier` | Moved verbatim | Shared by metadata across two endpoints. |
| `common/biosim_api/common.py` | `LogMessage` | **New** | `{type, message}` shared by `skipReason` + `exception` at 4 levels. |
| `common/biosim_api/projects.py` | `ProjectSummary` | Add `created`, `updated`; retype `simulation_run` | Fields the frontend reads that are currently untyped extras. |
| `common/biosim_api/runs.py` | `SimulationRunSummary` | **New** (from `ProjectSummarySimulationRun`) + `id`, `name`, `submitted`, `updated`; `run` → optional | Serves two endpoints; `.id` is the key for all dependent calls. |
| `common/biosim_api/runs.py` | `RunMetadata` | Rename of `ProjectSummaryMetadata` + `citations`, `encodes` | Prefix is wrong once shared; two required fields missing. |
| `common/biosim_api/runs.py` | `RunDetails`, `SimulatorDetails` | Rename of `ProjectSummaryRun` + nested simulator | `run.simulator.{name,version}` are required and unmodeled. |
| `common/biosim_api/{files,sedml,logs,results,ontology}.py` | all | **New** | Five unmodeled endpoints. |
| `biosim_runs/biosim_service.py` | `BiosimService` ABC | Add `get_run_summary`, `get_run_files`, `get_run_specifications`, `get_run_log`, `get_output_results`, `get_kisao_term` | No client methods exist for these. |
| `biosim_runs/biosim_service.py` | `BiosimServiceRest` | Implement the above; quote every path segment as `get_project_summary` does | `outputId` contains `/` — quoting is mandatory, not optional. |
| `biosim_runs/models.py` | `BiosimSimulationRun` | Add `simulator_id`, `simulator_version_string` | `/runs/{id}.simulator`/`.simulatorVersion` are parsed then discarded today. |
| `biosim_runs/biosim_service.py` | `_sim_run_from_response` | Populate the two new fields | Same. |
| `tests/fixtures/biosim_service_mock.py` | `BiosimServiceMock` | One stub + one dict per new ABC method | ABC widening breaks the mock otherwise — same commit, every time. |
| `tests/projects/test_project_summary.py` | `_SUMMARY_JSON`, `_assert_parsed_summary` | Extend fixture with `created`/`updated`/`citations`/`encodes`/`simulator`; assert new paths | Existing assertions cover only the seven current paths. |

### Probably affected

| Path | Symbol | Change | Reason |
|---|---|---|---|
| `biosim_server/projects/router.py` | new routes | `/runs/{id}` … `/ontologies/KISAO/{id}` proxies | Where the new endpoints land — **but see §15 Q6**: `/runs/*` and `/files/*` are run-scoped and may belong in a new router, not under `/projects`. |
| `biosim_server/api/main.py` | `include_router` | Register a new router if one is added | Only if the routes are not folded into `projects_router`. |
| `biosim_server/projects/database.py` | `_image_url`, `_format_from_language_urn` | Reuse (import), do not duplicate | These already solve thumbnail-URL and language-format derivation. |
| `biosim_server/common/kisao_data.py` | `KISAO_TERMS` | Read-only fallback | Lacks `url`/`description`; may warrant a generator change (see §15 Q5). |
| `backend/CLAUDE.md`, root `CLAUDE.md` | endpoint table | Add new routes | Repo convention — the tables are maintained. |
| `docs/Biosimulations Platform Backend Study Guide.md` | proxy table | Mark rows as implemented | It is the plan of record for this work. |

### Only if a specific design option is chosen

| Path | Symbol | Condition |
|---|---|---|
| `biosim_server/projects/models.py` | `ProjectDetail` | Only if the Phase-8 aggregate is built. |
| `biosim_server/projects/router.py` | `get_project_detail` | Same. |
| `biosim_server/config.py` | `kisao_cache_ttl_seconds` | Only if KISAO caching is made configurable rather than a hardcoded `@cached(ttl=3600)`. |
| `frontend/app/models/*.ts`, `pages/{projects,runs}/[id].vue` | `legacy_api_url` → `api_url` | Only when the frontend is cut over — **a separate PR**, following the `project-search-api-frontend-integration.md` pattern. |
| `biosim_server/simulations/models.py` | `JobLogs.logs` | Only if you decide to retype it to `RunLog`. **Recommendation: don't**, this pass. |

---

## 12. Step-by-Step Implementation Plan

### Phase 0 — Reorganize and clean the new module

**Goal.** Give the models a home that survives 8 endpoints, before any of it has git history.

**Changes.** Create `biosim_server/common/biosim_api/` with `__init__.py` re-exporting the public names. Move `LabeledIdentifier` → `common.py`; `ProjectSummary` → `projects.py`; the three `ProjectSummary*` children → `runs.py` renamed `SimulationRunSummary` / `RunMetadata` / `RunDetails`. Delete unused `ModelFormat`, `Simulator`, `RunStatus` (all three verified unused; `Simulator.VirtualCell` also has a leading-space typo). Delete `json_types.py`. Update the two importers.

**Files/Symbols.** `common/json_types.py` (deleted), `common/biosim_api/*` (new), `biosim_runs/biosim_service.py:17`, `projects/router.py:20`, `tests/fixtures/biosim_service_mock.py:9`, `tests/projects/test_project_summary.py:22`.

**Tests.** No new tests. The existing 11 must pass unchanged — that is the phase's proof.

**Dependencies.** None.

**Completion criteria.** `ruff check .`, `mypy biosim_server tests`, `pytest -m "not integration"` all green; `grep -rn json_types` returns nothing.

---

### Phase 1 — Widen `ProjectSummary` to the full project-summary surface

**Goal.** Type every field the required surface asks of `/projects/{id}/summary`, and fix the required-`run` bug.

**Changes.** `ProjectSummary`: add `created: str | None`, `updated: str | None`. `SimulationRunSummary`: add `id`, `name`, `submitted`, `updated` (all `str | None`); change `run: RunDetails | None = None`. `RunMetadata`: add `citations`, `encodes` (`list[LabeledIdentifier]`). `RunDetails`: add `simulator: SimulatorDetails | None`. New `SimulatorDetails {id, name, version, digest, url}`, all optional.

**Files/Symbols.** `common/biosim_api/projects.py`, `common/biosim_api/runs.py`.

**Tests.** Extend `_SUMMARY_JSON` with `created`, `updated`, `simulationRun.run.simulator`, `metadata[0].citations`, `metadata[0].encodes`. Extend `_assert_parsed_summary` to all 17 project-summary paths. **New:** a summary with no `run` key parses (regression for the required-`run` bug). **New:** a round-trip test asserting every modeled key serializes back under its original camelCase name.

**Dependencies.** Phase 0.

**Completion criteria.** All project-summary rows in §4 are typed; the no-`run` case returns 200, not 500.

---

### Phase 2 — `/runs/{id}` and `/runs/{id}/summary`

**Goal.** Cover the two run-level endpoints without duplicating `SimulationRunSummary`.

**Changes.** Add `simulator_id`, `simulator_version_string` to `BiosimSimulationRun`; populate both in `_sim_run_from_response`. Add `BiosimService.get_run_summary(run_id) -> SimulationRunSummary` + REST impl + mock impl. Add proxy routes reusing the existing error-mapping block (extract it into a shared `_map_upstream_error` helper rather than copy-pasting it seven times).

**Files/Symbols.** `biosim_runs/models.py:80`, `biosim_runs/biosim_service.py:23,48,84`, `tests/fixtures/biosim_service_mock.py`, `projects/router.py` (or a new run router — §15 Q6).

**Tests.** `_sim_run_from_response` populates the two new fields and leaves them `None` when absent. `get_run_summary` hits the right URL and quotes the id. The route maps 404/4xx/502/503 like `/projects/{id}/summary`. **`SimulationRunSummary` parses the project-summary `simulationRun` object unchanged** — the test that proves the dedup is real.

**Dependencies.** Phase 1.

**Completion criteria.** One model validates both payloads; the ABC and mock are in lockstep.

---

### Phase 3 — `/files/{id}`

**Goal.** Type the file listing.

**Changes.** New `ProjectFile` (`files.py`). `get_run_files(run_id) -> list[ProjectFile]` — the body is a bare array; validate with `TypeAdapter(list[ProjectFile])` and tolerate a non-list body. New route.

**Files/Symbols.** `common/biosim_api/files.py`, `biosim_runs/biosim_service.py`, `tests/fixtures/biosim_service_mock.py`, router.

**Tests.** Array parse; empty array → `[]`; a file with a `null` `format`; the tree-relevant `location` with a leading `./`; upstream 404 → 404.

**Dependencies.** Phase 2 (shared error helper).

**Completion criteria.** `useVisualizations`' Vega-file filter can be reproduced against the model.

---

### Phase 4 — `/specifications/{id}`

**Goal.** The largest and riskiest model. Do it alone.

**Changes.** `sedml.py`: `SedDocumentSpec`, `SedTaskSpec`, `SedModelRef`, `SedModelLanguage`, `SedDataSet`, `SedCurve`, `SedStyle` (recursive), `SedLineStyle`, `SedMarkerStyle`, `SedReport`, `SedPlot2D`, `SedPlot3D`, and the `_type`-discriminated `SedOutput` union with a permissive fallback. `model_rebuild()` for the recursive `SedStyle.base`. Client method + route; normalize a single-document body to a list internally.

**Files/Symbols.** `common/biosim_api/sedml.py`, `biosim_runs/biosim_service.py`, mock, router.

**Tests.** Discriminated dispatch: `SedReport` → has `data_sets`, `SedPlot2D` → has `curves`. Unknown `_type` does not raise. `xDataGenerator` as a string **and** as an object. `style` absent; `style.line` absent but `style.marker` present. `style.base` as a string and as a nested object. Empty `outputs`/`curves`/`dataSets`. Model-language fallback chain (`acronym` → `name` → `sedml_urn`).

**Dependencies.** Phase 3.

**Completion criteria.** A real captured `/specifications` response round-trips; all 22 spec rows in §4 are typed.

---

### Phase 5 — `/logs/{id}`

**Goal.** Prove the `LogEntry` inheritance actually collapses the repetition.

**Changes.** `logs.py`: `LogEntry` base, `RunLog`, `SedDocumentLog`, `SedTaskLog`, `SedOutputLog`. Add `get_run_log(run_id) -> RunLog` — **leave `get_sim_run_logs` returning `dict` untouched** so `simulations/router.py:364` and `JobLogs` keep working. New route.

**Files/Symbols.** `common/biosim_api/logs.py`, `biosim_runs/biosim_service.py`, mock, router.

**Tests.** Every level parses `status`/`algorithm`/`output`. `skipReason`/`exception` present at one level and absent at siblings. A log with `sedDocuments: []`. A running simulation: top-level status only, no documents. A failed run: `exception` populated at the task level, siblings clean. `dataSets` present on one output and absent on another (the report/plot split). **A test asserting `simulations/router.py`'s existing log passthrough is unchanged.**

**Dependencies.** Phase 4 (id joins are easier to assert once the spec exists).

**Completion criteria.** All 33 log rows typed via 5 inherited + 4 own fields; `JobLogs` untouched.

---

### Phase 6 — `/results/{id}/{outputId}`

**Goal.** Lazy results, correctly typed and correctly quoted.

**Changes.** `results.py`: `OutputResults`, `ResultDatum` (`values: list[Any]`). `get_output_results(run_id, output_id)` forwarding `includeData=true`. Route with `output_id: str` — **`quote(output_id, safe='')`**, since it legitimately contains `/`.

**Files/Symbols.** `common/biosim_api/results.py`, `biosim_runs/biosim_service.py`, mock, router.

**Tests.** An `output_id` containing `/` survives quoting (mirror `test_rest_client_quotes_the_project_id`). Nested `values` arrays parse. Empty `data`. Upstream 404 (results not ready) → 404.

**Dependencies.** Phase 4 (output ids come from the spec).

**Completion criteria.** `useVisualizations`' per-plot URL can be reproduced against the platform route.

---

### Phase 7 — `/ontologies/KISAO/{kisaoId}`

**Goal.** Cached term lookup with a local fallback.

**Changes.** `ontology.py`: `KisaoTerm`. `get_kisao_term(kisao_id) -> KisaoTerm` with `@cached(ttl=3600, cache=SimpleMemoryCache)` (the `get_simulator_versions` precedent). One `_normalize_kisao_id` helper handling `KISAO_0000019` ↔ `KISAO:0000019`. Fallback to `KISAO_TERMS[name]` + a derived OLS4 URL on upstream failure. Route.

**Files/Symbols.** `common/biosim_api/ontology.py`, `common/kisao_data.py` (read-only), `biosim_runs/biosim_service.py`, mock, router.

**Tests.** Both id separators resolve to the same term. Upstream success returns all four fields. Upstream failure falls back with `name` set and `description=None`. An id unknown both upstream and locally → 404. The cache is hit on the second call (assert one upstream call for two invocations).

**Dependencies.** Phase 5 (`algorithm` ids come from logs).

**Completion criteria.** Separator ambiguity handled in exactly one place.

---

### Phase 8 — Optional aggregate `GET /projects/{id}/detail`

**Goal.** One round trip for the project page, without touching `/summary`.

**Changes.** `ProjectDetail` in `projects/models.py`. Route: mandatory summary → `asyncio.gather(files, specifications, return_exceptions=True)` → optional log behind `?include=log`. Never results, never KISAO.

**Files/Symbols.** `projects/models.py`, `projects/router.py`.

**Tests.** Happy path composes all three. `/files` raises → `files == []`, status 200. `/specifications` raises → `specification is None`, status 200. `/projects/{id}/summary` raises 404 → 404. `?include=log` absent ⇒ `get_run_log` **not awaited**. A summary with no `simulationRun.id` ⇒ no dependent calls attempted.

**Dependencies.** Phases 1–5.

**Completion criteria.** `/summary` byte-identical to before; `/detail` degrades gracefully on every secondary failure.

---

## 13. Test Plan

| Test | Scenario | Input Shape | Expected Behavior | Test Level |
|---|---|---|---|---|
| `test_summary_parses_created_updated` | Project timestamps typed, not extras | `{id, created, updated, simulationRun:{…}}` | `summary.created == "..."`; serializes back as `created` | Unit (model) |
| `test_summary_without_run_object` | **Regression for the required-`run` bug** | `simulationRun` with `metadata` but no `run` | Parses; `run is None`; route 200 | Unit + route |
| `test_summary_run_id_and_name_typed` | Id provenance | `simulationRun.{id,name}` present | `.simulation_run.id` reachable as a field | Unit |
| `test_metadata_citations_and_encodes` | New metadata arrays | `citations[{label,uri}]`, `encodes[{label,uri}]` | Both parse; `uri` may be `null` | Unit |
| `test_metadata_missing_entirely` | Absent metadata | `simulationRun` with no `metadata` | `metadata == []`, no exception | Unit |
| `test_metadata_empty_array` | Empty metadata | `metadata: []` | `metadata == []`; no synthesized element | Unit |
| `test_simulator_details_nested` | `run.simulator` object | `run.simulator.{name,version}` | Typed; absent ⇒ `None` | Unit |
| `test_summary_roundtrip_preserves_wire_keys` | **Serialization compat** | Full `_SUMMARY_JSON` | `response.json()` retains every input key with identical camelCase names/values | Route |
| `test_run_summary_parses_project_simulation_run` | **Proves the dedup** | The `simulationRun` sub-object from `_SUMMARY_JSON` | `SimulationRunSummary` validates it unchanged | Unit |
| `test_run_response_captures_simulator_id` | `/runs/{id}` new fields | `{simulator:"copasi", simulatorVersion:"4.34.251", …}` | `simulator_id=="copasi"`, `simulator_version_string=="4.34.251"`; `simulator_version` still the object | Unit |
| `test_files_parses_array` | `/files` list body | `[{format,location,size,url}]` | `list[ProjectFile]` of len 1 | Unit |
| `test_files_empty_and_null_fields` | Sparse files | `[]` and `[{format:null,size:null}]` | `[]`; fields `None`, no raise | Unit |
| `test_spec_discriminates_report_vs_plot` | `_type` union | `outputs:[{_type:"SedReport",dataSets:[…]},{_type:"SedPlot2D",curves:[…]}]` | Correct classes; `data_sets`/`curves` populated | Unit |
| `test_spec_unknown_output_type` | Schema drift | `outputs:[{_type:"SedPlot4D"}]` | No raise; permissive fallback | Unit |
| `test_curve_data_generator_string_and_object` | Serialized vs expanded | `xDataGenerator:"gen1"` / `{id:"gen1"}` | Both parse | Unit |
| `test_curve_style_absent` | Missing style | `curves:[{id,name}]` | `style is None` | Unit |
| `test_curve_style_line_without_marker` | Partial style | `style:{line:{color:"#f00"}}` | `line` set, `marker is None` | Unit |
| `test_style_base_recursive` | `style.base` union | `base:"s1"` / `base:{line:{…}}` | Both parse; recursion resolves | Unit |
| `test_model_language_fallback_chain` | `acronym`/`name`/`sedmlUrn` | Each present alone | All three optional, independently readable | Unit |
| `test_log_shares_entry_fields_at_all_levels` | Inheritance | status/algorithm/output at run, doc, task, output | Identical field access at every level | Unit |
| `test_log_skip_reason_absent_is_none` | Not skipped | Entry without `skipReason` | `skip_reason is None` (not an empty object) | Unit |
| `test_log_exception_populated_on_task_only` | **Partial failure** | Task with `exception`, siblings without | Only that task's `exception` set | Unit |
| `test_log_running_simulation` | **In-flight run** | `{status:"RUNNING", sedDocuments:[]}` | Parses; `sed_documents == []` | Unit |
| `test_log_output_datasets_presence` | Report/plot split | One output with `dataSets`, one without | `data_sets` distinguishes them | Unit |
| `test_simulations_logs_passthrough_unchanged` | **Back-compat** | Existing `JobLogs` flow | `get_sim_run_logs` still returns `dict`; `simulations/router.py` untouched | Route |
| `test_results_values_nested_arrays` | Repeated tasks | `data[{values:[[1,2],[3,4]]}]` | Parses as `list[Any]` (would fail `list[float]`) | Unit |
| `test_results_output_id_is_quoted` | **Path safety** | `output_id = "sim.sedml/plot_1"` | Upstream URL contains `sim.sedml%2Fplot_1` | Unit (client) |
| `test_results_not_ready_is_404` | Run incomplete | Upstream 404 | Route returns 404 | Route |
| `test_kisao_id_separator_normalized` | `_` vs `:` | `KISAO_0000019` and `KISAO:0000019` | Same term, same upstream URL | Unit |
| `test_kisao_falls_back_to_local_terms` | Upstream down | `ClientError` on lookup | `name` from `KISAO_TERMS`, `url` derived, `description is None` | Unit |
| `test_kisao_is_cached` | Cache | Two calls, same id | One upstream call | Unit |
| `test_detail_composes_all_parts` | Aggregate happy path | Mocked summary + files + spec | All three populated | Route |
| `test_detail_tolerates_files_failure` | **Partial degradation** | `get_run_files` raises | 200; `files == []`; `summary` intact | Route |
| `test_detail_tolerates_spec_failure` | Same | `get_run_specifications` raises | 200; `specification is None` | Route |
| `test_detail_fails_on_summary_failure` | Mandatory call | Summary raises 404 | 404 | Route |
| `test_detail_skips_log_by_default` | **No eager logs** | No `?include=log` | `get_run_log` **not awaited** | Route |
| `test_detail_without_run_id_skips_dependents` | Missing id | Summary with no `simulationRun.id` | No dependent calls; 200 | Route |
| `test_all_new_routes_are_anonymous` | Auth parity | GET with and without `Authorization` | 200 both; no credential forwarded upstream | Route |
| `test_new_routes_do_not_shadow_existing` | Routing | `/projects/stats`, `/projects/reindex` | Still resolve correctly | Route |

Integration tests (`-m integration`, excluded from CI per `backend/CLAUDE.md`) are appropriate for capturing one real response per endpoint — and are in fact **the way to resolve most of §15**.

---

## 14. Risks and Tradeoffs

| Risk | Rank | Detail | Mitigation |
|---|---|---|---|
| **API-call explosion** | **High** | `/results` is one call **per plot output**; a doc with 20 plots = 20 upstream calls. Aggregating it would make one page load a 20× fan-out against a third party. | Never aggregate results. Keep it a per-output route the client calls lazily, exactly as `useVisualizations.ts:98` does today. |
| **Unstable upstream schemas** | **High** | The study guide marks nearly every endpoint **"Needs verification."** Modeling from TypeScript interfaces and Vue templates is inference, not a contract. A wrong required field turns a proxy into a 500. | `extra="allow"` everywhere; **every** field optional; permissive union fallbacks; capture real samples as integration fixtures before Phase 4. |
| **Large result payloads** | **High** | `values` is nested numeric data with no documented bound; `log.output` is raw stdout. Buffering either through the backend adds memory pressure the browser-direct path did not have. | Keep results/logs on separate routes; consider streaming for `/results` later; measure before promising a size bound. |
| **Oversized `ProjectSummary`** | Medium | Flattening 8 endpoints would produce a ~60-field object mixing four lifetimes (project metadata, run state, execution logs, numeric results). | The recommended design structurally prevents it — `ProjectSummary` mirrors one endpoint and gains 2 own fields. |
| **Partial / in-flight responses** | Medium | Logs exist before results; results exist before metadata is edited; runs can be `RUNNING` with half a log tree. | Optionality is the whole strategy (§8). Explicit tests for running/failed/skipped states. |
| **Backwards compatibility** | Medium | Typing a field that currently rides through as `extra` changes how it serializes. | Alias must match the upstream key exactly; a round-trip test guards it. |
| **Duplicated state** | Medium | `simulator` name/id and project/run `updated` are easy to conflate; `BiosimSimulationRun.simulator_version` is already a *different kind of thing* than `simulatorVersion`. | §5 rulings; distinct field names; the naming table in §9 is the contract. |
| **Serialization changes** | Medium | Declared fields serialize before extras, changing JSON key order. | Harmless; documented so no order-sensitive assertion is written. |
| **ABC widening breaks the mock** | Medium | Six new abstract methods break `BiosimServiceMock` and every test importing it. | Add ABC method + REST impl + mock impl in the **same commit**, every phase. |
| **`json_types.py` becomes a grab-bag** | Medium | It already holds three unused enums and a status duplicate; at 8 endpoints it becomes unnavigable. | Phase 0 package split, while the file is still untracked. |
| **Recursive/union models vs mypy strict** | Medium | `SedStyle.base: str \| SedStyle \| None` needs `model_rebuild()`; discriminated unions need `Literal` discriminants. Repo runs `mypy --strict`. | Isolate in Phase 4; run mypy before writing the tests, not after. |
| **Stale ontology information** | Low | A 1-hour TTL can serve a stale KISAO description. | KISAO terms are effectively immutable; 1 hour matches `get_simulator_versions`. |
| **KISAO id separator** | Low but sharp | `KISAO_0000019` vs `KISAO:0000019` silently yields "term not found". | One normalization helper, tested both ways. |
| **Scope creep into the frontend** | Low | Adding routes tempts a same-PR frontend cutover. | Backend-only, per the `project-search-api-frontend-integration.md` precedent: ship the contract, hand off the migration. |

---

## 15. Open Questions / Missing Evidence

**Q1 — Does `/projects/{id}/summary`'s `simulationRun` include `submitted`/`updated`?**
*Why it matters:* determines whether `SimulationRunSummary.submitted/updated` are reachable in project context, and whether the run's `updated` is even available to confuse with the project's.
*Missing:* a real project-summary response. `SimulationRunSummary` (`simulation.ts:108`) declares both, but the test fixture omits them and the project page never reads them.
*Verify:* `curl -s https://api.biosimulations.org/projects/Yeast-cell-cycle-Irons-J-Theor-Biol-2009/summary | jq '.simulationRun | keys'`.
*Provisional:* model both as `str | None = None`. Costs nothing if absent.

**Q2 — Does `/specifications/{id}` return one document or an array?**
*Why:* changes the client's return type and every downstream loop.
*Missing:* a real response. `projects/[id].vue:95` casts to a **singular** `SimulationRunSedDocument`, but `useVisualizations` accepts `T | T[] | undefined`, and `SimulationRunSedDocumentInputsContainer` (`simulation.ts:137`) wraps a `sedDocuments[]` — three signals, not agreeing.
*Verify:* `curl -s .../specifications/61fea483f499ccf25faafc4d | jq 'type'`.
*Provisional:* model the singular document; normalize both shapes at the client boundary into `list[SedDocumentSpec]` internally.

**Q3 — What is the shape of `sedDocuments[].outputs[].dataSets` in the *log* payload?**
*Why:* it is the only listed field with no stated sub-structure, and `SimulationLogs.vue:162` uses only its **presence**.
*Missing:* any sample. It is plausibly `list[str]` (ids) or a list of per-dataset log objects.
*Verify:* `curl -s .../logs/{runId} | jq '.sedDocuments[0].outputs[0].dataSets'`.
*Provisional:* `Any | None` with an explicit comment. Do not guess a structure into the type system.

**Q4 — Do the required `outputs[].xScale/yScale` appear on `SedPlot3D` too, and does `SedPlot3D` appear at all?**
*Why:* affects the union membership and whether `zScale`/`surfaces` need modeling.
*Missing:* evidence that any project uses 3D plots. `useVisualizations.ts:84` handles **only** `SedPlot2D`.
*Verify:* grep captured specifications for `"SedPlot3D"`.
*Provisional:* include `SedPlot3D` in the union with `surfaces` untyped (`list[Any]`) plus the two required scales; do not model surfaces until one is seen.

**Q5 — Should `KISAO_TERMS` be regenerated with `url` and `description`?**
*Why:* it would remove a per-algorithm upstream dependency entirely, and the generator (`scripts/generate_kisao_data.py`) already exists.
*Missing:* whether the KISAO source the script reads exposes definitions and IRIs.
*Verify:* read `backend/scripts/generate_kisao_data.py` and check its source ontology.
*Provisional:* proxy-with-cache now (Phase 7); revisit the generator as a follow-up. The study guide's own note — "Prefer local `KISAO_TERMS` if schema parity is proven" — makes parity the gate, and it is not proven.

**Q6 — Should run-scoped routes live under `/projects` or a new router?**
*Why:* `/runs/{id}`, `/files/{id}`, `/specifications/{id}`, `/logs/{id}`, `/results/{id}/{outputId}` are keyed by **run** id, not project id. Putting them in `projects/router.py` (prefix `/projects`) would misname them; putting them at the root risks colliding with `/simulations/*`, which uses platform **processing** ids and is explicitly *not* wire-compatible (study guide, line 206).
*Missing:* a decision on whether these proxies should be wire-identical to `api.biosimulations.org` (so the frontend swaps only a base URL) or platform-namespaced.
*Verify:* ask the frontend owner; the `project-search-api-frontend-integration.md` handoff is the precedent for how this gets decided.
*Provisional:* **wire-identical paths in a new `biosim_server/legacy_proxy/router.py`** — a one-line `legacy_api_url` → `api_url` swap in the frontend is by far the cheapest migration, and the study guide frames all of these as proxies.

**Q7 — Do any of these upstream endpoints require auth for private runs?**
*Why:* `/projects/{id}/summary` is documented public and the route deliberately forwards no credentials. `/runs/{id}` for a *private* run may behave differently, and `e8eaeeb feat(auth): make ownership and visibility server-authoritative` shows the platform now has its own ACL model.
*Missing:* upstream auth behavior — the study guide marks it "Needs verification" for nearly every row.
*Verify:* request a known-private run id anonymously.
*Provisional:* keep every new route anonymous and forward no credentials (matching `/projects/{id}/summary`); treat platform-side ACL enforcement as **out of scope** for this feature and file it separately. The study guide already flags it ("apply platform ACL for private runs").

**Q8 — Is `values` ever large enough to need streaming?**
*Why:* determines whether `/results` can be a buffering proxy at all.
*Missing:* payload sizes. `resultsSize` in the test fixture is 10,975 bytes — reassuring but a single sample.
*Verify:* `curl -sw '%{size_download}' '.../results/{run}/{out}?includeData=true' -o /dev/null` across several projects.
*Provisional:* buffer for now; add a size guard and revisit if p99 exceeds a few MB.

---

## 16. Recommended Final Design

**Implement Option D, with C's endpoint-scoped models as its substrate — and reject A and B outright.**

Concretely:

1. **`ProjectSummary` remains a faithful, typed mirror of `GET /projects/{id}/summary`** and nothing else. It gains exactly two fields of its own (`created`, `updated`) and delegates everything else to `SimulationRunSummary`. `extra="allow"` stays. Its route, its error mapping, and its wire shape are unchanged.

2. **Every other endpoint gets its own model and its own route.** `ProjectFile`, `SedDocumentSpec`, `RunLog`, `OutputResults`, `KisaoTerm` are peers of `ProjectSummary`, not fields on it. They live in a new `common/biosim_api/` package, one module per endpoint family.

3. **`SimulationRunSummary` is shared between `/runs/{id}/summary` and `ProjectSummary.simulationRun`**, because the repo proves they are the same object. In project context `/runs/{id}/summary` is never called. This is the single largest efficiency win available and it costs nothing.

4. **Reusable nested types where the API actually repeats itself, and nowhere else.** `LogEntry` (5 fields × 4 levels) and `LogMessage` (`skipReason` + `exception`) are the two justified abstractions. `LabeledIdentifier` is reused for creators, keywords, citations, and encodes. No one-field wrapper classes.

5. **Results and KISAO are never eagerly fetched.** Results because the payload is unbounded and the fan-out is per-plot; KISAO because it repeats across every log level and belongs behind a TTL cache. Both stay lazy, client-driven routes.

6. **A `ProjectDetail` composition root is optional and additive** (Phase 8): one mandatory call, two parallel best-effort calls, one conditional call, zero results, zero ontology. Secondary failures degrade fields, never the response — matching the frontend's existing `.catch(() => [])` behavior exactly, so the migration is invisible to users.

7. **Optionality is the error strategy.** Every field optional with a default; lists default to `[]`; nested objects default to `None`; `str` over `StrEnum` on passthrough payloads. The one enum that stays is `BiosimSimulationRunStatus` on `/runs/{id}.status`, which the platform already owns. Fix the currently-required `ProjectSummarySimulationRun.run` in Phase 1 — it is a live 500 waiting on the first project without a run block.

**Why the alternatives are weaker.** **(A) Flatten everything** destroys the 1:1 upstream correspondence that is the proxy's entire justification, forces an 8+N fan-out on every project page, welds numeric result arrays to a metadata request, and collides `updated`, `status`, and `simulator` across four different subjects. **(B) `ProjectSummary` as aggregate root with nested types** is A with better manners: the fan-out and the lifetime-mixing survive, and callers who want only metadata still pay for logs. **(C) Endpoint models with a normalized projection** is the right *substrate* — it is what §3 builds — but as a complete answer it invents a second vocabulary that must be maintained against a schema the study guide repeatedly marks "Needs verification"; the projection would drift from upstream and the frontend would have to learn two shapes.

**D wins because it matches what the repo already decided.** `docs/Biosimulations Platform Backend Study Guide.md:225` — "Keep the existing proxy. Do not build the detail response from `PlatformProjectSearch`." The platform's job on this surface is transport, typing, and error mapping, not re-modeling someone else's domain. This design types every one of the ~90 requested fields, adds no unnecessary network calls, removes one redundant endpoint, keeps the existing route byte-identical, and leaves the suite green after every phase.

---

## Appendix — Two immediate cleanups, independent of this plan

1. `Simulator.VirtualCell = " virtual cell"` (`json_types.py:29`) has a **leading space**.
2. `ModelFormat`, `Simulator`, and `RunStatus` in `json_types.py` are **unused** (verified by grep); `RunStatus` duplicates `BiosimSimulationRunStatus` (`biosim_runs/models.py:68`).

Phase 0 removes all four.
