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

## 16. Follow-up: Capping Buffered Legacy Proxy Responses

### 16.1 Status and problem statement

Sections 1-15 of this document describe the implemented proxy. Everything in them
is present and verified except one boundedness property, so this section closes
that gap and is the condition for calling the implementation 100% complete.

`backend/biosim_server/biosim_runs/legacy_api.py::proxy_run` splits its responses
in two. `operation == "download"` hands off to `_DownloadResponse`, which yields
`upstream.aiter_raw()` lazily and never holds more than one chunk. Every other
operation takes the buffered branch:

```python
elif upstream.is_stream_consumed:
    # MockTransport may supply an already buffered response. Real
    # responses always take the raw stream path below.
    response_body = upstream.content
else:
    response_body = b"".join([chunk async for chunk in upstream.aiter_raw()])
```

That `b"".join` reads an upstream body of any size into worker memory and then
copies it into a second contiguous allocation, on the request path, with no
bound. The configured ceiling `UPSTREAM_MAX_RESPONSE_BYTES` exists
(`Settings.upstream_max_response_bytes`, default 16 MiB, `gt=0`) and is documented
as "a hard ceiling on one upstream JSON body's decoded size" - but the proxy
never consults it.

This is not an oversight of a setting that was forgotten. The cap was introduced
as `SHARED-MAJ-001` against the page aggregators, which are platform-owned JSON
contracts, and section 3 of this document deliberately declined to route the proxy
through `fetch_upstream_json` ("must not be expanded into a transparent
passthrough helper"). The consequence is that the one code path that buffers an
*opaque* body - where the platform cannot validate the shape and therefore cannot
reason about the size - is the one path that inherited no bound.

The exposure is real: `GET /runs/{run_id}`, `GET /runs/{run_id}/validate`,
`GET /runs/summary`, and every PATCH/DELETE response body are attacker-influenced
in size whenever the upstream (or a network position between it and the platform)
chooses them. A single large or slow-drip metadata or error response is unbounded
worker memory per in-flight request.

### 16.2 Design decisions

#### D1. Reuse `UPSTREAM_MAX_RESPONSE_BYTES`; add no new setting

The memory risk is the one that setting already documents: one upstream body read
whole into a worker. Sixteen MiB is far above any legitimate run metadata,
validation, or run-summary response. Introducing `LEGACY_MAX_RESPONSE_BYTES`
would add a ConfigMap key, an `.env.example` line, a `config.py` field, an alias,
per-cluster values, and a second number for operators to reason about - in
exchange for no additional safety. Reuse the existing cap and update its
documentation to cover both consumers.

#### D2. Cap RAW bytes, not decoded bytes - and why this is the opposite of `common/upstream.py`

`common/upstream.py::_read_capped_body` measures `aiter_bytes()`, i.e. *decoded*
bytes, and that is correct there: the platform is about to `json.loads` the body,
so the decoded length is the memory and CPU it is about to spend.

Copying that helper verbatim would be a correctness regression, and this is the
single most important decision in this section.

The proxy deliberately never decodes. `aiter_raw()` is what keeps
`Content-Encoding`, `Content-Length`, `Content-Range` and `ETag` mutually
consistent - documented in `backend/CLAUDE.md` ("Upstream Content-Encoding is
preserved with raw bytes so compression cannot invalidate Content-Length,
Content-Range or ETag") and pinned by
`test_download_range_binary_and_compression`. `httpx`'s `aiter_bytes()` *decodes*
content-encoding, so a `Content-Encoding: gzip` metadata response would be
silently decompressed while the forwarded header still said `gzip`, handing the
caller bytes that do not match the declared encoding.

So the cap must be measured on `aiter_raw()`. This is not a weakening:

- the proxy's memory cost *is* the raw byte count, because the raw bytes are what
  it retains and relays. Decoded size is not a quantity the proxy ever materializes;
- the gzip-bomb scenario that motivates the decoded cap does not apply to this
  worker. A 1 MiB gzip body that would expand to 200 MiB costs *this* process
  1 MiB; the expansion is paid by the client that requested it. That exposure
  already exists, deliberately, on the download path, and capping it here would
  be capping someone else's decoder;
- the invariant that actually matters - memory held is bounded - holds either way.

Consequence to state explicitly in review: a compressed buffered response is
capped by its **on-the-wire** size. A 20 MiB `Content-Encoding: gzip` body that
decodes to 4 KiB is rejected. That is intended, and it is the conservative
direction.

#### D3. Over-limit is a sanitized platform 502, never a truncated relay

The proxy's contract is "received statuses and opaque bodies are relayed". A body
that exceeds the cap cannot be relayed faithfully, and relaying the first N bytes
with the upstream's status code would be worse than useless: the caller would
parse a truncated JSON document and see a schema error, or worse, accept a
truncated array. So the breach terminates the response with a platform-generated
error, matching `common/upstream.py`:

- status: **502** (the upstream produced something the platform cannot serve).
  Not 413 - the caller's request is not oversized; it is the upstream's response.
- detail: a static sanitized string naming no limit, no size, no body, no URL.
- the upstream status is *still recorded* in the log, so an operator sees
  `legacy_status: 200, legacy_outcome: too_large`.

This is a deliberate, documented exception to "received statuses are preserved",
and it is the "over-limit outcome" this section exists to preserve. It applies
equally to 2xx and to 4xx/5xx bodies: an oversized upstream 500 is a 502, because
a truncated error body is not a relayable error body.

#### D4. The cap is read from bytes actually read; `Content-Length` is not consulted

A declared `Content-Length` must not decide anything:

- overstating it would reject a legitimately small response, and
- understating or omitting it changes nothing.

`tests/common/test_upstream_bounds.py::test_declared_content_length_is_not_trusted`
already pins this semantics for the shared cap; the proxy must not introduce a
second, looser contract for the same setting. (This deliberately rules out the
tempting "reject early on a huge `Content-Length` without reading" optimization -
see 16.7.)

#### D5. Abandon the stream at the breach; never buffer the oversized body

The rejection must not first read the rest of the body to be sure. Iteration stops
at the first chunk that crosses the cap, and `proxy_run`'s existing `finally`
already calls `await transfer.close(upstream)` on the non-handed-off path, which
closes the upstream response. No new cleanup machinery is needed - but a test must
assert `closed` and that not all chunks were read, or a future refactor could
quietly reintroduce the buffering.

#### D6. Cap both buffered branches, including the pre-consumed one

`upstream.is_stream_consumed` is reached when `MockTransport` supplies an
already-buffered response. Its bytes are already in memory, so the cap there is a
correctness guard rather than a memory guard - but it must be enforced anyway, so
that every buffered response has exactly one outcome and no test can observe an
uncapped path. (Reading `upstream.content` on an *un*consumed response would
buffer the whole stream unbounded; that is precisely why the `is_stream_consumed`
guard stays.)

Caveat on what this branch measures: `httpx.Response(..., content=/text=/json=...)`
calls `read()` in its constructor, and `read()` goes through `iter_bytes()`, so
`upstream.content` here is already **decoded**. This branch therefore caps decoded
length, not raw length, and relays decoded bytes under an unchanged
`Content-Encoding`. That is a pre-existing, test-only wart (real responses from
`client.send(..., stream=True)` are never pre-consumed) and is not fixed here. Its
practical consequence is for tests: any test that asserts raw-byte or
stream-abandonment behavior **must** build the upstream response with
`stream=Chunks([...])`, never `content=`/`text=`, or it silently exercises this
branch instead of `_read_capped_raw` (see 16.5).

#### D7. `too_large` is recorded as its own outcome, and it wins over the status-derived outcome

`_Transfer.outcome` currently derives from the upstream status at
`legacy_api.py:188-194` (`upstream_error` / `client_error` / `ok`). A breach is
the terminal cause and must not be relabelled, so the raise site sets
`transfer.outcome = "too_large"` before raising.

This exposes a trap in the existing handler: `except HTTPException:` currently
does `transfer.outcome = "client_error"` unconditionally, which would overwrite
`too_large` and mislabel the breach as a client error. See 16.4, step 3.

`too_large` is already established vocabulary in this repo - it is the
`upstream_outcome` asserted by `test_page_instrumentation.py` - so reuse it rather
than inventing a proxy-specific value. `log_config.py` allowlists the *field*
`legacy_outcome`, not its values, so no formatter change is required.

`transfer.size` is incremented per chunk inside the read helper rather than
assigned once at the end, so the breach log carries an accurate `legacy_bytes`.
This mirrors how `_DownloadResponse.chunks` accumulates `self.transfer.size`.

### 16.3 What deliberately stays uncapped

- **`download` responses.** They stream and never buffer, so they have no
  memory exposure to bound. Capping them would break legitimate large archives.
- **`204` / `304` responses.** The body is never read at all on these paths;
  there is nothing to bound.
- **The inbound PATCH body.** Already bounded independently by
  `LEGACY_PATCH_MAX_BYTES` (20 MiB, `413`, rejected before any mutation is sent).
  That is a different direction of flow and a different limit; it is unaffected
  and must stay distinct. Reusing the 16 MiB JSON cap for inbound PATCH bodies
  was explicitly rejected in section 9.

### 16.4 Implementation

All source changes are in `backend/biosim_server/biosim_runs/legacy_api.py`. No
route, signature, or OpenAPI change.

**Step 1 - import the setting.**

```python
from biosim_server.config import get_settings
```

**Step 2 - add the sanitized detail and the capped raw read, immediately after
the `_Transfer` class** (not next to `_patch_body`, which precedes it). The module
has no `from __future__ import annotations` and targets Python 3.13, so the
`transfer: _Transfer` annotation is evaluated at definition time; placing the
helper above `_Transfer` would raise `NameError` on import.

```python
# Consumer-facing detail for a buffered response that exceeds the configured
# cap. Sanitized: it names nothing about the limit, the size, the body, or the
# upstream. Distinct wording from common.upstream._OVERSIZE_DETAIL because the
# two are different failures - a platform-owned contract could not be loaded,
# versus an opaque response the proxy is refusing to relay truncated.
_OVERSIZE_DETAIL = "The legacy runs service returned a response that is too large to relay."


async def _read_capped_raw(
    upstream: httpx.Response, transfer: _Transfer, limit: int
) -> bytes:
    """Buffer the relayed body, refusing to hold more than ``limit`` raw bytes.

    Counted on ``aiter_raw``, unlike ``common.upstream._read_capped_body``'s
    ``aiter_bytes``: the proxy relays raw bytes and never decodes them, which is
    what keeps Content-Encoding, Content-Length, Content-Range and ETag valid.
    The raw length *is* the memory cost, so this bounds what it actually holds -
    a compressed body is measured on the wire and costs this worker only its
    compressed size.

    Content-Length is not consulted: one that overstates the body would reject
    a legitimate small response, and one that understates it changes nothing,
    because the decision is made from the bytes actually read. The stream is
    abandoned the moment the cap is crossed and the caller's ``finally`` closes
    it, so a breach never buffers the oversized remainder.
    """
    body = bytearray()
    async for chunk in upstream.aiter_raw():
        transfer.size += len(chunk)
        # Checked before appending (as _patch_body does), so the buffer itself
        # never holds more than ``limit`` bytes.
        if len(body) + len(chunk) > limit:
            transfer.outcome = "too_large"
            raise HTTPException(502, _OVERSIZE_DETAIL)
        body += chunk
    return bytes(body)
```

**Step 3 - cap the buffered branch in `proxy_run`.**

Replace the `if upstream.status_code in (204, 304): ... elif ... else ...` block:

```python
        response_headers = _headers(upstream.headers, _RESPONSE_HEADERS)
        if upstream.status_code in (204, 304):
            response_body = b""
            if upstream.status_code == 204:
                response_headers.pop("content-length", None)
        else:
            limit = get_settings().upstream_max_response_bytes
            if upstream.is_stream_consumed:
                # MockTransport may supply an already buffered response. Real
                # responses always take the raw stream path below. Already in
                # memory, so this is a correctness guard, not a memory guard -
                # but leaving it out would give the cap a second, test-only hole.
                response_body = upstream.content
                transfer.size = len(response_body)
                if transfer.size > limit:
                    transfer.outcome = "too_large"
                    raise HTTPException(502, _OVERSIZE_DETAIL)
            else:
                response_body = await _read_capped_raw(upstream, transfer, limit)
            # Starlette computes the actual length; the upstream value must go
            # either way now that a relayed body is known to be bounded.
            response_headers.pop("content-length", None)
        return Response(
            response_body, status_code=upstream.status_code, headers=response_headers
        )
```

Note that `transfer.size = len(response_body)` moves out of the old unconditional
position: the streamed branch now accumulates it inside `_read_capped_raw`, and
setting it afterwards would double-count.

**Step 4 - stop the `HTTPException` handler from clobbering the outcome.**

```python
    except HTTPException:
        # A specific outcome (the buffered-body cap's `too_large`) is recorded at
        # its raise site; do not relabel it. A bare HTTPException here is
        # upstream_url's 404 or the inbound PATCH 413, both client errors.
        if transfer.outcome == "ok":
            transfer.outcome = "client_error"
        raise
```

Invariant for future raise sites inside this `try`: **set
`transfer.outcome` before raising, or it will be recorded as `client_error`.**

**Step 5 - nothing else in source.** No route handler, `config.py` field,
`log_config.py` field, or OpenAPI entry changes. `get_settings()` is already
`@lru_cache`d, so the per-request cost is a cached attribute read.

### 16.5 Test plan

Extend `backend/tests/simulations/test_legacy_runs_proxy.py` - it already has
`Chunks` (records `reads` and `closed`), the `clients()` two-client harness, and
the `JsonFormatter` log-capture pattern, so no new harness is needed. The shared
`_Body`-style semantics are already pinned by
`backend/tests/common/test_upstream_bounds.py`; mirror that file's structure
rather than inventing a third idiom.

Every upstream body below that is expected to reach `_read_capped_raw` must be
supplied as `httpx.Response(..., stream=Chunks([...]))`. A `content=`/`text=`
response is pre-consumed (and pre-decoded) by httpx and takes the
`is_stream_consumed` branch instead - see D6. The existing module tests already
use `Chunks` throughout, so follow them rather than the `text=` form used in
`test_page_instrumentation.py`.

Add a module-level fixture so the boundary is testable without megabyte payloads:

```python
@pytest.fixture
def cap(monkeypatch: pytest.MonkeyPatch) -> int:
    limit = 64
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", limit)
    return limit
```

Cases:

1. **`test_oversize_buffered_response_is_rejected_and_the_stream_is_abandoned`** -
   chunked body crossing the cap: status 502, detail `== _OVERSIZE_DETAIL`,
   `stream.closed`, and `stream.reads < len(chunks)` (proves the remainder was
   never buffered). This is the test that prevents the regression.
2. **`test_buffered_body_at_the_cap_is_relayed_exactly`** - a body of exactly
   `cap` bytes comes back whole, status preserved.
3. **`test_declared_content_length_is_not_trusted`** - parameterized like the
   `tests/common/test_upstream_bounds.py` original: a `Content-Length` of `10 * cap` over a
   small body is relayed as 200; an absent `Content-Length` over a large body is
   502.
4. **`test_oversize_upstream_error_is_not_relayed`** - upstream `500` with an
   oversized body returns 502, not a truncated 500. Locks D3 for the error case.
5. **`test_oversize_is_recorded_as_too_large_without_the_body`** - build the
   oversized body from a `SECRET` marker string, capture records with `caplog`
   and format them through `JsonFormatter` (the pattern
   `test_static_route_and_safe_structured_logging` already uses); assert exactly
   one `biosim_server.biosim_runs.legacy_api` record with
   `legacy_outcome == "too_large"`, `legacy_operation == "get"`,
   `legacy_status == 200`, `legacy_bytes > 0`, and that `SECRET` appears neither
   in the formatted log nor in the 502 response body. Do **not** assert that the
   numeric limit is absent from the log text: with `cap = 64` the substring
   `"64"` can legitimately appear in `legacy_duration_ms` or elsewhere, and
   `legacy_bytes` is by design within one chunk of the limit. The analogue,
   `tests/pages/test_page_instrumentation.py::test_oversize_body_is_recorded_without_the_body_or_limit`,
   likewise asserts only the secret marker despite its name.
6. **`test_buffered_compressed_response_stays_compressed_under_the_cap`** -
   a `Content-Encoding: gzip` body supplied via `stream=Chunks([...])` (not
   `content=`; see D6), under the cap, is relayed as the *compressed* bytes with
   the header intact - read it back with `caller.stream(...)` + `aiter_raw()` as
   `test_download_range_binary_and_compression` does, since the caller client
   would otherwise transparently decode it. This is the regression guard for
   D2: it fails loudly if someone swaps `aiter_raw` for `aiter_bytes`.
7. **`test_downloads_are_not_capped`** - a download body larger than the cap
   streams to completion with its status and headers intact. Guards 16.3.
8. **`test_empty_and_not_modified_responses_are_unaffected`** - `204`/`304` still
   return bodyless with `content-length` removed, under a tiny cap.
9. **`test_the_cap_is_read_from_settings`** - a second limit value changes the
   boundary, proving the proxy reads the setting rather than a constant.

No existing test should change. Specifically: the largest response body any
current test produces is a few bytes; `test_patch_at_limit_is_forwarded_exactly`
sends 20 MiB as a *request* body and its upstream response is `204` (uncapped
path); and the only compressed-response test,
`test_download_range_binary_and_compression`, is on the download path. If any
test does start failing, treat it as a real finding about that test's payload,
not as a cap to be loosened.

### 16.6 Documentation and configuration changes

Documentation only - no new configuration key, so no `kustomize` overlay edit.

| Path | Change |
|---|---|
| `backend/biosim_server/config.py` | Extend the `SHARED-MAJ-001` comment on `upstream_max_response_bytes` to name its second consumer (the legacy proxy's buffered responses) and note the proxy measures raw bytes. |
| `backend/.env.example` | Amend the `UPSTREAM_MAX_RESPONSE_BYTES` block (currently "Ceiling on one upstream JSON body's DECODED size ... for the page aggregations") to cover the legacy proxy, and distinguish raw-byte measurement there from decoded-byte measurement for pages. |
| `backend/CLAUDE.md` | In "Legacy runs proxy", amend "Metadata responses buffer opaque bytes and recalculate length" to state the cap, the 502 outcome, the raw-byte basis, and that downloads are not capped. Cross-reference the existing `UPSTREAM_MAX_RESPONSE_BYTES` row. |
| `kustomize/README-config.md` | The existing row for `UPSTREAM_MAX_RESPONSE_BYTES` ("decoded-body ceiling for one upstream fetch") is now slightly under-describing; broaden to cover the buffered proxy body. No new row, no new key. |
| `.agents/legacy-runs-proxy-implementation-plan.md` | This section. |

### 16.7 Considered and rejected alternatives

- **Share/generalize `common/upstream.py::_read_capped_body`.** Rejected: the two
  measure different things (decoded vs raw), sanitize differently, and log into
  different vocabularies. A shared helper with a flag would be a helper with two
  behaviors, and section 3 of this document already commits to not turning that
  module into a passthrough helper.
- **Reject early on an oversized declared `Content-Length` without reading the
  body.** Rejected: it contradicts the "Content-Length is never trusted"
  semantics the shared cap is already tested for, and an upstream that
  overstates `Content-Length` on a small body would have that body refused.
- **Return 413 on breach.** Rejected: 413 describes the caller's request. The
  caller's request was fine; the upstream response was not relayable. 502 matches
  `common/upstream.py` and the existing sanitized-upstream-failure convention.
- **Truncate at the cap and relay.** Rejected: hands the caller a body that
  contradicts the upstream status, in the one place - a transparent proxy - where
  the caller has no way to detect it.
- **Apply the cap to downloads as well.** Rejected: downloads are streamed and
  have no memory exposure; a cap there only breaks large legitimate archives.
- **A separate, lower cap for `GET /runs/summary`.** Deferred to 16.10: a
  collection is the one buffered route whose legitimate size is not predictable
  from the route's shape, and the right fix if it grows is upstream pagination
  (already an open question in section 15, item 4), not a second number.

### 16.8 Verification

```bash
cd backend
uv run pytest tests/simulations/test_legacy_runs_proxy.py -v
uv run pytest tests/simulations/test_legacy_runs_proxy.py \
  tests/common/test_upstream_bounds.py tests/pages/test_page_instrumentation.py -v
uv run ruff check .
uv run mypy biosim_server tests
uv run pytest -m "not integration"
```

`ruff` and `mypy` are the `lefthook` pre-commit/pre-push gates (same invocations
as `lefthook.yml`); `backend-ci` (`.github/workflows/ci.yaml`) itself runs only
`uv run python -m pytest`, so a clean local `ruff`/`mypy` run is what actually
gates those two.

The OpenAPI artifact should be **byte-identical** after this change - no route,
signature, or schema changes. Regenerate and run the equality check anyway, to
prove the drift check itself is still green:

```bash
uv run python -m scripts.generate_openapi
uv run pytest tests/api/test_openapi_endpoints.py -v
```

### 16.9 Acceptance criteria

- Every non-download legacy proxy response is refused above
  `UPSTREAM_MAX_RESPONSE_BYTES`, counted on bytes actually read.
- A breach is a sanitized 502 whose detail names no limit, size, body, or
  upstream; it is never a truncated body under the upstream's own status.
- The upstream response is closed and the remainder of the body is never read at
  the breach.
- The breach is observable as exactly one bounded log record carrying
  `legacy_outcome: too_large`, the received `legacy_status`, and a non-zero
  `legacy_bytes`, with no body content.
- Download, `204` and `304` responses are unchanged.
- Compressed buffered responses are still relayed as raw compressed bytes with
  `Content-Encoding` intact.
- No new setting, ConfigMap key, or environment variable is introduced.
- The nine cases in 16.5 pass; no previously passing test changes.
- `ruff`, `mypy`, the non-integration suite, and the OpenAPI equality check pass.

### 16.10 Open questions for this follow-up

1. **Cap adequacy.** Is 16 MiB comfortably above every legitimate buffered legacy
   response in production? It is an order of magnitude above plausible run
   metadata and validation bodies. If measurement shows otherwise, raise it per
   cluster exactly as the page aggregations do - do not add a second setting.
2. **`GET /runs/summary` growth.** This is a collection, so its size is not
   bounded by the shape of any one run. 16 MiB is generous for run summaries, but
   if the legacy collection grows unbounded the fix is upstream pagination, which
   depends on the unresolved section 15 question 4. Until then the cap is the
   backstop.
3. **Over-limit visibility.** Should a `too_large` breach raise an alert
   threshold, or is a warning-level log record sufficient? `_Transfer.log` emits
   at INFO for every operation and has no severity distinction; `common/upstream`
   logs its equivalent at WARNING. Aligning the severity is a small, separate
   change and is deliberately out of scope here.
4. **Frontend impact.** A caller whose response is now refused sees a 502 with a
   sanitized detail instead of a body. No frontend change is required for the
   cap, but if any client has a retry-on-502 path it should be confirmed not to
   amplify against an upstream that is genuinely over-producing.
