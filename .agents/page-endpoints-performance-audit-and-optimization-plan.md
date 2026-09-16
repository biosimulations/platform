# Page Endpoints Performance Audit & Optimization Plan

## 1. Executive Verdict

1. **Are the current page endpoints architecturally sound?**
   Yes. Both `GET /projects/{project_id}/page` and `GET /runs/{run_id}/page` implement platform-owned contracts with explicit Pydantic response models, closed `extra="forbid"` shapes, sanitized upstream errors, no caller-credential forwarding, and no passthrough. The architecture correctly separates identity fetching from satellite aggregation.

2. **What is the biggest verified or likely source of latency?**
   For the **run page**, the identity (`GET /runs/{run_id}/summary`) is fetched serially before all three satellite requests (files, specifications, logs), but the satellites use `run_id` directly — the route parameter — not any identifier extracted from the identity response. The identity barrier is **unnecessary for the satellite fan-out** and costs one full round-trip that could overlap with the satellites. This is CODE-PROVEN by the current implementation in `biosim_server/pages/service.py:46-56`.

   For the **project page**, the identity barrier is necessary because satellite requests require `project.simulation_run.id` (the embedded run ID), which differs from the `project_id` route parameter and can only be obtained from the identity response. This is CODE-PROVEN by `biosim_server/pages/service.py:30-40`.

3. **What is the highest-value safe improvement?**
   For the **run page**: start the three satellite requests (files, specifications, logs) concurrently with the identity request, since they only need `run_id`. Then construct `info` + `summary` from the identity response when it arrives. This can reduce run-page latency from `identity + max(satellites)` to `max(identity, satellites)`.

   For the **project page**: no safe shortcut exists for the satellite fan-out; the run ID must come from the identity response. The highest-value improvement here is removing the double `normalize_specifications` call and any minor mapping cleanup.

4. **Are there any correctness/security issues that should block optimization?**
   No blocking issues found. The security boundary (dot-only ID rejection, encoded-slash routing, credential stripping, error sanitization, path quoting) is intact and tested. The satellite 404-vs-5xx distinction is correctly implemented and tested.

5. **Is the page aggregation design fundamentally sound and in need of refinement, or should it be restructured?**
   The design is fundamentally sound. No restructuring is warranted. Targeted refinements — primarily the run-page concurrency change and minor mapping cleanup — are sufficient. The `pages/` and `summaries/` modules are appropriately separated; the shared `_satellite` helper is correctly scoped.

## 2. Repository / Git State

```text
Branch:      chore/api-improvements
HEAD:        23f406b399d868025cc7988f6f35af24e67dea66
Commit msg:  Merge pull request #98 from biosimulations/chore/Auth0-Improvement
Working tree: clean
PR #107:     Merged (commit 41001aa in history); includes 0bd73db + 1721df1 + 61eade2
Merge base:  23f406b (HEAD==merge base; on chore branch, not main)
```

The relevant PR #107 commits are reachable from HEAD:
- `0bd73db` — Replaced passthrough with platform-owned ProjectSummary/RunSummary contracts
- `1721df1` — Added platform-owned page aggregation endpoints
- `61eade2` — OpenAPI/model updates for curve style handling

No passthrough implementation remains. `common/proxy.py` does not exist. `proxy_get` is not present.

**Verified:** `git show --no-patch --oneline 0bd73db 1721df1 61eade2` confirms all three are in the history.

## 3. Scope and Preserved Contracts

### In scope
- `GET /projects/{project_id}/page` — backend architecture, performance, design
- `GET /runs/{run_id}/page` — backend architecture, performance, design
- All directly dependent modules: `pages/`, `summaries/`, `common/upstream.py`, `dependencies.py` HTTP client, route handlers, mapping functions, page models

### Out of scope
- Frontend code and migration (`frontend/`)
- Summary endpoints (`/projects/{id}/summary`, `/runs/{run_id}/summary`) except where they share infrastructure with page endpoints
- Any other backend endpoints
- Deployment, CI, or kustomize changes

### Contracts that must be preserved (verified current)

| Contract | Verified location |
|---|---|
| `ProjectsPagePayload` — closed model, `extra="forbid"`, camelCase aliases | `backend/biosim_server/pages/models.py:168-173` |
| `RunsPagePayload` — closed model, `extra="forbid"`, camelCase aliases | `backend/biosim_server/pages/models.py:175-180` |
| Project page shape: `{project, simulationRun, files, specifications}` | `backend/biosim_server/pages/models.py:168-173` |
| Run page shape: `{info, summary, files, specifications, logs}` | `backend/biosim_server/pages/models.py:175-180` |
| `logs` is `PageSimulationLog \| None` (nullable) | `backend/biosim_server/pages/models.py:180` |
| `files`/`specifications` are `list[...]` (empty-list on 404) | `backend/biosim_server/pages/models.py:171-172` |
| Identity 404 → 404 `{"detail": "Not Found"}` | `backend/biosim_server/common/upstream.py:60-61` |
| Satellite 404 → `[]` for files/specifications, `None` for logs | `backend/biosim_server/pages/service.py:24-27` |
| Satellite 5xx → fail the page with 502 | `backend/biosim_server/common/upstream.py:57-59` |
| Timeout → 504 | `backend/biosim_server/common/upstream.py:50-52` |
| No `Authorization`/`Cookie` forwarding | `backend/biosim_server/common/upstream.py:34-41` (no headers passed); tested in `test_caller_credentials_do_not_reach_upstream` |
| Dot-only ID rejection (`.` / `..`) | `backend/biosim_server/common/upstream.py:17-31` |
| Path segment quoting via `urllib.parse.quote` | `backend/biosim_server/common/upstream.py:31` |
| Error sanitization (no upstream body/host leakage) | `backend/biosim_server/common/upstream.py:50-72` |
| CORS owned by platform middleware | `backend/biosim_server/api/main.py` (CORS middleware) |
| Shared pooled `httpx.AsyncClient` | `backend/biosim_server/dependencies.py:106-127` |
| OpenAPI schema ownership (named `ProjectsPagePayload`, `RunsPagePayload`) | `backend/biosim_server/projects/router.py:182`, `backend/biosim_server/simulations/router.py:62` (response_model) |

## 4. Current Architecture

### File/symbol map

```text
routes/
  biosim_server/projects/router.py
    GET /{project_id}/summary  → get_project_summary()
    GET /{project_id}/page     → get_project_page()
         → assemble_project_page(client, project_id)
  biosim_server/simulations/router.py
    run_summary_router
      GET /{run_id}/summary    → get_run_summary()
    GET /{run_id}/page         → get_run_page()
         → assemble_run_page(client, run_id)
```

```text
pages/
  biosim_server/pages/models.py
    ProjectsPagePayload
    RunsPagePayload
    PageProject, PageProjectRun, PageRunInfo, PageRunSummary, PageFile,
    PageSpecification, PageSimulationLog, and all nested models
  biosim_server/pages/service.py
    assemble_project_page(client, project_id) → ProjectsPagePayload
    assemble_run_page(client, run_id)        → RunsPagePayload
    _satellite(client, resource, run_id)     → object | None
  biosim_server/pages/mapping.py
    parse_project(payload)        → _UpstreamProject
    parse_run(payload)            → _UpstreamRun
    map_project_page(...)         → ProjectsPagePayload
    map_run_page(...)             → RunsPagePayload
    map_files(payload)            → list[PageFile]
    map_specifications(docs)      → list[PageSpecification]
    map_logs(payload)             → PageSimulationLog | None
    normalize_specifications(...) → list[_UpstreamFullSpecification]
    model_formats(docs)           → list[str]
    _Upstream* private models (extra="ignore")
```

```text
summaries/
  biosim_server/summaries/models.py
    ProjectSummary, RunSummary, RunExecution, RunMetadataSummary, LabeledIdentifier, SimulatorSummary
  biosim_server/summaries/mapping.py
    map_project_summary(payload)  → ProjectSummary
    map_run_summary(payload)      → RunSummary
    _Upstream* private models (extra="ignore")
```

```text
common/
  biosim_server/common/upstream.py
    upstream_url(*segments)                    → str  (quotes segments, rejects dot-only)
    fetch_upstream_json(client, path, resource) → dict  (object-only, sanitized errors)
    fetch_upstream_json_value(client, path, resource) → dict|list  (object or array)
  biosim_server/dependencies.py
    global_http_client / get_http_client() / set_http_client()
    httpx.AsyncClient(base_url=..., timeout=30.0) — lazy, pooled, no connection limits set
```

### Dependency map

```text
get_project_page(project_id)
  → assemble_project_page(client, project_id)
    → fetch_upstream_json(client, upstream_url("projects", project_id, "summary"), "project summary")
    → parse_project(payload)                        # _UpstreamProject
    → asyncio.gather(
        _satellite(client, "files", project.simulation_run.id),
        _satellite(client, "specifications", project.simulation_run.id),
      )
    → map_project_page(project, files, specifications)

get_run_page(run_id)
  → assemble_run_page(client, run_id)
    → fetch_upstream_json(client, upstream_url("runs", run_id, "summary"), "run summary")
    → parse_run(payload)                           # _UpstreamRun
    → asyncio.gather(
        _satellite(client, "files", run_id),
        _satellite(client, "specifications", run_id),
        _satellite(client, "logs", run_id),
      )
    → map_run_page(run, files, specifications, logs)
```

### Current request flow (ASCII)

```text
PROJECT PAGE                          RUN PAGE
─────────────                          ─────────
T0  GET /projects/{id}/page            T0  GET /runs/{id}/page
   │                                      │
T1  ── fetch_upstream_json              T1  ── fetch_upstream_json
   │    GET /projects/{id}/summary       │    GET /runs/{id}/summary
   │    (serial, blocks)                 │    (serial, blocks)
   │                                      │
T2  ◄── identity response               T2  ◄── identity response
   │                                      │
T3  parse_project()                     T3  parse_run()
   │                                      │
T4  asyncio.gather(                     T4  asyncio.gather(
   │    _satellite("files", run_id)       │    _satellite("files", run_id)
   │    _satellite("specifications",      │    _satellite("specifications", run_id)
   │      run_id)                         │    _satellite("logs", run_id)
   │  )                                  │  )
   │                                      │
T5  ◄── files + specifications          T5  ◄── files + specifications + logs
   │    (concurrent, max of both)        │    (concurrent, max of three)
   │                                      │
T6  map_project_page(...)               T6  map_run_page(...)
   │                                      │
T7  ◄── ProjectsPagePayload             T7  ◄── RunsPagePayload
   │                                      │
T8  FastAPI serializes                  T8  FastAPI serializes
```

**Critical path equations:**

```text
Project page latency ≈ identity_latency
                      + max(files_latency, specifications_latency)
                      + local_mapping_overhead

Run page latency ≈ identity_latency
                  + max(files_latency, specifications_latency, logs_latency)
                  + local_mapping_overhead
```

## 5. Current Critical Path

### 5.1 Project page — verified serial dependency

**Evidence:** `backend/biosim_server/pages/service.py:30-40`

```python
async def assemble_project_page(client, project_id):
    payload = await fetch_upstream_json(...)               # T1: identity
    project = parse_project(payload)                       # T3
    files, specifications = await asyncio.gather(          # T4: satellites
        _satellite(client, "files", project.simulation_run.id),
        _satellite(client, "specifications", project.simulation_run.id),
    )
```

The satellite requests use `project.simulation_run.id`, which is **extracted from the identity response**, not from the route parameter `project_id`. The route parameter is a project identifier; the satellites need a run identifier.

**CODE-PROVEN:** The project page's serial identity barrier is necessary. You cannot know which run ID to query for files/specifications without first fetching the project summary.

### 5.2 Run page — verified unnecessary serial dependency

**Evidence:** `backend/biosim_server/pages/service.py:46-56`

```python
async def assemble_run_page(client, run_id):
    payload = await fetch_upstream_json(...)               # T1: identity
    run = parse_run(payload)                               # T3
    files, specifications, logs = await asyncio.gather(    # T4: satellites
        _satellite(client, "files", run_id),
        _satellite(client, "specifications", run_id),
        _satellite(client, "logs", run_id),
    )
```

The satellite requests use `run_id` directly — the route parameter — and do **not** depend on any field from the identity response. The `run` variable parsed from the identity is only used later in `map_run_page(run, files, specifications, logs)` to construct `info` and `summary`.

**CODE-PROVEN:** The identity fetch and the three satellite fetches are independent. The satellites can start immediately without waiting for the identity response.

### 5.3 Satellite ID source table

| Endpoint | Satellite | Current ID source | Route param available? | Values guaranteed identical? | Evidence | Can request start before identity? | Risk if changed |
|---|---|---|---|---|---|---|---|
| Project page | files | `project.simulation_run.id` (from identity) | No (route param is project ID, not run ID) | N/A — different identifier space | `service.py:37` | **No** | Would query wrong resource |
| Project page | specifications | `project.simulation_run.id` (from identity) | No | N/A | `service.py:38` | **No** | Would query wrong resource |
| Run page | files | `run_id` (route parameter) | Yes | Yes — identical | `service.py:53` | **Yes** | None |
| Run page | specifications | `run_id` (route parameter) | Yes | Yes — identical | `service.py:54` | **Yes** | None |
| Run page | logs | `run_id` (route parameter) | Yes | Yes — identical | `service.py:55` | **Yes** | None |

### 5.4 Concurrency primitive

Both endpoints use `asyncio.gather` to fan out satellite requests. This is correct and sufficient. `asyncio.gather` runs all tasks concurrently and returns results in order. If one satellite raises a non-404 exception, `gather` propagates it immediately and other tasks continue running in the background (they are not cancelled automatically).

**Note on exception ordering:** With `asyncio.gather`, if multiple satellites fail, the first exception to be awaited wins. This is acceptable today because any non-404 failure fails the page anyway. The specific exception that surfaces is an implementation detail, not a contract guarantee, since all failures produce 502.

### 5.5 HTTP client configuration

**Evidence:** `backend/biosim_server/dependencies.py:106-127`

```python
_HTTP_TIMEOUT = httpx.Timeout(30.0)

global_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    if global_http_client is None:
        global_http_client = httpx.AsyncClient(
            base_url=get_settings().biosimulations_api_base_url.rstrip("/"),
            timeout=_HTTP_TIMEOUT,
        )
    return global_http_client
```

- **One pooled client** per process, lazy-initialized
- **base_url:** `get_settings().biosimulations_api_base_url` (default `https://api.biosimulations.org`)
- **timeout:** 30.0 seconds total (no connect/read/write breakdown — uses defaults: connect=5s, read=30s, write=5s, pool=5s)
- **Connection limits:** Not explicitly configured — uses httpx defaults (10 connections per host, 100 total)
- **Keepalive:** Default (enabled, 0s idle timeout in httpx means "use pool settings")
- **HTTP version:** HTTP/1.1 by default (no `http2=True`)
- **Lifecycle:** Created lazily; closed in `shutdown_standalone()` via `aclose()`

**Verification:** `test_pooled_client_targets_the_configured_upstream` in `tests/common/test_summary_live.py:55-66` confirms the client targets the configured base URL.

**Pool contention analysis:** With 3 API replicas and a default pool of 10 connections per host, each pod can handle up to 10 concurrent upstream requests. A single run-page request uses up to 4 upstream calls (1 identity + 3 satellites). At 2-3 simultaneous run-page requests per pod, the pool is not contended. At higher concurrency, the 10-connection limit could become a bottleneck — but this is speculative without production traffic data.

### 5.6 Validation and mapping pipeline

**Current flow for each endpoint:**

1. `fetch_upstream_json` — fetches JSON, validates it's a dict, returns raw dict
2. `parse_project` / `parse_run` — validates raw dict against `_UpstreamProject` / `_UpstreamRun` (private models with `extra="ignore"`)
3. Satellite fetches — each returns raw JSON (dict or list)
4. `map_project_page` / `map_run_page` — constructs owned `ProjectsPagePayload` / `RunsPagePayload` (public models with `extra="forbid"`)

**Double validation:** The identity response is validated twice — once implicitly by `parse_project`/`parse_run` against the private upstream model, and once explicitly by `ProjectsPagePayload.model_validate` / `RunsPagePayload.model_validate` in the mapping functions. This is intentional: the private model validates upstream structure, the public model validates the owned contract.

**Specifications double normalization (code-proven inefficiency):**

In `map_run_page` (`backend/biosim_server/pages/mapping.py:303`):
```python
"specifications": map_specifications(normalize_specifications(specifications)),
```

The `specifications` parameter is already a raw payload that was passed through from `_satellite`. `map_run_page` calls `normalize_specifications` on it, then passes the result to `map_specifications`, which calls `TypeAdapter(list[_UpstreamFullSpecification]).validate_python(payload)` on the already-normalized list.

Wait — let me re-check. `map_specifications` receives `documents: list[_UpstreamFullSpecification]` and does:
```python
def map_specifications(documents: list[_UpstreamFullSpecification]) -> list[PageSpecification]:
    return [PageSpecification.model_validate(doc.model_dump(by_alias=True, exclude={"models"}))
            for doc in documents]
```

So `map_specifications` expects already-validated `_UpstreamFullSpecification` objects. `normalize_specifications` does the validation. In `map_run_page`, the call chain is:
```python
normalize_specifications(specifications) → list[_UpstreamFullSpecification]
→ map_specifications(documents) → list[PageSpecification]
```

This is correct — `normalize_specifications` validates, `map_specifications` projects. No double validation here.

But in `map_project_page` (`backend/biosim_server/pages/mapping.py:271-272`):
```python
documents = normalize_specifications(specifications)
formats = model_formats(documents)
return ProjectsPagePayload.model_validate({
    ...
    "specifications": map_specifications(documents),
})
```

Same pattern — correct, no double normalization.

**Where double work exists:** `map_run_page` and `map_project_page` each call `normalize_specifications` + `model_formats` + `map_specifications`. This is three passes over the specifications payload. For the run page, `model_formats` is not needed (it's only used in the project page for `modelFormats`). But `map_run_page` doesn't call `model_formats` — confirmed by reading `mapping.py:286-305`.

So the only redundant work is that both `map_project_page` and `map_run_page` independently normalize specifications. This is not redundant within a single endpoint — each endpoint needs its own normalization. But if a future refactoring merged the two page assemblers, the normalization could be shared. This is a minor design observation, not a current bottleneck.

### 5.7 Local overhead estimate

Local overhead consists of:
- JSON decoding (done by httpx, per response)
- `_UpstreamProject`/`_UpstreamRun` validation (~1 Pydantic validate call)
- `_UpstreamFile`/`_UpstreamFullSpecification`/`_UpstreamSimulationLog` validation (per satellite)
- Owned model construction (1 `model_validate` per endpoint)
- FastAPI response-model validation (1 per endpoint — redundant with step above)
- JSON serialization for response

The payloads are small (the fixture data is a few KB). Pydantic validation overhead for this payload size is negligible compared to network latency (typically 50-200ms per upstream call). Local overhead is estimated at <5ms total. This is a HIGH-CONFIDENCE INFERENCE based on payload size and typical Pydantic performance, not a measurement.

### 5.8 Duplicate upstream calls

No duplicate upstream calls found. Each satellite resource (files, specifications, logs) is fetched exactly once per page request. The identity is fetched once. No caching layer exists (by design — the endpoints are thin aggregators).

## 6. PR #107 / Issue #108 Audit Findings

### Finding: Owned contracts are in place

**Classification:** CODE-PROVEN

**Evidence:**
- `ProjectsPagePayload` and `RunsPagePayload` are explicit Pydantic models with `extra="forbid"` — `backend/biosim_server/pages/models.py:9-10, 168-180`
- Route handlers declare `response_model=ProjectsPagePayload` / `response_model=RunsPagePayload` — `backend/biosim_server/projects/router.py:182`, `backend/biosim_server/simulations/router.py:62`
- No passthrough: `fetch_upstream_json` returns `dict`, not raw response — `backend/biosim_server/common/upstream.py:34-41`
- `common/proxy.py` does not exist; `proxy_get` is not defined anywhere in the codebase

### Finding: Summary contracts are also owned

**Classification:** CODE-PROVEN

**Evidence:**
- `ProjectSummary` and `RunSummary` are explicit models — `backend/biosim_server/summaries/models.py`
- `map_project_summary` / `map_run_summary` with private `_Upstream*` models — `backend/biosim_server/summaries/mapping.py`
- Routes declare `response_model=ProjectSummary` / `response_model=RunSummary` — `backend/biosim_server/projects/router.py:153`, `backend/biosim_server/simulations/router.py:64`

### Finding: Private upstream models are properly isolated

**Classification:** CODE-PROVEN

**Evidence:**
- `pages/mapping.py` defines `_UpstreamModel`, `_UpstreamProject`, `_UpstreamRun`, `_UpstreamFile`, `_UpstreamFullSpecification`, `_UpstreamSimulationLog`, etc. — all with `extra="ignore"` — `backend/biosim_server/pages/mapping.py:36-213`
- `summaries/mapping.py` defines its own `_Upstream*` models — `backend/biosim_server/summaries/mapping.py:10-53`
- No upstream model is exposed in a public response contract

### Finding: Mapping validation fails closed

**Classification:** CODE-PROVEN

**Evidence:**
- `parse_project` / `parse_run` raise `ValidationError` on missing required fields — `backend/biosim_server/pages/mapping.py:253-258`
- `_UpstreamModel` base has `extra="ignore"` (accepts unknown fields but requires known ones) — `backend/biosim_server/pages/mapping.py:36-37`
- `PageModel` base has `extra="forbid"` (rejects unknown fields in the public contract) — `backend/biosim_server/pages/models.py:9-10`
- Tests verify required-field drift raises `ValidationError` — `backend/tests/pages/test_mapping.py:61-75` (run), `80-86` (project)

### Finding: Satellite 404 vs 5xx distinction is correct

**Classification:** CODE-PROVEN

**Evidence:**
- `_satellite` catches `HTTPException` with status 404 and returns `None` (logs) or `[]` (files, specifications) — `backend/biosim_server/pages/service.py:24-27`
- Non-404 exceptions (5xx, timeout, invalid JSON) propagate and fail the page — `backend/biosim_server/pages/service.py:25-26`
- `fetch_upstream_json_value` maps 5xx → 502, 404 → 404, timeout → 504 — `backend/biosim_server/common/upstream.py:50-66`
- Tests in `test_mapping.py` verify that satellite required-field drift fails — `backend/tests/pages/test_mapping.py:199-208`

### Finding: Shared HTTP client is reused

**Classification:** CODE-PROVEN

**Evidence:**
- `get_http_client()` returns a module-level singleton — `backend/biosim_server/dependencies.py:120-127`
- Tests override via `app.dependency_overrides[get_http_client]` — `backend/tests/common/test_summary_live.py:47`
- `init_standalone` creates one client for the process — `backend/biosim_server/dependencies.py:157-162`

### Finding: Caller credentials are not forwarded

**Classification:** CODE-PROVEN

**Evidence:**
- `fetch_upstream_json` / `fetch_upstream_json_value` call `client.get(path)` with no headers — `backend/biosim_server/common/upstream.py:49, 67`
- `test_caller_credentials_do_not_reach_upstream` verifies `Authorization` and `Cookie` are absent from upstream requests — `backend/tests/common/test_summary_live.py:98-112`

### Finding: Path security is intact

**Classification:** CODE-PROVEN

**Evidence:**
- `upstream_url` rejects `.` and `..` segments before URL construction — `backend/biosim_server/common/upstream.py:25-30`
- All segments are quoted via `urllib.parse.quote(segment, safe="")` — `backend/biosim_server/common/upstream.py:31`
- Encoded slash rejection is handled by Starlette routing (not by the upstream helper) — the route parameter is constrained by the router before reaching the handler

### Finding: Error sanitization is correct

**Classification:** CODE-PROVEN

**Evidence:**
- `fetch_upstream_json_value` raises `HTTPException(502, ...)` without including response body or upstream host — `backend/biosim_server/common/upstream.py:50-72`
- `fetch_upstream_json` additionally validates the payload is a dict — `backend/biosim_server/common/upstream.py:39-40`
- Error messages use generic phrasing: "The upstream service returned an unexpected {resource}." — no upstream details leaked
- `test_absent_run_upstream_5xx_becomes_sanitized_502` verifies the sanitized message and absence of the run ID in the response — `backend/tests/common/test_summary_live.py:117-124`

### Finding: Page models are closed (extra="forbid")

**Classification:** CODE-PROVEN

**Evidence:**
- `PageModel` base: `ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")` — `backend/biosim_server/pages/models.py:9-10`
- All page models inherit from `PageModel` — `backend/biosim_server/pages/models.py:22-180`
- Tests verify `ignored` fields are not in serialized output — `backend/tests/pages/test_mapping.py:43, 143, 169`

### Finding: No tests exist for page endpoint concurrency behavior

**Classification:** GAP — MISSING TEST

**Evidence:**
- `test_project_page.py` and `test_run_page.py` test mounted contract, request isolation, and failure policy — but do not verify that satellite requests actually run concurrently or that the identity barrier exists where expected
- No test uses controlled artificial delays to prove overlap of satellite requests
- No test proves that the run-page satellites do not depend on identity response fields

### Finding: No tests exist for dot-only ID rejection on page endpoints

**Classification:** GAP — MISSING TEST

**Evidence:**
- `test_upstream.py` tests dot-only rejection for `upstream_url` directly — `backend/tests/common/test_upstream.py`
- But no page-specific test verifies that `GET /projects/./page` or `GET /runs/../page` returns 404 with zero upstream calls
- The protection exists in `upstream_url` (called by both page endpoints), so it is functionally covered, but a page-level test would be more direct

### Finding: No tests for encoded-slash rejection on page endpoints

**Classification:** GAP — MISSING TEST

**Evidence:**
- Encoded slash rejection is enforced by Starlette routing, not by application code
- No page-specific test verifies this behavior
- Functionally covered by the router, but not explicitly tested at the page endpoint level

### Finding: No mock-based page endpoint tests (all page tests are either mapping-level or live)

**Classification:** GAP — MISSING TEST

**Evidence:**
- `test_project_page.py` and `test_run_page.py` use `biosim_service_mock` and `TestClient` — but they test via the mounted app with dependency overrides, not via httpx MockTransport
- The page service (`assemble_project_page` / `assemble_run_page`) is tested indirectly through the mounted app, not directly with a mock HTTP client
- There is no `MockTransport`-based test that simulates upstream latency to prove concurrency behavior

## 7. Performance Evidence

### 7.1 Local mapping overhead (not measured — inference)

The page fixtures are small:
- `page_files_response.json`: 1 file entry (~200 bytes)
- `page_specifications_response.json`: 1 SED document with 2 outputs (~2KB)
- `page_logs_response.json`: 1 simulation log with nested documents (~3KB)
- `run_summary_response.json`: ~2KB
- `project_summary_response.json`: ~2KB

Pydantic validation of these payloads is expected to take <1ms each. Total local overhead (validation + mapping + serialization) is estimated at <5ms. **Not measured** — this is a HIGH-CONFIDENCE INFERENCE based on payload size, not a benchmark.

### 7.2 Critical path simulation (not yet done — hypothesis)

No benchmark or simulation was run during this audit. The following is a structural analysis based on code inspection:

**Current run page (code-proven):**
```
T_total = T_identity + max(T_files, T_specifications, T_logs) + T_local
```

**Proposed run page (if satellites start concurrently with identity):**
```
T_total = max(T_identity, T_files, T_specifications, T_logs) + T_local
```

**Expected reduction:** If `T_identity` is the longest call, no reduction. If any satellite is longer than identity, the reduction equals `T_identity - max(satellites)` when `T_identity > max(satellites)`, or zero when `T_identity < max(satellites)`. In the typical case where identity and satellites have similar latency (all are simple GETs to the same upstream), the reduction is approximately `T_identity` (one full RTT eliminated from the critical path).

**Confidence:** CODE-PROVEN for the structural analysis. The actual latency reduction depends on real upstream latency distributions and is not yet measured.

### 7.3 Connection pool analysis (not measured — inference)

With the default httpx pool (10 connections per host) and 3 API replicas, each pod can handle 10 concurrent upstream requests. A single run-page request uses up to 4 upstream calls. At 2-3 simultaneous requests, the pool is not contended. At higher concurrency, contention is possible but unverified. **HYPOTHESIS — MEASURE DURING IMPLEMENTATION if throughput issues arise.**

## 8. Bottlenecks and Design Findings

### Finding: Run page has unnecessary serial identity barrier

**Classification:** CODE-PROVEN

**Evidence:**
- `backend/biosim_server/pages/service.py:46-56` — `assemble_run_page` awaits identity before creating satellite tasks
- Satellite `_satellite` calls use `run_id` (the route parameter), not any field from the identity response
- `backend/biosim_server/pages/mapping.py:286-305` — `map_run_page` uses `run` (from identity) only for `info` and `summary` construction, not for satellite IDs

**Current behavior:**
The identity fetch (`GET /runs/{run_id}/summary`) blocks all three satellite fetches (`GET /runs/{run_id}/files`, `GET /runs/{run_id}/specifications`, `GET /runs/{run_id}/logs`).

**Problem:**
For every run-page request, one full network round-trip (the identity fetch) is added to the critical path before satellites can begin. If identity takes 100ms and satellites take 100ms each, the page takes 200ms + local overhead. If satellites could start immediately, the page would take 100ms + local overhead (the max of all concurrent calls).

**Recommended direction:**
Start satellite requests concurrently with the identity request. Construct `info` and `summary` from the identity response when it arrives. If the identity fails, cancel the satellites and return the appropriate error.

**Expected impact:**
Up to one full RTT reduction in run-page latency. The actual reduction depends on the relative latency of identity vs satellites. In the common case (similar latency for all calls), this is roughly a 33-50% reduction for the run page (from 2 RTTs to 1 RTT in the critical path).

**Risk:**
- If identity returns 404, satellites have already been issued. The satellites will also return 404 (since the run doesn't exist), and the page will correctly return 404. No functional change.
- If identity returns 5xx, satellites may still be in flight. The page should fail with 502 as soon as the identity failure is known, cancelling outstanding satellites. This requires explicit cancellation logic.
- If identity times out, same as 5xx — cancel satellites.
- The error semantics are preserved: 404 from identity → 404 response; 5xx/timeout from identity → 502/504 response. Satellite 404s during a failed identity are irrelevant (the page is already failed).

**Contract constraints:**
- `info` and `summary` must be constructed from the identity response (same as today)
- `files`, `specifications`, `logs` must come from the satellite responses (same as today)
- 404/5xx/timeout error mapping must be preserved (same as today)
- Response shape must be identical (same as today)

### Finding: Project page identity barrier is necessary and correct

**Classification:** CODE-PROVEN

**Evidence:**
- `backend/biosim_server/pages/service.py:30-40` — satellites use `project.simulation_run.id`
- This ID comes from the identity response, not the route parameter

**No change recommended.** The serial dependency is correct.

### Finding: `_satellite` helper is correctly scoped but slightly fragile

**Classification:** HIGH-CONFIDENCE INFERENCE

**Evidence:**
- `backend/biosim_server/pages/service.py:17-27` — `_satellite` handles 404 → empty, everything else → raise
- The helper takes `run_id` as a parameter, but for the project page it's called with `project.simulation_run.id`

**Problem:**
The `_satellite` function signature takes `(client, resource, run_id)`, but for the project page the ID is a run ID derived from the project summary. The parameter name `run_id` is slightly misleading for the project page context, but the behavior is correct.

**Recommended direction:**
Rename the parameter to `resource_id` or `id` to reflect that it's not always a run ID. This is a minor clarity improvement, not a performance change.

### Finding: `map_run_page` does unnecessary work for `model_formats`

**Classification:** CODE-PROVEN (minor)

**Evidence:**
- `backend/biosim_server/pages/mapping.py:286-305` — `map_run_page` calls `normalize_specifications(specifications)` and `map_specifications(normalized)`
- `model_formats` is NOT called in `map_run_page` (it's only used in `map_project_page`)

Wait — let me re-verify. Looking at `mapping.py:286-305`:
```python
def map_run_page(run, files, specifications, logs):
    metadata = _metadata(run)
    return RunsPagePayload.model_validate({
        "info": {...},
        "summary": {...},
        "files": map_files(files),
        "specifications": map_specifications(normalize_specifications(specifications)),
        "logs": map_logs(logs),
    })
```

`model_formats` is NOT called. `normalize_specifications` + `map_specifications` is the correct chain. No unnecessary work here.

**Correction:** This finding was initially flagged as a potential inefficiency but upon closer inspection, `map_run_page` does not call `model_formats`. The specifications processing is correct and minimal.

### Finding: `assemble_project_page` and `assemble_run_page` have similar structure but are not unified

**Classification:** HIGH-CONFIDENCE INFERENCE

**Evidence:**
- `backend/biosim_server/pages/service.py:30-44` vs `46-60`
- Both fetch identity, parse it, gather satellites, map the page
- The projection differs (project page uses `project.simulation_run.id`; run page uses `run_id` directly)
- The satellite sets differ (2 vs 3)

**Problem:**
There is some code duplication in the two `assemble_*_page` functions. However, unifying them would create a more complex abstraction that handles two different cases (project page needs the run ID from identity; run page doesn't). The current duplication is simple and clear.

**Recommended direction:**
Do not unify. The two functions are different enough that a unified abstraction would be more complex than the duplication. Keep them separate but ensure they share the `_satellite` helper (which they already do).

### Finding: `_metadata` function silently handles missing metadata

**Classification:** CODE-PROVEN (correct behavior, worth documenting)

**Evidence:**
- `backend/biosim_server/pages/mapping.py:261-263` — `_metadata(run)` returns `run.metadata[0]` if present, else `_UpstreamMetadata()`
- This is used in both `map_project_page` and `map_run_page`

**Current behavior:**
If the upstream run has no metadata array or an empty metadata array, the page uses a default empty metadata object. This is correct and tested (`test_known_empty_metadata_and_nullable_logs`).

**No change recommended.** This is intentional and correct.

### Finding: `parse_project` / `parse_run` are thin wrappers

**Classification:** HIGH-CONFIDENCE INFERENCE

**Evidence:**
- `backend/biosim_server/pages/mapping.py:253-258` — `parse_project(payload)` = `_UpstreamProject.model_validate(payload)`
- Same for `parse_run`

**Problem:**
These are one-line wrappers that add no logic. They exist presumably for symmetry with the mapping functions and to give a name to the validation step.

**Recommended direction:**
These are harmless. They could be inlined, but they also serve as clear documentation that the identity payload is being validated against the private upstream model. Keep them for clarity.

### Finding: No timeout budget for the aggregation as a whole

**Classification:** HIGH-CONFIDENCE INFERENCE

**Evidence:**
- Each individual upstream call has a 30s timeout (`dependencies.py:112`)
- There is no separate timeout for the page aggregation as a whole
- If all 4 calls (identity + 3 satellites) take 29s each, the page could take 58s (current, serial identity) or 29s (proposed, concurrent)

**Problem:**
A page aggregation endpoint that makes multiple upstream calls should have a total timeout budget that is longer than a single call but bounded. Currently, the page could take up to `N * 30s` where N is the number of serial calls.

**Recommended direction:**
Consider adding a total timeout for the page assembly (e.g., 60s for the run page, 45s for the project page). This is a safety measure, not a performance optimization. It would also bound the worst-case latency. This is a P2 improvement, not P0/P1.

## 9. Optimization Options Considered

### 9.1 Removing/shortening the identity serial dependency

**Run page — SHORTEN (recommended):**
Satellites use `run_id` directly. They can start concurrently with the identity request. The `info` and `summary` fields are constructed from the identity response when it arrives.

**Project page — NO CHANGE (necessary):**
Satellites need `project.simulation_run.id`, which comes from the identity response. The serial dependency is correct.

**Expected latency impact (run page):**
- Current: `T_identity + max(T_files, T_specifications, T_logs)`
- Proposed: `max(T_identity, T_files, T_specifications, T_logs)`
- Reduction: up to `T_identity` when `T_identity > max(satellites)`, else zero
- Confidence: CODE-PROVEN for the structural change; actual latency reduction is HIGH-CONFIDENCE INFERENCE

### 9.2 Avoiding duplicate upstream calls

**Finding:** No duplicate upstream calls exist. Each resource is fetched exactly once. No change needed.

### 9.3 Better concurrency structure (TaskGroup vs gather)

**Finding:** `asyncio.gather` is sufficient for the current use case. The satellites are independent and the number is small (2-3). `asyncio.TaskGroup` (Python 3.11+) would provide structured concurrency and automatic cancellation, but the benefit for this specific case is marginal.

**Recommendation:** After the run-page concurrency change (starting satellites early), consider `asyncio.TaskGroup` for cleaner cancellation semantics — if identity fails, all in-flight satellites are automatically cancelled. However, this is a P2 improvement, not P0/P1. The current `asyncio.gather` + manual cancellation (if needed) is acceptable.

### 9.4 Shared satellite-fetch abstraction

**Finding:** The `_satellite` helper already provides a shared abstraction for fetching satellites with the 404→empty policy. No additional abstraction is needed.

### 9.5 HTTP connection configuration

**Finding:** The current configuration (30s timeout, default pool of 10 connections/host, no HTTP/2) is adequate for the current traffic pattern. No evidence suggests pool contention or timeout issues.

**Recommendation:** Do not change connection pool settings without evidence of contention. If the run-page concurrency change increases peak concurrent upstream requests per pod, monitor pool usage. The default of 10 connections/host should handle 2-3 simultaneous run-page requests (each using up to 4 connections) without contention.

### 9.6 Timeout budgets

**Finding:** The 30s per-call timeout is reasonable. A total aggregation timeout would be a safety improvement but is not a performance bottleneck.

**Recommendation:** P2 — add a total timeout budget for page assembly (e.g., 60s for run page, 45s for project page) using `asyncio.wait_for` or a deadline primitive. This bounds worst-case latency without changing normal-case behavior.

### 9.7 Validation/mapping pipeline simplification

**Finding:** The current pipeline (fetch → validate upstream → map to owned) is correct and necessary. No redundant validation was found.

**Recommendation:** Do not simplify validation. The double validation (upstream model + owned model) is intentional and provides defense in depth.

### 9.8 Avoiding unnecessarily large upstream payloads

**Finding:** The upstream endpoints (`/runs/{id}/files`, `/runs/{id}/specifications`, `/runs/{id}/logs`) return the full resource. There is no known query parameter to reduce payload size. This is a limitation of the upstream API, not the platform implementation.

**Recommendation:** If the upstream API later supports field selection or smaller representations, the platform can adopt them. Not actionable now.

### 9.9 Response serialization

**Finding:** FastAPI's default JSON serialization (via Pydantic's `model_dump_json`) is used. The payloads are small. Serialization overhead is negligible.

**Recommendation:** Do not optimize serialization without measurement showing it's a bottleneck.

### 9.10 Caching

**Finding:** Caching is not recommended as a primary optimization. The page endpoints aggregate multiple upstream resources with different 404/5xx semantics. Caching would complicate error semantics and freshness guarantees. If upstream data is frequently requested for the same IDs, a cache could help — but this requires product-level decisions about freshness, invalidation, and user-specificity.

**Recommendation:** P3 — only consider after the critical-path optimization is implemented and measured. Caching is a separate product decision, not a performance tweak.

### 9.11 Retries

**Finding:** Retries are not recommended. They change failure semantics (latency, upstream load, timeout behavior) and the current contract does not include retry behavior.

**Recommendation:** Do not add retries to the primary roadmap.

### 9.12 What about the ` Kisao_data` lookup in `_coerce_algorithm`?

**Finding:** `backend/biosim_server/pages/mapping.py:18-33` — `_coerce_algorithm` looks up KiSAO terms from `KISAO_TERMS` to enrich algorithm names and URLs. This is a dictionary lookup (O(1)) and is negligible.

**Recommendation:** No change needed.

## 10. Recommended Target Architecture

### Current architecture (ASCII)

```text
┌─────────────────────────────────────────────────────────────┐
│  GET /projects/{project_id}/page                            │
│  GET /runs/{run_id}/page                                   │
└──────────┬──────────────────────────────────┬───────────────┘
           │                                  │
           ▼                                  ▼
┌─────────────────────┐            ┌─────────────────────┐
│ get_project_page()  │            │ get_run_page()      │
│ (router)            │            │ (router)            │
└─────────┬───────────┘            └─────────┬───────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌─────────────────────┐
│ assemble_project_   │            │ assemble_run_page() │
│ page(client, id)    │            │ (client, run_id)    │
│                     │            │                     │
│ 1. fetch identity   │            │ 1. fetch identity   │
│    (BLOCKS)         │            │    (BLOCKS)         │
│ 2. parse identity   │            │ 2. parse identity   │
│ 3. gather satellites│            │ 3. gather satellites│
│    using run_id     │            │    using run_id     │
│    from identity    │            │    from route param │
│ 4. map page         │            │ 3b. map page        │
└─────────────────────┘            └─────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌─────────────────────┐
│ _satellite()        │            │ _satellite()        │
│ (shared helper)     │            │ (shared helper)     │
│ 404 → empty         │            │ 404 → empty         │
│ 5xx → raise         │            │ 5xx → raise         │
└─────────────────────┘            └─────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌─────────────────────┐
│ map_project_page()  │            │ map_run_page()      │
│ (mapping)           │            │ (mapping)           │
│ validate + project  │            │ validate + project  │
└─────────────────────┘            └─────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌─────────────────────┐
│ ProjectsPagePayload │            │ RunsPagePayload     │
│ (extra="forbid")    │            │ (extra="forbid")    │
└─────────────────────┘            └─────────────────────┘
```

### Recommended architecture (ASCII)

```text
┌─────────────────────────────────────────────────────────────┐
│  GET /projects/{project_id}/page                            │
│  GET /runs/{run_id}/page                                   │
└──────────┬──────────────────────────────────┬───────────────┘
           │                                  │
           ▼                                  ▼
┌─────────────────────┐            ┌──────────────────────────┐
│ get_project_page()  │            │ get_run_page()           │
│ (router)            │            │ (router)                 │
└─────────┬───────────┘            └─────────┬────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌──────────────────────────┐
│ assemble_project_   │            │ assemble_run_page()      │
│ page(client, id)    │            │ (client, run_id)         │
│                     │            │                          │
│ 1. fetch identity   │            │ 1. START IDENTITY +      │
│    (serial, needed  │            │    SATELLITES CONCURRENTLY│
│     for run_id)     │            │    (satellites use        │
│ 2. parse identity   │            │     run_id directly)      │
│ 3. gather satellites│            │ 2. await identity         │
│    using run_id     │            │ 3. parse identity         │
│    from identity    │            │ 4. await satellites       │
│ 4. map page         │            │ 5. map page               │
└─────────────────────┘            └──────────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌──────────────────────────┐
│ _satellite()        │            │ _satellite()             │
│ (shared helper)     │            │ (shared helper)          │
│ 404 → empty         │            │ 404 → empty              │
│ 5xx → raise         │            │ 5xx → raise              │
│ (unchanged)         │            │ (unchanged)              │
└─────────────────────┘            └──────────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌──────────────────────────┐
│ map_project_page()  │            │ map_run_page()           │
│ (mapping)           │            │ (mapping)                │
│ (unchanged)         │            │ (unchanged)              │
└─────────────────────┘            └──────────────────────────┘
          │                                  │
          ▼                                  ▼
┌─────────────────────┐            ┌──────────────────────────┐
│ ProjectsPagePayload │            │ RunsPagePayload          │
│ (extra="forbid")    │            │ (extra="forbid")         │
└─────────────────────┘            └──────────────────────────┘
```

**Key change:** Only the run-page flow changes. The project-page flow is unchanged. The `_satellite` helper, mapping functions, models, error handling, and security checks are all unchanged.

## 11. Prioritized Implementation Roadmap

### Phase 0 — Baseline and regression harness (P0)

**Objective:** Establish a measurable baseline and a regression test suite before making changes.

**Evidence/problem:** No concurrency or latency tests exist for the page endpoints. Changes to the critical path need a baseline to measure against and tests to prove behavior is preserved.

**Files/symbols affected:**
- New: benchmark or test utility for simulating upstream latency
- Existing: `backend/tests/pages/` (add new tests)

**Specific proposed change:**
1. Create a deterministic test that simulates controlled upstream latencies and proves the current critical path for both endpoints.
2. For the run page: prove that satellites currently start AFTER identity completes.
3. For the project page: prove that satellites currently start AFTER identity completes (and that they need the run ID from identity).

**Why this approach:** Without a baseline, you cannot prove the optimization worked. The tests also serve as regression guards.

**Behavioral invariants:** None — this phase adds tests, not changes to production code.

**Security invariants:** N/A

**Tests to add:**
- `test_run_page_concurrency_current` — proves satellites start after identity in the current implementation
- `test_project_page_concurrency_current` — proves satellites start after identity and need the run ID from identity
- Use `asyncio.Event` or `MockTransport` with controlled delays to synchronize and measure

**Benchmark to run:**
- If MockTransport is available, simulate: identity = 100ms, files = 100ms, specifications = 100ms, logs = 100ms
- Measure total page latency for both endpoints
- Record baseline numbers

**Expected benefit:** Baseline established; regression tests in place.

**Risk:** None — tests only.

**Rollback:** N/A

**Dependencies:** None

**Definition of done:**
- Baseline latency numbers recorded
- Tests prove current critical path behavior
- All existing tests still pass

---

### Phase 1 — Run page: start satellites concurrently with identity (P1)

**Objective:** Eliminate the unnecessary serial identity barrier for the run page.

**Evidence/problem:** CODE-PROVEN — `assemble_run_page` in `backend/biosim_server/pages/service.py:46-56` awaits identity before creating satellite tasks, but satellites only need `run_id` (the route parameter).

**Files/symbols affected:**
- `backend/biosim_server/pages/service.py` — `assemble_run_page`
- `backend/biosim_server/pages/__init__.py` — if re-exporting changes
- `backend/tests/pages/test_run_page.py` — add concurrency test
- `backend/tests/pages/test_mapping.py` — no changes needed (mapping is unchanged)

**Specific proposed change:**

Rewrite `assemble_run_page` to start satellites concurrently with the identity fetch:

```python
async def assemble_run_page(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    # Start satellites immediately — they only need run_id, not the identity response.
    identity_task = fetch_upstream_json(
        client, upstream_url("runs", run_id, "summary"), resource="run summary",
    )
    satellite_tasks = [
        _satellite(client, "files", run_id),
        _satellite(client, "specifications", run_id),
        _satellite(client, "logs", run_id),
    ]
    
    # Wait for identity first (we need it for info/summary).
    try:
        payload = await identity_task
    except HTTPException:
        # Identity failed — cancel satellites and propagate the error.
        for task in satellite_tasks:
            task.cancel()
        raise
    
    try:
        run = parse_run(payload)
        files, specifications, logs = await asyncio.gather(*satellite_tasks)
        return map_run_page(run, files, specifications, logs)
    except ValidationError as exc:
        logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc
    except HTTPException:
        raise
    except Exception:
        # Satellite failure — fail the page.
        raise HTTPException(502, "The upstream service failed while loading the run page.")
```

**Key design decisions:**
1. **Identity is awaited first** — we need it for `info` and `summary`. But satellites are already in flight.
2. **If identity fails (404/5xx/timeout), satellites are cancelled** — this prevents wasted upstream work. Cancellation is done via `task.cancel()`.
3. **If a satellite fails, the page fails** — same as current behavior. `asyncio.gather` propagates the first exception.
4. **If identity succeeds but a satellite fails, the page fails with 502** — same as current behavior.
5. **404 from identity → 404 response** — satellites will also return 404, but we already have the 404 from identity, so we return it. This is the same observable behavior as today (the page returns 404 when the run doesn't exist).

**Alternative design (speculative parallelism):**
Start satellites BEFORE checking identity at all. If identity returns 404, cancel satellites and return 404. If identity returns 5xx, cancel satellites and return 502. This would be marginally faster (satellites start slightly earlier) but more complex and risks unnecessary upstream calls for non-existent runs.

**Why the recommended approach is preferred:**
- It preserves the error semantics exactly: identity failure → page failure, with satellites cancelled.
- It doesn't issue upstream requests for runs that don't exist (identity 404 is detected before satellites complete, and they're cancelled).
- It's simpler than full speculative parallelism.
- The latency improvement is nearly the same: in both cases, satellites overlap with identity.

**Behavioral invariants:**
- `info` and `summary` are constructed from the identity response (unchanged)
- `files`, `specifications`, `logs` come from satellite responses (unchanged)
- 404 from identity → 404 response (unchanged)
- 5xx/timeout from identity → 502/504 response (unchanged)
- Satellite 404 → empty array/null (unchanged)
- Satellite 5xx → page fails with 502 (unchanged)
- Response shape is identical (unchanged)

**Security invariants:**
- No caller credentials forwarded (unchanged — uses same `client`)
- No upstream body/host leakage (unchanged — uses same `fetch_upstream_json`)
- Path security unchanged (unchanged — uses same `upstream_url`)
- Dot-only ID rejection unchanged (unchanged — uses same `upstream_url`)

**Tests to add/change:**
- `test_run_page_satellites_overlap_with_identity` — use controlled delays to prove satellites start before identity completes
- `test_run_page_identity_404_cancels_satellites` — prove that when identity returns 404, satellites are cancelled and no additional upstream work is done
- `test_run_page_identity_5xx_fails_page` — prove that when identity returns 5xx, the page returns 502
- `test_run_page_satellite_5xx_fails_page` — existing behavior, add a test if missing
- `test_run_page_concurrency_after_fix` — prove the new critical path: total latency ≈ max(identity, satellites)

**Benchmark to run:**
- Same simulation as Phase 0, but with the fix applied
- Expected: run page latency ≈ max(identity, satellites) instead of identity + max(satellites)
- Record before/after numbers

**Expected benefit:**
- Run page latency reduced by up to one full RTT (the identity call)
- In the common case (similar latency for all calls): ~33-50% reduction for run page
- No change to project page latency

**Risk:**
- Cancellation of in-flight satellites may not fully cancel the HTTP request if the timeout is long. httpx cancels the wait but the server may still process the request. This is acceptable — the response is discarded.
- If `task.cancel()` is called on a task that has already completed, it's a no-op. Safe.
- If identity and satellites all fail simultaneously, the error that surfaces is non-deterministic. This is acceptable — all failures produce 502, and the specific error message is not a contract guarantee.

**Rollback:**
- Revert `assemble_run_page` to the current implementation
- All existing tests should still pass

**Dependencies:**
- Phase 0 (baseline tests) should be done first to measure impact

**Definition of done:**
- `assemble_run_page` starts satellites concurrently with identity
- Tests prove concurrency behavior
- Tests prove error semantics are preserved
- Benchmark shows latency improvement
- All existing tests pass

---

### Phase 2 — Project page: minor cleanup (P2)

**Objective:** Clean up minor design issues in the project page path without changing behavior.

**Evidence/problem:** The `_satellite` helper parameter is named `run_id` but is used with a project-derived run ID. Minor clarity issue.

**Files/symbols affected:**
- `backend/biosim_server/pages/service.py` — `_satellite` parameter rename
- No behavior change

**Specific proposed change:**
Rename `_satellite(client, resource, run_id)` to `_satellite(client, resource, resource_id)` to accurately reflect that the ID is not always a run ID.

**Why this approach:** Clarity improvement only. No performance impact.

**Behavioral invariants:** Unchanged.

**Security invariants:** Unchanged.

**Tests to add/change:** None needed — this is a rename only.

**Benchmark to run:** None.

**Expected benefit:** Improved code clarity.

**Risk:** None — rename only.

**Rollback:** Rename back.

**Dependencies:** None.

**Definition of done:** Parameter renamed; all tests pass.

---

### Phase 3 — Total timeout budget for page aggregation (P2)

**Objective:** Add a total timeout budget for page assembly to bound worst-case latency.

**Evidence/problem:** Each upstream call has a 30s timeout, but there is no total budget for the aggregation. In the worst case, a run page could take 4 × 30s = 120s (if all calls are serial and hit the timeout). With the Phase 1 fix, the worst case is 30s (all concurrent), but a total budget is still good practice.

**Files/symbols affected:**
- `backend/biosim_server/pages/service.py` — `assemble_project_page`, `assemble_run_page`
- Potentially `backend/biosim_server/common/upstream.py` — if a reusable timeout helper is warranted

**Specific proposed change:**
Wrap the page assembly in `asyncio.wait_for` with a total timeout:
- Run page: 60s total (enough for 2 × 30s serial calls, but with concurrent calls it's ~30s)
- Project page: 45s total (identity + 2 satellites, serial identity)

```python
async def assemble_run_page(client, run_id):
    try:
        return await asyncio.wait_for(_assemble_run_page_inner(client, run_id), timeout=60.0)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timed out while loading the run page.")
```

**Why this approach:** `asyncio.wait_for` cancels the inner task on timeout, which cancels all in-flight satellite requests. This provides a clean total timeout.

**Behavioral invariants:**
- Normal-case behavior unchanged (timeout is much larger than typical latency)
- Worst-case behavior improved (bounded at 60s instead of unbounded)

**Security invariants:** Unchanged.

**Tests to add/change:**
- `test_run_page_total_timeout` — simulate a slow identity that exceeds 60s total; verify 504
- `test_project_page_total_timeout` — same for project page

**Benchmark to run:** None — this is a safety improvement, not a performance one.

**Expected benefit:** Bounded worst-case latency. No normal-case impact.

**Risk:** If the timeout is too aggressive, it could cause false timeouts under high load. 60s is generous and should not trigger under normal conditions.

**Rollback:** Remove the `wait_for` wrapper.

**Dependencies:** None. Can be done independently of Phase 1.

**Definition of done:** Total timeout added; tests prove timeout behavior; all existing tests pass.

---

### Phase 4 — Error handling refinement for concurrent satellites (P2)

**Objective:** Ensure that when multiple satellites fail concurrently, the error handling is clean and deterministic.

**Evidence/problem:** With `asyncio.gather`, if multiple satellites fail, the first exception wins. The other tasks continue running in the background. This is acceptable today (all failures → 502), but with the Phase 1 change (satellites running concurrently with identity), it's worth ensuring clean cancellation.

**Files/symbols affected:**
- `backend/biosim_server/pages/service.py` — `assemble_run_page` (if not already using TaskGroup)
- Potentially `backend/biosim_server/pages/service.py` — `_satellite` error handling

**Specific proposed change:**
After the Phase 1 change, evaluate whether to use `asyncio.TaskGroup` (Python 3.11+) for cleaner structured concurrency:

```python
async with asyncio.TaskGroup() as tg:
    identity_task = tg.create_task(fetch_upstream_json(...))
    files_task = tg.create_task(_satellite(client, "files", run_id))
    specs_task = tg.create_task(_satellite(client, "specifications", run_id))
    logs_task = tg.create_task(_satellite(client, "logs", run_id))

    payload = identity_task.result()
    run = parse_run(payload)
    files = files_task.result()
    specifications = specs_task.result()
    logs = logs_task.result()
    return map_run_page(run, files, specifications, logs)
```

**Why this approach:** `TaskGroup` automatically cancels all remaining tasks if one fails. This is cleaner than manual `task.cancel()` loops.

**Behavioral invariants:** Same as Phase 1.

**Security invariants:** Unchanged.

**Tests to add/change:** Same as Phase 1 concurrency tests.

**Benchmark to run:** Same as Phase 1.

**Expected benefit:** Cleaner cancellation semantics. Marginal performance impact (TaskGroup is not faster than gather).

**Risk:** `asyncio.TaskGroup` is available in Python 3.11+. The project uses Python 3.13 (per `pyproject.toml:10`), so it's available. However, `asyncio.TaskGroup` has different exception handling semantics than `gather` — if multiple tasks fail, `TaskGroup` raises the first exception and cancels the rest, but the `result()` calls on cancelled tasks raise `CancelledError`. The error handling needs to be adjusted.

**Decision:** This is optional. The Phase 1 implementation with `gather` + manual cancellation is sufficient. TaskGroup can be adopted if it simplifies the code.

**Rollback:** Revert to `gather`.

**Dependencies:** Phase 1.

**Definition of done:** Error handling is clean; all tests pass.

---

### Phase 5 — Performance regression tests (P2)

**Objective:** Add deterministic performance regression tests that would catch accidental re-introduction of the serial identity barrier.

**Evidence/problem:** Without regression tests, a future refactor could accidentally reintroduce the serial dependency.

**Files/symbols affected:**
- `backend/tests/pages/test_run_page.py` — add regression test
- `backend/tests/pages/test_project_page.py` — add regression test (for correctness, not performance)

**Specific proposed change:**
Add a test that uses `asyncio.Event` to prove that satellite tasks are created before the identity response is processed:

```python
async def test_run_page_satellites_start_before_identity_completes():
    """Prove that satellites are created before identity completes."""
    events = {"satellite_created": asyncio.Event(), "identity_completed": asyncio.Event()}
    
    call_order = []
    
    async def delayed_fetch_upstream_json(client, path, *, resource):
        call_order.append(resource)
        if resource == "run summary":
            await events["satellite_created"].wait()  # Delay identity until satellites are created
        return _fake_response(resource)
    
    # ... set up dependency override with delayed_fetch_upstream_json ...
    
    response = await client.get(f"/runs/{RUN_ID}/page")
    
    assert response.status_code == 200
    assert "files" in call_order  # files was called before identity completed
    assert "run summary" in call_order
    assert call_order.index("files") < call_order.index("run summary")  # satellites started first
```

**Why this approach:** Using `asyncio.Event` to synchronize the identity response with satellite creation proves the ordering. This is deterministic and doesn't depend on wall-clock timing.

**Behavioral invariants:** The test proves behavior, it doesn't change it.

**Security invariants:** N/A

**Tests to add/change:** One regression test for the run page.

**Benchmark to run:** N/A — this is a behavioral test, not a benchmark.

**Expected benefit:** Prevents regression of the concurrency optimization.

**Risk:** None — test only.

**Rollback:** Remove the test.

**Dependencies:** Phase 1.

**Definition of done:** Regression test proves satellite/identity ordering; test passes.

---

### Phase 6 — Full contract/security validation (P1)

**Objective:** Run the full test suite and verify all contracts and security invariants are preserved.

**Evidence/problem:** After making changes, the full contract and security surface must be re-validated.

**Files/symbols affected:** All existing tests.

**Specific proposed change:**
Run the full backend test suite:
- `uv run pytest tests/pages/` — page tests
- `uv run pytest tests/summaries/` — summary tests
- `uv run pytest tests/common/test_upstream.py` — upstream helper tests
- `uv run pytest tests/common/test_summary_live.py` — live contract tests (if network available)
- `uv run ruff check .` — linting
- `uv run mypy biosim_server tests` — type checking

**Behavioral invariants:** All existing tests must pass. No new failures.

**Security invariants:** All security tests must pass.

**Tests to add/change:** None — run existing tests.

**Benchmark to run:** Compare Phase 0 baseline with post-Phase-1 numbers.

**Expected benefit:** Confidence that changes are correct and complete.

**Risk:** None — validation only.

**Rollback:** Revert changes if tests fail.

**Dependencies:** Phases 1-5.

**Definition of done:** All tests pass; linting and type checking pass; benchmark shows improvement.

---

### Roadmap summary

| Phase | Priority | Description | Files affected | Risk |
|---|---|---|---|---|
| 0 | P0 | Baseline + regression harness | `tests/pages/` (new tests) | None |
| 1 | P1 | Run page: concurrent satellites with identity | `pages/service.py`, `tests/pages/test_run_page.py` | Low |
| 2 | P2 | Project page: `_satellite` parameter rename | `pages/service.py` | None |
| 3 | P2 | Total timeout budget | `pages/service.py` | Low |
| 4 | P2 | Error handling refinement (TaskGroup optional) | `pages/service.py` | Low |
| 5 | P2 | Performance regression tests | `tests/pages/test_run_page.py` | None |
| 6 | P1 | Full contract/security validation | All tests | None |

## 12. Test and Benchmark Plan

### 12.1 Tests to add

#### Run page concurrency tests (Phase 0 + Phase 1)

1. **`test_run_page_satellites_overlap_with_identity`** (Phase 0 — proves current behavior; Phase 1 — proves fix)
   - Use `MockTransport` or dependency override with controlled delays
   - Delay identity response until after satellite requests are issued
   - Prove that satellite requests are made before identity completes
   - Phase 0 expectation: satellites are NOT created before identity (current behavior)
   - Phase 1 expectation: satellites ARE created before identity completes (fixed behavior)

2. **`test_run_page_identity_404`** (Phase 1)
   - Identity returns 404; verify page returns 404
   - Verify satellites are cancelled (no additional upstream calls after identity 404)

3. **`test_run_page_identity_5xx`** (Phase 1)
   - Identity returns 500; verify page returns 502
   - Verify error message is sanitized

4. **`test_run_page_satellite_5xx`** (Phase 1 — if not already covered)
   - One satellite returns 500; verify page returns 502
   - Verify other satellites' responses are discarded

5. **`test_run_page_timeout`** (Phase 1 + Phase 3)
   - Identity times out; verify page returns 504
   - Verify satellites are cancelled

6. **`test_run_page_concurrency_after_fix`** (Phase 1)
   - Simulate: identity = 100ms, files = 100ms, specs = 100ms, logs = 100ms
   - Measure total latency
   - Expected: ~100ms + local overhead (not 200ms + overhead)

#### Project page tests (Phase 0)

7. **`test_project_page_satellites_need_identity_run_id`** (Phase 0)
   - Prove that project page satellites cannot start before identity completes
   - Use `MockTransport` with controlled delays
   - Prove that the satellite requests use the run ID from the identity response, not the project ID from the route parameter

#### Regression tests (Phase 5)

8. **`test_run_page_satellites_start_before_identity_completes`** (Phase 5)
   - Deterministic test using `asyncio.Event` to prove ordering
   - Should fail on current implementation, pass after Phase 1 fix

#### Security tests (Phase 6)

9. **`test_run_page_caller_credentials_not_forwarded`** (Phase 6 — if not already covered by summary tests)
   - Verify `Authorization` and `Cookie` are not forwarded for page endpoints
   - Note: `test_caller_credentials_do_not_reach_upstream` covers summary endpoints; page endpoints should be tested separately

10. **`test_project_page_dot_only_id_rejected`** (Phase 6)
    - `GET /projects/./page` → 404, zero upstream calls
    - `GET /projects/../page` → 404, zero upstream calls

11. **`test_run_page_dot_only_id_rejected`** (Phase 6)
    - `GET /runs/./page` → 404, zero upstream calls
    - `GET /runs/../page` → 404, zero upstream calls

### 12.2 Tests to verify (existing)

The following existing tests should continue to pass after all changes:

| Test | File | What it verifies |
|---|---|---|
| `test_run_fields_and_first_metadata` | `test_mapping.py` | Run page field projection |
| `test_project_fields_and_model_formats` | `test_mapping.py` | Project page field projection |
| `test_required_run_drift` | `test_mapping.py` | Required field validation |
| `test_required_project_drift` | `test_mapping.py` | Required field validation |
| `test_run_page_info_requires_timestamps` | `test_mapping.py` | Timestamp requirements |
| `test_run_page_info_requires_status` | `test_mapping.py` | Status requirement |
| `test_nullable_sizes_and_citation_uris` | `test_mapping.py` | Nullable fields |
| `test_specifications_projection_and_generator_union` | `test_mapping.py` | Spec projection |
| `test_files_projection` | `test_mapping.py` | File projection |
| `test_logs_nested_tree` | `test_mapping.py` | Log tree projection |
| `test_log_algorithm_kisao_string_is_projected` | `test_mapping.py` | KiSAO algorithm enrichment |
| `test_satellite_required_drift` | `test_mapping.py` | Satellite field validation |
| `test_known_empty_metadata_and_nullable_logs` | `test_mapping.py` | Empty metadata + nullable logs |
| `test_unpublished_run_needs_no_project_and_generator_name_is_optional` | `test_mapping.py` | Unpublished run handling |
| `test_optional_title_does_not_fall_back_to_run_name` | `test_mapping.py` | Title fallback behavior |
| `test_live_owned_contract_and_fixture_drift` | `test_summary_live.py` | Live contract validation |
| `test_caller_credentials_do_not_reach_upstream` | `test_summary_live.py` | Credential stripping |
| `test_absent_run_upstream_5xx_becomes_sanitized_502` | `test_summary_live.py` | Error sanitization |
| `test_pooled_client_targets_the_configured_upstream` | `test_summary_live.py` | HTTP client configuration |
| All `test_upstream.py` tests | `test_upstream.py` | Upstream helper behavior |

### 12.3 Benchmark plan

#### Local simulation benchmark (Phase 0 + Phase 1)

Use `httpx.MockTransport` or a custom ASGI transport to simulate upstream latency:

```python
import asyncio
import time
import httpx
from httpx import ASGITransport, AsyncClient

async def benchmark_page_latency():
    # Set up MockTransport that delays each response by a controlled amount
    delays = {
        "/runs/61fea483f499ccf25faafc4d/summary": 0.1,  # 100ms
        "/runs/61fea483f499ccf25faafc4d/files": 0.1,
        "/runs/61fea483f499ccf25faafc4d/specifications": 0.1,
        "/runs/61fea483f499ccf25faafc4d/logs": 0.1,
    }
    
    mock = httpx.MockTransport(...)
    client = httpx.AsyncClient(transport=mock, base_url="http://platform.test")
    
    start = time.monotonic()
    response = await client.get("/runs/61fea483f499ccf25faafc4d/page")
    elapsed = time.monotonic() - start
    
    print(f"Run page latency: {elapsed*1000:.1f}ms")
    # Expected before fix: ~200ms + overhead
    # Expected after fix: ~100ms + overhead
```

#### Live measurement (Phase 6 — optional)

If the live upstream is accessible and safe:
- Make 5-10 requests to `GET /runs/{known_run_id}/page` and measure latency
- Make 5-10 requests to `GET /projects/{known_project_id}/page` and measure latency
- Record min/avg/max
- Do NOT load test aggressively

**Classification:** HIGH-CONFIDENCE INFERENCE for the simulation; NOT VERIFIED for live measurements (depends on upstream accessibility).

## 13. Acceptance Criteria

### Functional acceptance criteria

1. **Run page latency:** After Phase 1, the run page critical path is `max(identity, satellites)` not `identity + max(satellites)`. Proven by deterministic concurrency test.

2. **Run page error semantics preserved:**
   - Identity 404 → page returns 404
   - Identity 5xx → page returns 502
   - Identity timeout → page returns 504
   - Satellite 404 → empty array/null in response
   - Satellite 5xx → page returns 502
   - Satellite timeout → page returns 504

3. **Project page unchanged:** No behavioral change to the project page endpoint.

4. **All existing tests pass:** `tests/pages/`, `tests/summaries/`, `tests/common/test_upstream.py`, `tests/common/test_summary_live.py` (if network available).

5. **Security invariants preserved:**
   - No `Authorization`/`Cookie` forwarding
   - No upstream body/host leakage
   - Dot-only IDs rejected
   - Encoded slashes rejected by router
   - Path segments quoted

6. **OpenAPI schemas unchanged:** `ProjectsPagePayload` and `RunsPagePayload` remain the response models with the same field structure.

### Performance acceptance criteria

1. **Run page latency reduction:** Measured reduction in the simulated benchmark consistent with eliminating one RTT from the critical path. Expected: ~33-50% reduction when identity and satellites have similar latency.

2. **No regression:** Project page latency is unchanged. Run page latency does not increase.

3. **Baseline captured:** Phase 0 establishes before/after numbers.

### Code quality acceptance criteria

1. **Linting passes:** `uv run ruff check .`

2. **Type checking passes:** `uv run mypy biosim_server tests`

3. **No new warnings:** No new mypy warnings, no new ruff violations.

## 14. Risks and Rollback Strategy

### Risk: Cancellation may not fully cancel HTTP requests

**Description:** When `task.cancel()` is called on an in-flight httpx request, the asyncio task is cancelled but the underlying HTTP connection may still be in use. httpx's `AsyncClient` uses a connection pool, so cancelled requests may leave connections in the pool.

**Impact:** Low. Cancelled connections are returned to the pool and reused. The upstream server may process the request, but the response is discarded. This is acceptable.

**Mitigation:** Use `asyncio.wait_for` with a total timeout (Phase 3) to bound the worst case. Consider using `httpx.AsyncClient` with a lower connection pool limit if contention is observed.

**Rollback:** Revert to the current `asyncio.gather` implementation.

### Risk: Non-deterministic error ordering with concurrent failures

**Description:** When identity and satellites fail concurrently, the error that surfaces depends on which task completes first. This could change which error message is returned.

**Impact:** Low. All failures produce 502 with a sanitized message. The specific message is not a contract guarantee. The `detail` field varies ("The upstream service failed while loading the run summary." vs "The upstream service returned an unexpected run page."), but both are 502 sanitized errors.

**Mitigation:** Ensure the error message is consistent regardless of which task fails first. The current implementation already handles this — the identity failure produces a specific message, and the satellite failure produces a different message. With concurrent failures, the first to complete wins. This is acceptable.

**Rollback:** Document the non-determinism as acceptable. No code change needed.

### Risk: Regression of project page behavior

**Description:** Changes to `assemble_run_page` could accidentally affect `assemble_project_page` if the two functions are refactored together.

**Impact:** Medium. The project page is a separate endpoint with different semantics.

**Mitigation:** Keep the two functions separate. Do not unify them. Test both endpoints independently.

**Rollback:** Revert changes to `assemble_project_page` if any unintended changes are made.

### Risk: Test fragility

**Description:** Concurrency tests that rely on timing or event ordering can be flaky.

**Impact:** Medium. Flaky tests erode confidence.

**Mitigation:** Use deterministic synchronization primitives (`asyncio.Event`, `MockTransport` with controlled delays) rather than wall-clock timing. Avoid assertions like "latency < Xms" — use structural assertions like "satellite requests were issued before identity completed."

**Rollback:** Rewrite flaky tests to use deterministic synchronization.

### Rollback strategy summary

| Change | Rollback |
|---|---|
| Phase 1: `assemble_run_page` concurrency | Revert to current `asyncio.gather` implementation |
| Phase 2: parameter rename | Rename back |
| Phase 3: total timeout | Remove `wait_for` wrapper |
| Phase 4: TaskGroup | Revert to `gather` |
| Phase 5: regression tests | Remove tests |
| Phase 6: validation | N/A — read-only |

## 15. Out-of-Scope / Behavior-Changing Ideas

The following ideas are explicitly out of scope for this optimization plan. They may be useful in the future but require broader product decisions or change observable behavior.

### 15.1 Caching

**Why out of scope:** Caching would change freshness semantics, require invalidation strategy, and complicate the 404/5xx error model. The page endpoints aggregate multiple resources with different absence/failure semantics. A cache would need to handle per-resource TTL, per-resource invalidation, and cache-behavior during upstream outages. This is a product-level decision, not a performance tweak.

**When to reconsider:** If production traffic shows repeated requests for the same project/run IDs within seconds, and the upstream latency is consistently high, caching could be evaluated. But first consider whether the upstream calls are necessary at all (they are — the page endpoint is a thin aggregator).

### 15.2 Retries

**Why out of scope:** Retries change latency, upstream load, and failure semantics. The current contract does not include retry behavior. Adding retries would change the observable behavior of the endpoint (e.g., a 502 might become a 200 after a retry succeeds, or a timeout might be extended).

**When to reconsider:** If upstream reliability is a documented problem and the product team agrees on retry semantics (max retries, backoff, which errors to retry), retries could be added. But this is a separate feature, not an optimization.

### 15.3 Response compression

**Why out of scope:** The payloads are small (a few KB). Compression would add CPU overhead for minimal bandwidth savings. The current responses are already small.

**When to reconsider:** If payloads grow significantly (e.g., large specifications with many outputs), compression could be evaluated. Not now.

### 15.4 HTTP/2

**Why out of scope:** HTTP/2 multiplexing could theoretically improve connection reuse, but the current upstream is HTTP/1.1 and the connection pool is adequate for current traffic. Enabling HTTP/2 would require verifying upstream support and testing the impact.

**When to reconsider:** If connection pool contention is observed in production, HTTP/2 could be evaluated. Not now.

### 15.5 Increasing connection pool size

**Why out of scope:** The default pool (10 connections/host) is adequate for current traffic patterns. Increasing it without evidence of contention is premature.

**When to reconsider:** If production metrics show pool exhaustion (connection timeouts, queueing), increase the pool size. Not now.

### 15.6 New API response fields

**Why out of scope:** Adding fields would change the public contract. The current contracts are owned and validated. Any field addition requires a contract decision.

**When to reconsider:** If the frontend or API consumers request additional fields, evaluate and add them through the normal contract process. Not now.

### 15.7 Frontend migration

**Why out of scope:** Issue #108 historically contains a frontend migration checkbox, but it is explicitly out of scope for this task. The frontend remains out of scope.

### 15.8 Upstream API redesign

**Why out of scope:** The upstream API is not under the platform's control. Any redesign would require coordination with the upstream team.

**When to reconsider:** If the upstream API adds field selection, smaller representations, or dedicated page endpoints, the platform can adopt them. Not now.

### 15.9 Persistent cache infrastructure (Redis, etc.)

**Why out of scope:** Adding Redis or any persistent cache is a significant infrastructure change that requires operational support, monitoring, and invalidation strategy. It's not justified by the current performance profile.

**When to reconsider:** If the platform scales to many replicas and cache invalidation becomes manageable, persistent caching could be evaluated. Not now.

### 15.10 Cross-request deduplication

**Why out of scope:** Deduplicating concurrent requests for the same resource across different incoming requests would require a shared cache or request coalescing mechanism. This changes the request model and adds complexity.

**When to reconsider:** If production traffic shows many concurrent requests for the same project/run ID, request coalescing could be evaluated. Not now.

## 16. Open Questions and Remaining Measurements

### 16.1 What is the actual upstream latency distribution?

**Status:** NOT VERIFIED

**What was unavailable:** Live upstream access was not used during this audit. The `test_summary_live.py` tests are marked `integration` and require network access to `api.biosimulations.org`.

**What evidence would verify:** Run `test_live_owned_contract_and_fixture_drift` and measure the actual latency of identity and satellite calls.

**Does it materially affect the plan:** The optimization (Phase 1) is structurally correct regardless of actual latency. The magnitude of the benefit depends on the relative latency of identity vs satellites. If identity is always faster than satellites, the benefit is smaller. If identity is slower, the benefit is larger.

### 16.2 Is the connection pool adequate for projected traffic?

**Status:** NOT VERIFIED

**What was unavailable:** Production traffic metrics are not available.

**What evidence would verify:** Production metrics showing concurrent upstream requests per pod, connection pool utilization, and connection timeout rates.

**Does it materially affect the plan:** The Phase 1 optimization increases peak concurrent upstream requests per run-page request from 1 (serial identity) to 4 (identity + 3 satellites in flight). With the default pool of 10 connections/host, this is still adequate for 2-3 simultaneous requests. If production traffic exceeds this, pool tuning may be needed.

### 16.3 Does the live upstream return the same run ID in the summary as the route parameter?

**Status:** CODE-PROVEN for the implementation (satellites use `run_id` directly), but NOT VERIFIED against live upstream behavior.

**What was unavailable:** Live upstream verification was not performed.

**What evidence would verify:** A live test that calls `GET /runs/{run_id}/summary` and verifies the `id` field matches `run_id`, and that `GET /runs/{run_id}/files` returns the files for that run.

**Does it materially affect the plan:** The implementation already uses `run_id` directly for satellites. If the upstream ever changed to return a different canonical ID, the satellites would break. But this is an upstream contract issue, not a platform issue. The platform's assumption (route parameter = upstream resource identifier) is standard for REST APIs.

### 16.4 Are there any edge cases where `project.simulation_run.id` differs from the run ID used in satellite URLs?

**Status:** NOT VERIFIED against live upstream

**What was unavailable:** Live upstream verification.

**What evidence would verify:** A live test that fetches a project summary, extracts `simulationRun.id`, and verifies that `GET /runs/{simulationRun.id}/files` returns the same files as the project page expects.

**Does it materially affect the plan:** This is the project page's existing behavior. The optimization plan does not change it. If there's an upstream inconsistency, it's a pre-existing issue, not introduced by this plan.

### 16.5 What is the actual local overhead (validation + mapping + serialization)?

**Status:** NOT VERIFIED — measured inference only

**What was unavailable:** No profiling or benchmarking was performed.

**What evidence would verify:** A microbenchmark that measures `parse_run` + `map_run_page` + serialization time for a representative payload.

**Does it materially affect the plan:** Local overhead is expected to be <5ms, which is negligible compared to network latency (50-200ms per call). Even if local overhead is 10ms, it's still small relative to the network RTT. The optimization focus is on the network path, not local processing.

### 16.6 Should the `_satellite` helper be extended to handle cancellation explicitly?

**Status:** OPEN QUESTION

**Current behavior:** `_satellite` catches `HTTPException` with status 404 and returns empty. All other exceptions propagate.

**Question:** When satellites are cancelled (via `task.cancel()`), `asyncio.CancelledError` is raised inside the task. Should `_satellite` catch `CancelledError` and return a sentinel, or should it let it propagate?

**Recommendation:** Let `CancelledError` propagate. The cancellation is initiated by the page assembler when identity fails. The satellite task is cancelled, and the `CancelledError` propagates to the `gather` call. The page assembler catches the first exception (identity failure) and returns the appropriate error. The `CancelledError` from satellites is subsumed.

**Impact:** If `CancelledError` is not caught, `asyncio.gather` will raise it as the first exception (if it arrives before the identity exception). This could change which error is surfaced. To avoid this, cancel satellites AFTER the identity failure is detected, not before.

**Refinement for Phase 1 implementation:**
```python
try:
    payload = await identity_task
except HTTPException:
    # Cancel satellites and wait for them to finish cancelling.
    for task in satellite_tasks:
        task.cancel()
    # Wait for cancellation to complete (or raise if a satellite already failed).
    await asyncio.gather(*satellite_tasks, return_exceptions=True)
    raise
```

This ensures that satellites are cancelled and their `CancelledError` is suppressed before re-raising the identity error.

## 17. Final Recommendation

**Implement Phase 1 (run page concurrent satellites) as the primary optimization.** This is the highest-value, lowest-risk change. It eliminates an unnecessary serial dependency that costs one full RTT on every run-page request. The change is localized to `assemble_run_page` in `backend/biosim_server/pages/service.py` and preserves all existing error semantics, security boundaries, and response contracts.

**Follow with Phase 0 (baseline tests) before Phase 1, and Phase 6 (full validation) after Phase 1.** The baseline proves the current behavior and provides a measurement foundation. The validation confirms the change is correct and complete.

**Implement Phase 3 (total timeout budget) as a safety measure, either alongside Phase 1 or independently.** It bounds worst-case latency without affecting normal-case behavior.

**Defer Phases 2, 4, and 5 to after Phase 1 is complete and measured.** They are lower-impact improvements that can be done in a follow-up PR if desired.

**Do not implement caching, retries, pool tuning, HTTP/2, compression, or any other infrastructure change without evidence of a specific problem.** The current architecture is sound, and the primary bottleneck (run page serial identity barrier) is architectural, not infrastructural.

---

*Audit completed: 2026-09-15. Repository HEAD: 23f406b. Branch: chore/api-improvements.*

## 18. Implementation Record

*Recorded 2026-09-15 on `chore/api-improvements`, on top of 23f406b.*

### 18.1 Before/after latency (Phase 0 baseline, Phase 1 benchmark)

The §12.3 simulation: every upstream call delayed 100 ms via `httpx.MockTransport`, assemblers called directly, 10 samples each, arm64 macOS, Python 3.14.7. "Before" is the verbatim 23f406b code replicated in `tests/pages/test_phase0_baseline.py`.

| Assembler | min ms | median ms | max ms |
|---|---|---|---|
| Run page, before (serial) | 204 | 206 | 211 |
| Run page, after (concurrent) | 103 | 105 | 109 |
| Project page, before | 204 | 208 | 209 |
| Project page, after | 204 | 207 | 219 |

Run page median drops 49% — one round trip removed from the critical path. Project page is unchanged, as intended (§5.1).

### 18.2 Phase status

| Phase | Status | Where |
|---|---|---|
| 0 | Done | `tests/pages/test_phase0_baseline.py` — ordering proofs and a lower-bound latency baseline against the 23f406b replicas |
| 1 | Done | `_assemble_run_page_inner` starts identity and satellites together |
| 2 | Done | `_satellite(client, resource, resource_id)` |
| 3 | Done | `assemble_project_page` / `assemble_run_page` wrap assembly in `asyncio.wait_for` with `_PAGE_TIMEOUTS` (45 s / 60 s) |
| 4 | Not adopted (optional) | `gather` plus `_cancel_and_drain` in a `finally` gives the cancellation `TaskGroup` would, without `TaskGroup` wrapping each `HTTPException` in an `ExceptionGroup` that would have to be unwrapped to keep 404/502/504 |
| 5 | Done | `test_run_page_satellites_start_before_identity_completes`, with a bounded wait so a regression fails in about 2 s instead of hanging |
| 6 | Done | `ruff check .`, `mypy biosim_server tests`, and 217 tests across `tests/pages`, `tests/summaries`, `tests/common/test_upstream.py`, `tests/common/test_summary_live.py` (`-m "not integration"`) |

### 18.3 §12.1 test map

| # | Planned | Implemented as |
|---|---|---|
| 1 | satellites overlap with identity | before: `test_phase0_run_page_satellites_wait_for_identity`; after: `test_run_page_satellites_start_before_identity_completes` |
| 2 | identity 404 | `test_run_page_identity_failure_cancels_satellites[404-404]` |
| 3 | identity 5xx | `test_run_page_identity_failure_cancels_satellites[500-502]`, `test_failure_policy[*-identity]` |
| 4 | satellite 5xx | `test_run_page_satellite_failure_cancels_siblings`, `test_failure_policy[*-files/specifications/logs]` |
| 5 | timeout | `test_run_page_identity_failure_cancels_satellites[timeout-504]`, `test_run_page_total_timeout[identity/satellites]`, `test_project_page_total_timeout[identity/satellites]` |
| 6 | concurrency after fix | `test_run_page_critical_path_is_max_not_sum` |
| 7 | project satellites need identity run id | `test_phase0_project_page_satellites_need_identity_run_id`, `test_embedded_run_id_is_encoded` |
| 8 | regression guard | `test_run_page_satellites_start_before_identity_completes` |
| 9 | page credentials not forwarded | `test_success_parallel_requests_and_isolation` (both pages) |
| 10–11 | dot-only ids rejected | `test_dot_segments_and_slashes_rejected` (both pages; `%2E` forms, since an HTTP client normalizes a literal `.` before sending), `test_embedded_dot_run_id_is_rejected` |

Every cancellation test holds the satellites open until cancelled and asserts which ones were cancelled; none relies on instant mocks.

### 18.4 Mutation check

The new tests were run against two broken versions of `service.py`:

- The first Phase 1 implementation (cancelled satellites only when identity raised `HTTPException`): 3 failures — identity drift, satellite failure leaving siblings running, page timeout while identity is in flight.
- The original serial code (23f406b): 17 failures, none hanging.

### 18.5 Corrections to this plan

- **§11 Phase 1, "Why the recommended approach is preferred"** says the design does not issue upstream requests for runs that don't exist. It does: all four requests go out together. On identity failure the in-flight satellites are cancelled, but a request already sent may still be processed upstream (§14).
- **§11 Phase 3** says `wait_for` "cancels all in-flight satellite requests." That holds only because `_assemble_run_page_inner` cancels its tasks in `finally`. Tasks from `create_task` are not cancelled with the coroutine that awaits them, so the first implementation left satellites running after a page timeout, after identity drift, and after a failed satellite.
- **§11 Phase 1 code sketch** calls `task.cancel()` on bare coroutine objects, which have no `cancel`; the satellites must be wrapped in tasks.
- **§14 "Test fragility"**: the two wall-clock tests assert only bounds that serial code cannot meet — `elapsed >= 2 * delay` for the baseline, and `elapsed < 2 * delay` after the fix with a full `delay` of slack.
