# Legacy Runs API Proxy Implementation Plan

## 1. Goal and Scope

Add backend support for the legacy BioSimulations run API currently called directly by the frontend at `https://api.biosimulations.org/`.

The platform API must expose these public paths:

```text
GET    /runs/{runId}
PATCH  /runs/{runId}
DELETE /runs/{runId}
GET    /runs/{runId}/download
GET    /runs/{runId}/summary
GET    /runs/{runId}/validate
GET    /runs/summary
```

The implementation should proxy requests to the same paths on the configured legacy upstream, preserve opaque response bodies where the platform does not own a response contract, and avoid decoding or corrupting downloads.

This is an implementation plan only. No production source code is changed by this document.

## 2. Current Repository Architecture

### Relevant applications/packages

- `backend/` is a Python 3.13 FastAPI application with Temporal workers.
- `frontend/` is a Nuxt 4 SSR application. The current run detail and run-list pages still call `runtimeConfig.public.legacy_api_url` directly:
  - `frontend/app/pages/runs/[id].vue`
  - `frontend/app/pages/simulations/index.vue`
- `kustomize/` owns Kubernetes configuration and per-cluster ConfigMaps.

### Routing architecture

The FastAPI app and router registration are in `backend/biosim_server/api/main.py`.

`backend/biosim_server/simulations/router.py` defines two routers:

- `router = APIRouter(prefix="/simulations", tags=["Simulations"])`
- `run_summary_router = APIRouter(prefix="/runs", tags=["Runs"])`

`run_summary_router` is mounted by `api/main.py` through `app.include_router(run_summary_router)`, via the export in `backend/biosim_server/simulations/__init__.py`.

Existing `/runs` routes are:

- `GET /runs/{run_id}/summary`, implemented by `get_run_summary`
- `GET /runs/{run_id}/page`, implemented by `get_run_page`

There is no existing `GET /runs/{run_id}`, `PATCH /runs/{run_id}`, `DELETE /runs/{run_id}`, download, validate, or collection-summary route. `DELETE /simulations/{processing_id}` is a different platform-owned operation that deletes local Mongo run records and requires `admin` or `publisher` roles.

### Existing HTTP/API clients

There are two relevant outbound-client patterns:

1. `backend/biosim_server/dependencies.py::get_http_client`
   - owns a process-scoped pooled `httpx.AsyncClient`;
   - uses `get_settings().biosimulations_api_base_url.rstrip("/")` as `base_url`;
   - uses `httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS)`, currently 30 seconds per phase;
   - is injectable with `app.dependency_overrides`, which is how page and summary tests install `httpx.MockTransport`.

2. `backend/biosim_server/biosim_runs/biosim_service.py::BiosimServiceRest`
   - uses short-lived `aiohttp.ClientSession` instances;
   - submits and polls simulations and converts upstream run responses into the typed `BiosimSimulationRun` model;
   - currently contains a direct `GET /runs/{id}` call in `get_sim_run`;
   - is workflow/service logic, not a transparent HTTP proxy. It calls `raise_for_status()`, parses JSON, resolves simulator metadata, and discards upstream response headers.

The existing platform-owned upstream helper is `backend/biosim_server/common/upstream.py`:

- `upstream_url(*segments)` independently quotes path segments and rejects decoded `.` and `..`;
- `fetch_upstream_json` and `fetch_upstream_json_value` stream and cap JSON bodies at `UPSTREAM_MAX_RESPONSE_BYTES`;
- upstream 5xx and transport failures are sanitized into platform 502/504 errors;
- caller `Authorization`, `Cookie`, query parameters, and arbitrary headers are intentionally not forwarded.

That helper is appropriate for platform-owned JSON contracts such as `/runs/{run_id}/summary` and `/runs/{run_id}/page`, but it is not a transparent proxy helper: it rejects/rewrites statuses, parses JSON, strips headers, and has no binary response path.

### Configuration

`backend/biosim_server/config.py::Settings` already defines:

```python
biosimulations_api_base_url: str = "https://api.biosimulations.org"
```

It is loaded through Pydantic Settings and `get_settings()` is `@lru_cache`d. `backend/.env.example` documents `BIOSIMULATIONS_API_BASE_URL`, and the current deployment ConfigMaps inherit the default unless overridden.

The existing setting is the exact upstream required by this feature and is already the base URL for `get_http_client`. Do not introduce a second hard-coded URL or a duplicate environment variable unless the operators explicitly need the legacy run API to be configured independently from the simulation-submission API.

The frontend currently has separate runtime variables (`LEGACY_API_URL` and `BIOSIMULATIONS_API_URL`) in `frontend/nuxt.config.ts`; that does not change the backend configuration decision.

### Authentication and authorization

Authentication is Auth0 access-token based:

- `backend/biosim_server/common/auth/auth0.py::get_current_user` validates a bearer access token.
- `backend/biosim_server/common/auth/roles.py` provides role/permission and ownership helpers.
- Existing public page and summary routes do not require platform authentication.
- Existing platform simulation mutation routes have explicit platform authorization.

The current frontend calls legacy `DELETE /runs/{id}` without an `Authorization` header (`frontend/app/pages/simulations/index.vue::confirm_delete`). The inspected repository does not document the legacy API's required auth scheme for the seven requested endpoints, nor does it contain a legacy PATCH request or upstream endpoint schema.

The plan therefore recommends:

- forward an incoming bearer `Authorization` header to the legacy API when present;
- do not forward platform Auth0 credentials as a new service credential;
- do not forward cookies by default;
- do not invent role/ownership checks or require platform auth on a route until the legacy endpoint's intended authorization contract is confirmed;
- resolve the mutation-route auth decision before implementation, because requiring `get_current_user` would change the current unauthenticated delete behavior and a route with no platform auth may become a mutation proxy.

### Error handling

Platform-owned upstream contracts use sanitized `HTTPException` responses:

- upstream 404 is returned as platform 404;
- upstream 5xx becomes 502;
- timeout becomes 504;
- malformed or schema-drifting JSON becomes 502;
- upstream body contents are not returned in error details.

The requested legacy routes are different if the frontend needs legacy-compatible behavior. For a transparent proxy, a received upstream 4xx/5xx response should retain its status, body, and safe end-to-end headers. Network failures and timeouts still need platform-generated 502/504 responses because there is no upstream response to relay.

### Testing conventions

Backend tests use pytest, pytest-asyncio, FastAPI `TestClient`, and async `httpx` clients with `ASGITransport`.

The strongest existing upstream-mocking pattern is in:

- `backend/tests/simulations/test_run_summary.py`
- `backend/tests/pages/test_run_page.py`

Those tests:

- create an `httpx.AsyncClient` with `httpx.MockTransport`;
- override `get_http_client`;
- drive the FastAPI app through a separate `ASGITransport`;
- inspect both the outbound `httpx.Request` and inbound platform response.

`backend/tests/api/test_openapi_endpoints.py` is an OpenAPI-driven meta-contract. Every documented operation must be added to:

- `_CORE_PATHS`;
- `AUTH_MODE`;
- `VALIDATION_RUNNERS` or `VALIDATION_SKIP`;
- the unauthenticated probe mapping or required-auth operation set;
- `OPERATION_IDS` indirectly through the generated OpenAPI.

The committed artifact is `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml`, regenerated with `uv run python -m scripts.generate_openapi`. `backend/tests/simulations/test_run_summary.py` also asserts the existing typed summary schema.

## 3. Existing Patterns to Reuse

### Similar endpoints/proxies

1. **Existing run summary route**
   - `backend/biosim_server/simulations/router.py::get_run_summary`
   - `backend/biosim_server/common/upstream.py::fetch_upstream_json`
   - `backend/biosim_server/summaries/mapping.py::map_run_summary`
   - This is the correct pattern when the platform owns and validates a stable public response contract.

2. **Run page aggregation**
   - `backend/biosim_server/pages/service.py::assemble_run_page`
   - demonstrates concurrent upstream requests, explicit cancellation/draining, timeout budgets, and structured upstream instrumentation.
   - It intentionally does not forward caller credentials.

3. **Shared HTTP-client dependency**
   - `backend/biosim_server/dependencies.py::get_http_client`
   - keeps connection pooling in the application lifecycle and makes tests hermetic through dependency overrides.

4. **Path quoting and dot-segment rejection**
   - `backend/biosim_server/common/upstream.py::upstream_url`
   - should be reused for every `run_id` path construction. Do not concatenate an untrusted path parameter into a URL.

5. **Streaming file helper**
   - `backend/biosim_server/biosim_runs/biosim_service.py::file_sender`
   - shows the repository's async chunked-file iteration style, but it reads local files and is not sufficient for an open upstream HTTP stream. The proxy download should use `httpx.AsyncClient.stream` and a `StreamingResponse` generator that owns the upstream context until iteration completes.

### Why these patterns should be reused

- They already provide the correct FastAPI dependency and lifecycle model.
- `httpx.MockTransport` can verify outbound method, path, query, body, and headers without network access.
- `upstream_url` already prevents path traversal and normalizes encoded path segments consistently.
- The existing JSON helper must not be expanded into a transparent passthrough helper because its parsing, response-size cap, and sanitization semantics are deliberate for platform-owned contracts.

## 4. Proposed Architecture

### Request flow

For missing transparent routes:

```text
frontend caller
  -> FastAPI run route in simulations/router.py
  -> LegacyRunsClient (new, backed by injected get_http_client)
  -> configured biosimulations_api_base_url + safely quoted path
  -> legacy API
  <- status/body/allowlisted end-to-end headers
```

For `GET /runs/{run_id}/summary`, retain the current flow unless the frontend explicitly requires the full legacy payload:

```text
route -> get_http_client -> fetch_upstream_json -> map_run_summary -> RunSummary
```

That route is currently a platform-owned contract, not a transparent proxy. Changing it to opaque passthrough would be a public API contract change and would invalidate its current mapping/OpenAPI tests.

### Component responsibilities

- `backend/biosim_server/simulations/router.py`
  - remains the public registration point for `/runs` because `run_summary_router` already owns the prefix;
  - adds the missing route handlers alongside `get_run_summary`/`get_run_page`;
  - handles FastAPI request extraction and response construction, not upstream URL concatenation or retry policy.

- New `backend/biosim_server/biosim_runs/legacy_api.py` (recommended)
  - defines a small `LegacyRunsClient` or focused functions for request construction;
  - uses the injected `httpx.AsyncClient`;
  - builds paths with `upstream_url`;
  - applies an explicit request-header allowlist;
  - exposes one buffered passthrough method for JSON/metadata/mutations and one streaming method for downloads;
  - contains the common mapping of `httpx.Response` headers to Starlette response headers.

- `backend/biosim_server/dependencies.py`
  - likely needs no new global client; `LegacyRunsClient` can be constructed from `get_http_client` in a FastAPI dependency function;
  - if a class dependency is used, keep it request-light and reuse the existing pooled client rather than creating a new `AsyncClient` per request.

- `backend/biosim_server/common/upstream.py`
  - continue to own safe path quoting and the shared timeout constant;
  - do not change `fetch_upstream_json` semantics for the current summary/page contracts.

### Legacy API client/proxy strategy

Use `httpx`, not `BiosimServiceRest`:

- the existing pooled client already points at the required base URL;
- `httpx` supports both buffered responses and `stream()` for downloads;
- the route needs to preserve response status and headers, which the typed `BiosimServiceRest` intentionally does not;
- using the shared dependency preserves the repository's test override mechanism.

Do not add retries. GET requests are technically idempotent, but transparent proxy retries can duplicate load and complicate cancellation; PATCH and DELETE must never be automatically retried because the repository has no idempotency-key protocol for these legacy mutations.

### Configuration strategy

Reuse `Settings.biosimulations_api_base_url` / `BIOSIMULATIONS_API_BASE_URL`, whose default is already `https://api.biosimulations.org`.

Update `backend/.env.example` comments and deployment documentation only if the implementation needs to clarify that this setting also controls legacy run proxying. No new ConfigMap key is required for the current design.

If a future deployment must use one upstream for simulation submission and another for legacy reads/mutations, add a separate typed setting only then, with explicit aliases and per-overlay values. Do not introduce it speculatively.

### Authentication/header strategy

Request headers should be allowlisted, not copied wholesale:

- forward `Authorization` when supplied, preserving the incoming bearer value;
- forward `Content-Type` for PATCH;
- forward `Accept` when supplied;
- forward `Range` for download resume/range requests if the legacy API supports it;
- forward conditional/cache headers (`If-None-Match`, `If-Modified-Since`) only if the frontend needs them;
- do not forward `Host`, `Content-Length`, `Connection`, `Keep-Alive`, `Transfer-Encoding`, `TE`, `Trailer`, `Upgrade`, `Proxy-*`, or arbitrary `X-*` headers;
- do not forward `Cookie` unless the legacy API contract explicitly requires browser-session cookies and the security review approves it.

The upstream client should set its own `Host`, connection framing, and content length. `httpx` will calculate request framing from the body/stream.

For responses, copy only safe end-to-end headers needed by the frontend:

- `Content-Type`
- `Content-Disposition`
- `Content-Length` when the upstream supplied it and the response remains valid
- `Content-Range`
- `Accept-Ranges`
- `ETag`
- `Last-Modified`
- `Cache-Control`
- `Expires`
- `Vary`
- `Location` only if redirects are intentionally surfaced rather than followed
- `Retry-After` for an upstream rate-limit response if the platform returns that response unchanged

Strip hop-by-hop headers and never copy `Set-Cookie` by default. Confirm whether the frontend needs any additional header before expanding the allowlist.

### Error propagation strategy

For a received upstream response:

- preserve its HTTP status;
- preserve the opaque response body, including legacy error bodies;
- preserve only the response-header allowlist above;
- do not call `response.json()` or Pydantic validation in transparent handlers.

For failures before a response is received:

- `httpx.TimeoutException` -> platform `504` with a stable sanitized detail such as `Timed out while contacting the legacy runs service.`;
- other `httpx.RequestError` -> platform `502` with a stable sanitized detail such as `Could not reach the legacy runs service.`;
- `asyncio.CancelledError` -> re-raise after closing/draining the upstream response; do not convert caller disconnects into a successful response.

Log exception types and bounded operation metadata, never authorization headers, cookies, request bodies, or upstream response bodies.

## 5. Endpoint Implementation Details

The examples below use `{run_id}` because that is the repository's existing FastAPI parameter name. The frontend-facing URL is the same route with the concrete `runId` value.

### GET /runs/{runId}

#### Platform route

`GET /runs/{run_id}` under the existing `run_summary_router` (`prefix="/runs"`).

Register it after any static `/runs/summary` route and before/alongside the existing parameterized routes, following FastAPI route-order discipline.

#### Upstream request

- Method: `GET`
- Path: `/{run_id}` relative to `Settings.biosimulations_api_base_url`, yielding `https://api.biosimulations.org/runs/{run_id}`
- Path forwarding: use `upstream_url("runs", run_id)`; reject dot-only segments and quote the parameter as one segment
- Query: forward the complete query string unchanged, including repeated keys, unless the legacy API contract says a query key is platform-owned
- Body: no request body
- Headers: forward the request-header allowlist, especially optional `Authorization`, `Accept`, and conditional headers
- Response: transparent status/body/header passthrough; do not use `BiosimServiceRest::get_sim_run`, because that method converts the response into a narrower `BiosimSimulationRun` and performs simulator metadata lookup

#### Special behavior

This route is distinct from `GET /simulations/{processing_id}` and should not query platform MongoDB or Temporal. It represents the upstream BioSimulations run identifier.

### PATCH /runs/{runId}

#### Platform route

`PATCH /runs/{run_id}` under `run_summary_router`.

#### Upstream request

- Method: `PATCH`
- Path: `/{run_id}` relative to the configured legacy base URL
- Path forwarding: `upstream_url("runs", run_id)`
- Query: forward the complete query string unchanged
- Body: forward the raw request body without reserialization
- Content type: forward the caller's `Content-Type`; do not assume JSON until the legacy schema is confirmed
- Headers: forward optional `Authorization`, `Accept`, and `Content-Type`; do not forward cookies or hop-by-hop headers by default
- Response: transparent status/body/header passthrough, including a possible JSON object, empty response, or legacy validation error

#### Body validation behavior

The repository contains no PATCH request model and no frontend call to legacy `PATCH /runs/{id}`. `BiosimSimulationRunApiRequest` is the submission body for `POST /runs`, not a PATCH model and must not be reused.

Until the legacy API schema is obtained, the implementation should treat the body as an opaque bounded byte stream and let the legacy service validate field names and values. If product requirements identify a stable PATCH schema, add a dedicated Pydantic request model and explicitly document whether unknown fields are rejected or preserved. Do not invent fields based on `BiosimSimulationRun`.

The implementation must choose one bounded approach:

- preferred for a small known PATCH body: read `Request.body()` with an explicit maximum size and forward bytes; or
- preferred for an unknown/possibly larger body: forward `Request.stream()` as an async iterator to `httpx`, with a configured request-size limit.

The limit and oversize response must be specified before coding; the current repository has a response-body cap for page JSON but no generic inbound request-size helper.

#### Authorization concern

The route should forward the caller's bearer token if present. Whether it should require `get_current_user` is unresolved from this repository: the frontend currently sends no legacy run PATCH request, and no upstream contract is checked in. Resolve this with the legacy API owner before implementation.

### DELETE /runs/{runId}

#### Platform route

`DELETE /runs/{run_id}` under `run_summary_router`.

This is not the same as the existing `DELETE /simulations/{processing_id}`, which deletes platform-owned Mongo records and requires roles.

#### Upstream request

- Method: `DELETE`
- Path: `/{run_id}` relative to the configured legacy base URL
- Path forwarding: `upstream_url("runs", run_id)`
- Query: forward the complete query string unchanged
- Body: normally empty; reject or preserve a body only according to the legacy contract
- Headers: forward optional `Authorization` and `Accept`; do not forward cookies by default
- Response: preserve the upstream status and body. A successful `204 No Content` must remain bodyless; a legacy `200`/`202` body must not be discarded.

#### Special behavior

The current frontend calls this route without an `Authorization` header. The legacy API's authorization behavior is not documented in the repository. Do not silently substitute platform role checks or claim that platform ownership applies to a legacy run unless the product contract explicitly requires that. If the route becomes platform-authenticated, update the frontend integration contract and OpenAPI auth expectations together.

No automatic retry is permitted.

### GET /runs/{runId}/download

#### Platform route

`GET /runs/{run_id}/download` under `run_summary_router`.

#### Upstream request

- Method: `GET`
- Path: `/{run_id}/download` relative to the configured legacy base URL
- Path forwarding: `upstream_url("runs", run_id, "download")`
- Query: forward all query parameters unchanged, including `thumbnail` and any future download options
- Body: none
- Headers: forward `Authorization`, `Accept`, `Range`, and conditional headers when present
- Response: return a Starlette/FastAPI `StreamingResponse` backed by the upstream `httpx.AsyncClient.stream()` context

#### Binary/streaming requirements

Do not call `response.json()`, `response.text`, `response.content`, or Pydantic validation. Do not use the JSON body cap as a download implementation.

The stream generator must:

1. open `client.stream("GET", path, params=..., headers=...)`;
2. expose the upstream status code;
3. copy the safe download headers, especially `Content-Type`, `Content-Disposition`, `Content-Length`, `Content-Range`, and `Accept-Ranges`;
4. yield `response.aiter_bytes()` chunks;
5. close the upstream response when iteration ends, the caller disconnects, or an exception occurs.

If the upstream returns a non-success response, preserve its opaque error body and safe headers using the same streaming path rather than trying to parse it. If the implementation buffers error responses for simpler handling, cap only error buffering and document that choice; never buffer a successful archive/download unnecessarily.

### GET /runs/{runId}/summary

#### Platform route

`GET /runs/{run_id}/summary` already exists in `backend/biosim_server/simulations/router.py::get_run_summary`.

#### Upstream request

Current behavior:

- Method: `GET`
- Path: `/runs/{run_id}/summary`
- Query: intentionally not forwarded by `fetch_upstream_json`
- Caller credentials/cookies: intentionally not forwarded
- Body: none
- Response: upstream JSON is bounded, parsed, mapped by `map_run_summary`, and returned as `RunSummary`

#### Special behavior

This is the one requested endpoint that is already implemented, but it is not a transparent proxy. The platform contract intentionally:

- validates required fields;
- strips unknown fields;
- keeps only the first metadata entry;
- returns a typed `RunSummary`;
- sanitizes upstream 5xx/invalid-body failures;
- strips upstream headers.

Recommended implementation decision: retain this platform-owned contract and do not add a duplicate route. Update it only if the frontend requirement explicitly needs legacy fields, query behavior, auth forwarding, or response headers. If the requirement is truly transparent legacy passthrough, that is a deliberate breaking contract change requiring updates to `backend/tests/simulations/test_run_summary.py`, `backend/tests/api/test_openapi_endpoints.py`, the generated OpenAPI artifact, and frontend response typing.

### GET /runs/{runId}/validate

#### Platform route

`GET /runs/{run_id}/validate` under `run_summary_router`.

#### Upstream request

- Method: `GET`
- Path: `/{run_id}/validate`
- Path forwarding: `upstream_url("runs", run_id, "validate")`
- Query: forward all query parameters unchanged
- Body: none
- Headers: forward optional `Authorization`, `Accept`, and conditional headers
- Response: transparent status/body/header passthrough; do not assume the response is JSON because the legacy validation API schema is not present in this repository

#### Special behavior

Do not use the platform `compatibility` router or `POST /compatibility/check` models for this endpoint. The names are related but the legacy validation operation is a distinct upstream contract.

### GET /runs/summary

#### Platform route

`GET /runs/summary` under `run_summary_router`.

Register this static route before `GET /runs/{run_id}` so the static segment cannot be captured as a run identifier by an earlier dynamic route.

#### Upstream request

- Method: `GET`
- Path: `/runs/summary`
- Query: forward the complete query string unchanged, including repeated pagination/filter keys
- Body: none
- Headers: forward optional `Authorization`, `Accept`, and conditional headers
- Response: transparent status/body/header passthrough unless the legacy response schema is explicitly adopted as a platform-owned Pydantic contract

#### Special behavior

The repository has no route or model for the legacy collection summary. Do not infer its response shape from `ListSimulationRunsResponse`, `RunSummary`, or Mongo `SimulationRunRecord`; those are platform-owned models with different semantics.

## 6. Files to Add or Modify

The following is the recommended change set after the unresolved upstream/auth questions are answered.

| Path | Status | Purpose |
|---|---|---|
| `backend/biosim_server/biosim_runs/legacy_api.py` | New | Define the small `LegacyRunsClient`/proxy helpers, explicit request/response header allowlists, buffered passthrough behavior, streaming download behavior, timeout/transport error mapping, and safe path construction. |
| `backend/biosim_server/biosim_runs/__init__.py` | Modified if exports are desired | Export the new client type only if the existing package style requires it; otherwise keep the helper internal to avoid unnecessary public surface. |
| `backend/biosim_server/simulations/router.py` | Modified | Add the six missing handlers to the existing `run_summary_router`; preserve or explicitly revise `get_run_summary`; add operation IDs, tags, response descriptions, and auth dependencies after the auth decision. |
| `backend/biosim_server/common/upstream.py` | Modified only if needed | Reuse `upstream_url` and `UPSTREAM_TIMEOUT_SECONDS`. Add a narrowly scoped reusable path/query or transport helper only if `legacy_api.py` would otherwise duplicate it; do not weaken current JSON sanitization. |
| `backend/biosim_server/dependencies.py` | Modified only if needed | Add a dependency factory for `LegacyRunsClient` if the client is a class. Continue using the existing pooled `get_http_client`; do not create a per-request client. |
| `backend/biosim_server/log_config.py` | Modified | Extend the structured-field allowlist with bounded legacy-proxy operation/outcome/status/duration/byte fields if those logs are implemented. Never allow URLs, IDs, auth values, bodies, or arbitrary headers through `extra`. |
| `backend/tests/simulations/test_legacy_runs_proxy.py` | New | Hermetic endpoint tests using `MockTransport` plus `ASGITransport`, covering outbound requests and returned responses. |
| `backend/tests/simulations/test_run_summary.py` | Modified only if summary semantics change | Preserve current typed-summary assertions, or replace them with an explicit transparent-contract test if the product decides to change `/runs/{run_id}/summary`. |
| `backend/tests/api/test_openapi_endpoints.py` | Modified | Add every new operation ID to core paths, auth mode, unauthenticated probes, and validation probes/skips. |
| `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` | Regenerated | Update the committed OpenAPI artifact using `uv run python -m scripts.generate_openapi`; never hand-edit it. |
| `backend/.env.example` | Documentation-only modification if useful | Clarify that `BIOSIMULATIONS_API_BASE_URL` controls both existing simulation upstream calls and the legacy run proxy. No new setting is required by the recommended design. |
| `kustomize/config/biosim-{gke,rke,local}/api.env` | No change expected | Existing defaults already target the required upstream. Modify only if an environment needs an explicit non-default value or a separate setting is approved. |
| `backend/CLAUDE.md` / relevant backend docs | Documentation-only modification | Document the new route contract, auth decision, streaming behavior, and no-retry policy once those are finalized. |

No frontend source change is part of this backend implementation plan. A later frontend migration can replace `legacy_api_url` calls with the platform API URL after the backend contract is deployed.

## 7. Step-by-Step Implementation Plan

1. **Resolve the external contract before coding.**
   - Obtain the legacy API's request/response documentation or representative fixtures for `PATCH /runs/{id}`, `DELETE /runs/{id}`, `GET /runs/{id}/validate`, and `GET /runs/summary`.
   - Confirm whether legacy authentication is bearer-token based, cookie based, anonymous, or endpoint-specific.
   - Confirm the PATCH body schema, maximum expected size, success statuses, and whether the body is JSON.
   - Confirm whether `/runs/summary` accepts pagination/filter query parameters and whether download supports range requests.
   - This step is required because none of these contracts are checked into the repository.

2. **Decide the summary contract.**
   - Default recommendation: keep `get_run_summary` as the existing typed platform-owned route.
   - If the frontend requires raw legacy summary fields or headers, explicitly choose transparent behavior and update its tests/OpenAPI/frontend types as a contract migration, not as an incidental proxy change.

3. **Add the legacy client/proxy helper.**
   - Create `backend/biosim_server/biosim_runs/legacy_api.py`.
   - Accept an injected `httpx.AsyncClient`, use `upstream_url` for paths, and pass query pairs without collapsing repeated keys.
   - Implement an explicit request-header allowlist and a response-header allowlist.
   - Implement one buffered opaque-response method for ordinary GET/PATCH/DELETE/collection-summary calls and one streaming method for downloads.
   - Map only no-response timeout/transport failures to 504/502; leave received upstream statuses untouched.
   - Add structured operation/outcome logging using only bounded fields.

4. **Wire the client into the existing `/runs` router.**
   - Add the static `GET /runs/summary` route before `GET /runs/{run_id}`.
   - Add the missing detail, mutation, download, and validate routes next to the existing `get_run_summary` and `get_run_page`.
   - Use `run_id: str` and `upstream_url` rather than manual URL interpolation.
   - Add explicit `operation_id` values, for example `get-legacy-run`, `update-legacy-run`, `delete-legacy-run`, `download-legacy-run`, `validate-legacy-run`, and `get-legacy-runs-summary`; use the repository's established hyphenated operation-ID style.
   - Apply `get_current_user` only if step 1 confirms platform authentication is part of the intended contract. If auth is optional, do not add a required dependency merely because the upstream may accept a token.

5. **Implement download streaming and cancellation handling.**
   - Use `StreamingResponse` with an async generator that owns `client.stream()` and closes it in `finally`.
   - Forward safe download headers and status codes.
   - Verify that caller cancellation closes the upstream stream and does not leave an unhandled task.
   - Do not use `fetch_upstream_json_value`, `response.json()`, or `response.content` for successful downloads.

6. **Add endpoint tests before changing the committed contract.**
   - Create `backend/tests/simulations/test_legacy_runs_proxy.py` using the existing `MockTransport`/`ASGITransport` pattern.
   - Assert outbound method, exact quoted path, repeated query pairs, body bytes, content type, and selected auth headers.
   - Assert returned status, body bytes/text, and safe response headers.
   - Assert unsafe request/response headers are not copied.

7. **Update OpenAPI and meta-contract tests.**
   - Add routes to `backend/tests/api/test_openapi_endpoints.py`.
   - Add validation probes for body-bearing PATCH and path-only routes, or document intentional skips for routes whose validation is opaque/upstream-owned.
   - Regenerate `backend/biosim_server/api/spec/openapi_3_1_0_generated.yaml` with `uv run python -m scripts.generate_openapi`.
   - Run the artifact equality test to catch route/spec drift.

8. **Document configuration and frontend migration.**
   - State that `BIOSIMULATIONS_API_BASE_URL` is the backend proxy upstream.
   - Document the chosen auth behavior and that frontend callers should use the platform API base URL after migration.
   - For browser downloads, ensure the frontend uses the platform URL and does not assume the browser can read a cross-origin legacy `Content-Disposition` header directly.

9. **Run targeted and baseline verification.**
   - Targeted: `cd backend && uv run pytest tests/simulations/test_legacy_runs_proxy.py tests/simulations/test_run_summary.py tests/api/test_openapi_endpoints.py -v`
   - Static: `uv run ruff check .` and `uv run mypy biosim_server tests`
   - Non-external suite: `uv run pytest -m "not integration"`
   - If the summary contract changes, include all affected page tests because `assemble_run_page` uses the summary upstream path independently of the public summary route.

## 8. Testing Plan

### Unit tests

Add focused tests for the new client/helper:

- `upstream_url("runs", run_id, ...)` quotes special characters as one segment;
- dot-only IDs (`.`/`..`, including encoded forms) are rejected before an outbound request;
- query strings retain repeated keys and values;
- request header allowlists exclude hop-by-hop headers and cookies by default;
- response header allowlists exclude hop-by-hop and `Set-Cookie`;
- transport exceptions map to 502 and timeout exceptions map to 504 without leaking exception text.

### Integration/API tests

Use the established two-client setup:

```python
upstream = AsyncClient(
    transport=httpx.MockTransport(handler),
    base_url="https://upstream.test",
)
app.dependency_overrides[get_http_client] = lambda: upstream
caller = AsyncClient(transport=ASGITransport(app=app), base_url="http://platform.test")
```

For each route, the handler should record the received `httpx.Request`; the caller should assert the platform response.

### Endpoint-specific cases

- `GET /runs/{id}`: successful opaque JSON, query forwarding, quoted ID, upstream 404/429/500, timeout, transport failure.
- `PATCH /runs/{id}`: exact raw body bytes, exact content type, authorization forwarding, successful JSON/empty response, upstream 400/401/403/422/500, malformed body handling, request-size limit behavior.
- `DELETE /runs/{id}`: no-body success such as 204, body-bearing success if supported, upstream 401/403/404/409/500, no retries.
- `GET /runs/{id}/download`: binary bytes that are not valid UTF-8/JSON, content type/disposition/length/range headers, query forwarding, chunked delivery, upstream error body, cancellation closing the stream.
- `GET /runs/{id}/summary`: retain current `test_owned_json_credentials_query_and_headers` and mapping/failure tests if the typed contract remains; add a transparent test only if the contract is intentionally changed.
- `GET /runs/{id}/validate`: JSON and non-JSON bodies, query/header forwarding, upstream validation errors.
- `GET /runs/summary`: static-route matching, repeated pagination/filter query forwarding, opaque response body and status.

### Authentication/header tests

Test at least:

- an incoming `Authorization: Bearer ...` is forwarded exactly when allowed;
- absence of authorization does not cause the proxy helper to synthesize credentials;
- `Cookie` and arbitrary caller headers are not forwarded;
- if required platform auth is selected, no outbound call occurs for an unauthenticated caller and the response has the repository's RFC 6750 `WWW-Authenticate` behavior;
- if optional auth is selected, an unauthenticated request reaches the upstream and an authenticated request forwards its bearer token.

### Failure cases

Verify:

- upstream response statuses are preserved for transparent routes;
- upstream response bodies are preserved for received 4xx/5xx responses;
- connection failure is 502 and timeout is 504;
- upstream failure details and bodies are not accidentally logged or included in generated platform error details;
- caller cancellation propagates and closes an in-flight download.

## 9. Error Handling and Edge Cases

- **Path traversal:** always call `upstream_url`; never concatenate `run_id` into a URL. Reject dot-only path segments as the existing helper does.
- **Encoded IDs:** preserve a caller's intended single path segment; add regression tests for `%`, spaces, `#`, `?`, dots inside IDs, and slash-like encoded input.
- **Static/dynamic route collision:** register `/runs/summary` before `/{run_id}`.
- **Upstream redirects:** use the existing client default unless a product decision says otherwise. Do not silently follow redirects to an untrusted host. If redirects are returned, preserve status/body and only expose `Location` according to the header policy.
- **Upstream malformed bodies:** transparent routes do not parse bodies, so malformed JSON is still an opaque successful/error body. The existing typed summary route continues to reject malformed JSON as 502.
- **Empty bodies:** support 204 and empty 200 responses without attempting JSON decoding.
- **Content length:** preserve it only when the returned body/stream framing remains consistent; otherwise let Starlette/httpx calculate framing.
- **Large PATCH bodies:** establish and enforce an inbound limit before implementation. Do not use the existing 16 MiB JSON response cap as an undocumented request policy.
- **Large downloads:** stream successful bodies; do not buffer them in worker memory.
- **Client disconnect:** propagate cancellation and close upstream resources.
- **Retries:** no automatic retries for PATCH or DELETE; preferably none for transparent GETs unless a later design adds an explicit idempotency/safe-retry policy.
- **Upstream rate limiting:** preserve received 429 status and `Retry-After` for transparent routes; do not convert it to the platform page/workflow limiter semantics.
- **Existing platform summary:** do not accidentally replace its typed mapping and sanitization while adding the other routes.

## 10. Security Considerations

- **Authentication:** forward only a caller-provided bearer token if the confirmed legacy contract requires it. Never log or synthesize tokens.
- **Authorization:** do not assume platform Auth0 ownership equals legacy API ownership. The platform's `SimulationRunRecord` ownership applies to `/simulations/*`, not automatically to an upstream BioSimulations run ID.
- **Header forwarding:** use allowlists. Never forward `Host`, hop-by-hop headers, arbitrary `X-*` headers, cookies, or proxy headers without an explicit security decision.
- **Sensitive data:** exclude authorization values, cookies, raw request bodies, upstream bodies, and full URLs/IDs from structured logs.
- **Input validation:** quote path parameters, preserve query pairs safely, bound PATCH request size, and reject invalid route IDs before any upstream call.
- **Upstream URL construction:** use the configured base URL only; never let a request parameter select the host or scheme.
- **SSRF:** the base URL comes from trusted deployment configuration; no caller-controlled URL may enter the proxy.
- **Downloads:** preserve `Content-Disposition` from the trusted configured upstream, but do not allow caller-provided headers to override response headers.
- **CORS/browser behavior:** the platform CORS configuration is in `backend/biosim_server/api/main.py`; a frontend migration should use the platform origin and not rely on direct cross-origin legacy API access.

## 11. Observability

The repository has no general metrics/tracing abstraction. Logging is JSON-formatted by `backend/biosim_server/log_config.py` with an explicit allowlist.

Add one bounded structured record per proxy operation, modeled on `common/upstream.py`:

- operation name from a fixed vocabulary;
- outcome (`ok`, `upstream_error`, `client_error`, `timeout`, `transport_error`, `cancelled`);
- upstream HTTP status, if a response was received;
- duration in milliseconds;
- bytes relayed, if known.

Do not log `run_id`, full URL, query values, authorization, cookies, request bodies, response bodies, or arbitrary headers unless a separate privacy review approves a hashed/low-cardinality identifier. Extend `JsonFormatter`'s allowlist in `log_config.py` for only these bounded fields.

If the deployment already provides ingress metrics, use those for request counts and latency. Do not add a new metrics dependency solely for this feature.

## 12. Deployment and Configuration Changes

### Expected changes

- The default `Settings.biosimulations_api_base_url` already points to `https://api.biosimulations.org`.
- Existing `api.env` ConfigMaps in `kustomize/config/biosim-gke`, `biosim-rke`, and `biosim-local` do not need a new key if the existing setting is reused.
- Update `backend/.env.example` and backend operational documentation to describe the setting's proxy role.
- Regenerate and commit the backend OpenAPI artifact.

### Conditional changes

If a separate proxy upstream is required operationally:

- add a typed `legacy_runs_api_base_url` field to `backend/biosim_server/config.py`;
- add its documented environment alias to `backend/.env.example`;
- add explicit values to every cluster's `kustomize/config/*/api.env`;
- use that setting only in `LegacyRunsClient`;
- add settings tests proving local/test overrides.

Do not make this conditional split unless the existing shared base URL is insufficient.

No Dockerfile, Temporal worker, Mongo migration, or Kubernetes workload change is expected: this is an API-only route and client addition.

## 13. Frontend Integration Contract

Once implemented and deployed, the frontend will call the platform API base URL for:

```text
GET    {PLATFORM_API_URL}/runs/{runId}
PATCH  {PLATFORM_API_URL}/runs/{runId}
DELETE {PLATFORM_API_URL}/runs/{runId}
GET    {PLATFORM_API_URL}/runs/{runId}/download
GET    {PLATFORM_API_URL}/runs/{runId}/summary
GET    {PLATFORM_API_URL}/runs/{runId}/validate
GET    {PLATFORM_API_URL}/runs/summary
```

Contract expectations:

- `runId` is forwarded as one safely encoded upstream path segment.
- Query parameters are retained for transparent routes, including repeated keys.
- PATCH body bytes and content type are forwarded according to the confirmed legacy schema.
- An incoming bearer token is forwarded only if the final auth decision enables it.
- Transparent routes preserve received upstream status, opaque body, and safe end-to-end headers.
- Download responses remain binary/streamed and preserve `Content-Type`, `Content-Disposition`, and available length/range metadata.
- Network failures are platform 502/504 responses with sanitized details.
- `/runs/{runId}/summary` remains the existing typed `RunSummary` contract unless a deliberate breaking-contract decision changes it.

The frontend should stop constructing direct `https://api.biosimulations.org` URLs for these operations after the backend route is available. In particular, browser download links should target the platform endpoint so CORS and response-header behavior are controlled by the platform.

## 14. Acceptance Criteria

- All seven requested paths exist under the existing `/runs` router without conflicting with `/runs/{run_id}/summary` or `/runs/{run_id}/page`.
- The six missing paths proxy to the corresponding legacy paths using the configured `biosimulations_api_base_url`.
- `run_id` path construction uses the repository's safe quoting/dot-segment rules.
- Query parameters are forwarded losslessly for transparent routes.
- PATCH forwards the confirmed body format and content type without inventing an incompatible model.
- DELETE behavior and authorization are explicitly tested and documented.
- Download responses are streamed, not JSON-decoded or unnecessarily buffered, and preserve required content headers.
- Existing typed summary behavior is either preserved with regression tests or intentionally migrated with updated contract tests and documentation.
- Received upstream statuses/bodies are preserved for transparent routes; timeout/transport failures map to documented sanitized 504/502 responses.
- Authorization/header forwarding is allowlisted and covered by tests.
- No automatic mutation retries exist.
- Structured logs contain bounded operation/outcome/status/duration/byte fields and no secrets or payloads.
- OpenAPI includes every route with stable operation IDs, and the committed artifact matches `app.openapi()`.
- Targeted tests, `ruff`, `mypy`, and the non-external pytest suite pass.
- Deployment configuration uses the existing upstream setting or documents every new per-environment setting if a split base URL is approved.

## 15. Open Questions / Assumptions

1. **PATCH schema:** What fields and content type does legacy `PATCH /runs/{runId}` accept, and what request-size limit is appropriate?
2. **Mutation authentication:** Does legacy PATCH/DELETE require the caller's bearer token, a cookie, an API key, or no credential? Should the platform require a validated Auth0 user before forwarding either mutation?
3. **Legacy auth forwarding:** Is forwarding the platform caller's `Authorization` header sufficient, or does the legacy API expect a different audience/token?
4. **Collection summary contract:** What query parameters and response shape does `GET /runs/summary` use?
5. **Validation response:** Is `GET /runs/{runId}/validate` JSON, plain text, or another media type, and does it support download/range semantics?
6. **Download behavior:** Does the download endpoint support `Range`, conditional requests, redirects, or a required custom header?
7. **Summary compatibility:** Does the frontend need the complete raw legacy summary, or is the existing platform-owned `RunSummary` projection sufficient?
8. **Separate upstream setting:** Should legacy proxy traffic share `BIOSIMULATIONS_API_BASE_URL` with current simulation submission/polling, or must it be independently configurable by environment?
9. **Frontend rollout:** Should frontend changes migrate all direct legacy URLs at once, or should the platform routes be introduced first and adopted incrementally?

Facts established from repository inspection are separated above from recommendations and these unresolved external-contract questions. No endpoint implementation should begin until the mutation/auth and response-shape questions are answered.
