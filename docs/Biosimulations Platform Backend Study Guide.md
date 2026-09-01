# BioSimulations Platform Backend Study Guide

> Repository-specific orientation for backend architecture, FastAPI, Auth0, MongoDB/materialized views, legacy API proxies, and testing.

## The 10 files and directories to inspect first

1. **`backend/biosim_server/api/main.py`** — FastAPI entry point and composition root. Study lifespan management, CORS, router registration, health checks, and the older endpoints that still live directly on `app`.
2. **`backend/biosim_server/simulations/router.py`** — Best example of modern endpoint design in the repository: Pydantic models, authentication, ownership checks, Mongo services, Temporal, and error handling.
3. **`backend/biosim_server/projects/router.py`** — Project search, statistics, reindexing, and the existing `GET /projects/{project_id}/summary` proxy.
4. **`backend/biosim_server/common/auth/auth0.py`** — JWT validation, JWKS caching, issuer/audience/expiration checks, and required versus optional authentication.
5. **`backend/biosim_server/common/auth/roles.py`** — Role checks and simulation-run access/mutation policies.
6. **`backend/biosim_server/dependencies.py`** — Construction and lifecycle of MongoDB, Temporal, storage, project search, and the BioSimulations client.
7. **`backend/biosim_server/biosim_runs/biosim_service.py`** — Existing external BioSimulations client, including runs, logs, simulation data, and project summary calls.
8. **`backend/biosim_server/projects/search.py`** — Platform-owned project search materialization. This is a search projection, not a complete project-detail view.
9. **`backend/tests/projects/test_project_summary.py`** — Exact tests for the project-summary task: pass-through behavior, public access, URL encoding, error mapping, and credential isolation.
10. **`frontend/app/pages/projects/[id].vue` and `frontend/app/pages/runs/[id].vue`** — Primary consumers of the legacy endpoints. Read with `frontend/app/models/simulation.ts`.

> [!important]
> `GET /projects/{id}/summary` already exists in the platform backend, is registered with FastAPI, and has focused tests.

## Repository orientation

### Study first

| Path | Purpose and patterns | Relation to current work |
|---|---|---|
| `backend/biosim_server/api/main.py` | Application lifecycle, middleware, and router registration | Shows how new routers become part of the API |
| `backend/biosim_server/simulations/` | Backend-for-frontend run orchestration | Strongest current service/model/router example |
| `backend/biosim_server/projects/` | Search, materialization, and summary proxy | Directly owns the project-summary task |
| `backend/biosim_server/common/auth/` | JWT authentication and role/ownership authorization | Required before protecting proxy endpoints |
| `backend/biosim_server/dependencies.py` | Process-wide service construction | New upstream clients should integrate consistently |
| `backend/biosim_server/config.py` | Pydantic settings and environment variables | Auth0, MongoDB, storage, and upstream configuration |
| `backend/biosim_server/biosim_runs/biosim_service.py` | Legacy API client abstraction | Natural starting point for more proxy operations |
| `backend/tests/common/test_auth0.py` | JWT/JWKS tests | Model for authentication tests |
| `backend/tests/simulations/test_router.py` | Endpoint and ACL tests | Model for protected proxy tests |
| Frontend project/run detail pages | Current legacy API callers | Establish contracts that the backend must preserve |

### Study next

- `backend/biosim_server/simulations/models.py`: API-domain models and serialization.
- `backend/biosim_server/simulations/database.py`: Mongo queries, visibility, ownership, and pagination.
- `backend/biosim_server/projects/models.py`: project response models and frontend aliases.
- `backend/biosim_server/projects/database.py`: Mongo joins behind the live project view.
- `backend/biosim_server/biosim_runs/models.py`: run and HDF5 models.
- `backend/tests/conftest.py` and `backend/tests/fixtures/`: Mongo, storage, JWT, Keycloak, and Temporal fixtures.
- `frontend/nuxt.config.ts`: `api_url`, server-only `apiUrl`, and `legacy_api_url`.
- `frontend/app/plugins/auth0.client.ts`: Auth0 SPA initialization.
- `frontend/app/middleware/auth.ts`: login enforcement, but not access-token forwarding.
- `frontend/app/components/FilesOutputsTable.vue`: download and results URLs.

### Useful later

- `backend/biosim_server/biosim_verify/`: verification workflows.
- `backend/biosim_server/biosim_omex/`: archive persistence and owner-aware access.
- `backend/biosim_server/common/storage/`: local/GCS/MinIO storage abstraction.
- `backend/biosim_server/worker/`: Temporal worker registration.
- `backend/biosim_server/users/`: Auth0 Management API operations.
- `backend/biosim_server/rbac_demo/`: explicit RBAC examples.
- `backend/biosim_server/log_config.py`: logging setup.
- `docs/project-search-api-plan.md`: project-search design history.
- `docs/workflows-architecture.md`: Temporal flow.
- `kustomize/config/*/*.env`: deployed configuration names; never store secrets in this note.

## Backend request lifecycle

```text
Nuxt frontend
  → FastAPI router
  → authentication dependency, if applicable
  → Pydantic/path/query validation
  → domain service or workflow orchestration
  → MongoDB, Temporal, storage, or BioSimulations upstream API
  → Pydantic serialization or explicit pass-through response
  → frontend
```

Example, listing runs:

```text
POST /simulations/runs
  → get_optional_user
  → ListSimulationRunsRequest validation
  → SimulationRunDatabaseService.query_simulation_runs
  → ownership/visibility filtering in Mongo
  → SimulationRun.from_record
  → ListSimulationRunsResponse
```

Example, project summary:

```text
GET /projects/{project_id}/summary
  → path extraction
  → BiosimService.get_project_summary
  → GET api.biosimulations.org/projects/{quoted-id}/summary
  → upstream error mapping
  → unchanged JSON body
```

### Layer responsibilities

- **Routers:** HTTP parameters, dependencies, status codes, response models, and translation of known service failures to `HTTPException`.
- **Services:** external API calls and domain orchestration; they should not depend on FastAPI response objects.
- **Database classes:** collection access, aggregation, persistence, and query-level access control.
- **Models:** Pydantic models in the relevant domain package's `models.py`.
- **Authentication:** token verification in `common/auth/auth0.py`.
- **Authorization:** shared policy functions in `common/auth/roles.py`, or domain-specific policy modules when needed.
- **Errors:** library/service exceptions originate below the router and are mapped at the HTTP boundary. Internal detail goes to logs; safe detail goes to clients.

There is no repository-wide custom exception hierarchy. Follow the existing router mapping pattern for current tickets. A shared upstream exception hierarchy becomes useful when multiple endpoints repeat identical mappings.

## Auth0 and FastAPI architecture

### Current flow

The frontend configures `@auth0/auth0-vue` with the Auth0 domain, SPA client ID, API audience, and redirect URI. For an authenticated API request, it must obtain an access token and send:

```http
Authorization: Bearer <access-token>
```

The backend then:

1. Parses the header with `HTTPBearer(auto_error=False)`.
2. Reads the JWT `kid`.
3. Retrieves Auth0's JWKS.
4. Selects the corresponding RSA public key.
5. Verifies the signature using an explicit algorithm allowlist.
6. Validates audience, issuer, and expiration.
7. Requires a non-empty `sub`.
8. Extracts configured custom role and email claims.
9. Returns `AuthenticatedUser(sub, email, roles)`.

The JWKS implementation has one-hour caching, concurrency locking, key-rotation refresh, forced-refresh rate limiting, and a `503` response when no key set is available because the identity provider is down.

> [!warning] Frontend integration gap
> Current frontend API calls do not use `getAccessTokenSilently()` and do not attach an `Authorization` header. Route middleware proves the browser user logged in, but it does not authenticate API calls.

Suggested client-only helper:

```ts
// Suggested implementation; no equivalent helper currently exists.
export function useApiFetch() {
  const config = useRuntimeConfig()
  const { getAccessTokenSilently } = useAuth0()

  return async function apiFetch<T>(path: string, options: any = {}) {
    const token = await getAccessTokenSilently()

    return $fetch<T>(`${config.public.api_url}${path}`, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Bearer ${token}`
      }
    })
  }
}
```

Because Auth0 is installed through a client-only Nuxt plugin, SSR behavior requires an explicit design.

### Authentication versus authorization

- Authentication: Is the token valid, and who is the caller?
- Authorization: May that caller perform this operation?

Repository examples:

- `Depends(get_current_user)`: valid token required.
- `Depends(get_optional_user)`: anonymous allowed, but an invalid supplied token is still `401`.
- `Depends(require_roles("admin", "publisher"))`: valid token plus required role.
- `authorize_simulation_run_access`: public/owner/admin visibility.
- `authorize_simulation_run_mutation`: owner or admin mutation.

The repository currently enforces roles and ownership rather than OAuth scopes.

### Status codes

- `401`: missing, malformed, invalid, or expired token; wrong issuer/audience; missing subject.
- `403`: authenticated but lacks a role or mutation permission.
- `404`: sometimes deliberately used for inaccessible private resources to avoid confirming existence.
- `503`: authentication provider unavailable, so the token cannot be verified.

Relevant configuration includes `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_ISSUER`, `AUTH0_JWKS_URI`, `AUTH0_ROLES_CLAIM`, and `AUTH0_EMAIL_CLAIM`. Management API credentials must remain server-side.

## Required proxy endpoints

All listed frontend calls currently use `runtimeConfig.public.legacy_api_url`, configured as `https://api.biosimulations.org`. Except for project summary, matching top-level platform routes were not found.

| Endpoint | Current caller | Data source | Auth | Response | Recommendation |
|---|---|---|---|---|---|
| `GET /runs/{id}` | Run detail page | Legacy API | Caller sends no token; legacy requirement **Needs verification** | JSON `SimulationRun` | Proxy initially; apply platform ACL for private runs |
| `GET /runs/{id}/summary` | Run detail page | Legacy API | **Needs verification** | JSON `SimulationRunSummary` | Proxy; model after full schema verification |
| `GET /runs/{id}/download` | Runs list, output table, rerun links | Legacy API | **Needs verification** | Binary OMEX/archive; exact type **Needs verification** | Streaming proxy preserving headers |
| `DELETE /runs/{id}` | Runs list | Legacy API | Current caller sends no token | **Needs verification** | Require authentication and owner/admin authorization |
| `GET /projects/{id}/summary` | Project page | Legacy API through existing platform proxy | Public | JSON project envelope | Keep existing proxy for now |
| `GET /files/{runId}` | Project/run pages | Legacy API | **Needs verification** | JSON `ProjectFile[]` | Proxy and validate frontend model against samples |
| `GET /files/{runId}/{filePath}/download` | Thumbnails and file links | Legacy API | **Needs verification** | Binary/streaming | Stream; safely encode path segments |
| `GET /specifications/{runId}` | Project/run pages | Legacy API | **Needs verification** | JSON `SimulationRunSedDocument` | Proxy first; consider DB only after contract comparison |
| `GET /results/{runId}` | Output table with `includeData=true` | Legacy API | **Needs verification** | JSON; exact schema **Needs verification** | Proxy with allowlisted query forwarding |
| `GET /results/{runId}/download` | Output table | Legacy API | **Needs verification** | ZIP/binary | Streaming proxy |
| `GET /results/{runId}/{outputId}` | Visualization code | Legacy API | **Needs verification** | JSON with `data[].id` and `data[].values` | Proxy; encode output ID consistently |
| `GET /logs/{runId}` | Run page and output table | Legacy API | **Needs verification** | JSON; possible `output` text field | Proxy; service method already exists |
| `GET /ontologies/KISAO/{kisaoId}` | `LogAlgorithm.vue` | Legacy API | No token currently | JSON term object | Prefer local `KISAO_TERMS` if schema parity is proven |

The related `/simulations/{processing_id}/results` and `/simulations/{processing_id}/logs` endpoints are not wire-compatible replacements. They use platform processing IDs and return per-job envelopes.

## Deep focus: `GET /projects/{id}/summary`

### Verified implementation

The existing route is public, retrieves `BiosimService`, calls the configured BioSimulations API, percent-encodes the project ID, returns JSON unchanged, maps upstream errors, returns `503` when the service is unavailable, and forwards no caller credentials.

The frontend still calls `legacy_api_url`, so it has not yet switched to the platform route.

### Materialized view versus proxy

| Concern | Platform search materialization | Legacy proxy |
|---|---|---|
| Source | Derived `PlatformProjectSearch` document | Existing project-detail API |
| Contents | Slim search fields and facets | Full project and nested run summary |
| Freshness | Reindex dependent | Current legacy assembly |
| Compatibility | Cannot reproduce full schema today | Exact current frontend contract |
| Performance | Local Mongo read | Additional network call |
| Reliability | Local but potentially stale/incomplete | Subject to upstream outages |
| Maintenance | Reimplements legacy joins and semantics | Maintains transport/error behavior |
| Migration value | Strong long-term direction | Transitional |
| Testing | Mongo fixtures and parity tests | Mocked HTTP client and error tests |

The search materialization contains IDs, dates, title, abbreviated summary, model format, image URL, and facets. The frontend expects a full nested `simulationRun` with tasks, outputs, simulator/run details, metadata arrays, and timestamps.

### Recommendation

Keep the existing proxy. Do not build the detail response from `PlatformProjectSearch`; it is incomplete for this contract.

A local database implementation should wait until:

1. The complete legacy response schema is captured.
2. Every field is mapped to authoritative collections.
3. Representative parity tests prove equivalence.
4. Freshness semantics are defined.
5. The frontend no longer depends on undocumented fields.

Long term, assembling from authoritative Mongo collections may be preferable, but that should be a separate migration ticket.

## Existing project-summary structure

### Repository pattern: router

```python
@router.get("/{project_id}/summary", operation_id="get-project-summary")
async def get_project_summary(project_id: str) -> dict[str, Any]:
    biosim_service = get_biosim_service()
    if biosim_service is None:
        raise HTTPException(503, "Biosim service not available")

    try:
        return await biosim_service.get_project_summary(project_id)
    except ClientResponseError as exc:
        if exc.status == 404:
            raise HTTPException(404, f"Project not found: {project_id}")
        if 400 <= exc.status < 500:
            raise HTTPException(exc.status, f"Upstream rejected project id: HTTP {exc.status}")
        logger.warning("Upstream project summary failed ...")
        raise HTTPException(502, "Failed to fetch project summary")
    except aiohttp.ClientError:
        logger.warning("Upstream project summary unreachable ...")
        raise HTTPException(502, "Failed to fetch project summary")
```

### Repository pattern: service

```python
async def get_project_summary(self, project_id: str) -> dict[str, Any]:
    api_base_url = get_settings().biosimulations_api_base_url
    url = f"{api_base_url}/projects/{quote(project_id, safe='')}/summary"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()
```

`quote(project_id, safe="")` prevents `/`, `?`, and similar characters from restructuring the upstream URL.

There is intentionally no Pydantic response model because the complete schema has not been verified. This preserves compatibility but weakens OpenAPI documentation and validation. Add a model only after representative response capture and nullable/optional field analysis.

Existing tests cover unchanged JSON, anonymous access, no credential forwarding, 404 and other 4xx behavior, upstream failures, safe public errors, missing service, route ordering, exact upstream URL, and hostile-ID encoding. Add timeout and non-JSON upstream cases next.

## Proxy design concepts

- These routes are **application-level proxies**: they understand domain IDs, authorization, schemas, and error semantics.
- Reuse one asynchronous client for connection pooling.
- Configure explicit connect, read, write, and pool timeouts.
- Retry only safe idempotent reads and limited transient failures; do not automatically retry `DELETE`.
- Forward only explicitly needed headers such as `Accept`, `Range`, and selected conditional headers.
- Do not forward caller bearer tokens unless upstream delegation is deliberately designed.
- Forward allowlisted query parameters such as `includeData` and `thumbnail`.
- Quote identifiers used as one path segment.
- For multi-segment file paths, use `{file_path:path}`, validate traversal, and encode segments.
- Build upstream URLs only from configured base URLs to prevent SSRF.
- Stream ZIP and OMEX downloads instead of buffering them.
- Preserve safe headers such as `Content-Type`, `Content-Disposition`, `ETag`, and `Last-Modified`.
- Do not forward hop-by-hop headers such as `Connection`, `Transfer-Encoding`, or `Keep-Alive`.
- Map semantic 404s to 404; usually map upstream 5xx/connectivity to 502 and timeouts to 504.
- Never expose internal hostnames, stack traces, tokens, or raw sensitive upstream errors.
- Ensure upstream responses close when clients disconnect.
- Mock HTTP transports in CI; normal tests must not call production.

## Reusable architecture

Do not duplicate sessions, URL construction, errors, and streaming in every endpoint.

```text
backend/biosim_server/
├── biosim_runs/
│   ├── biosim_service.py       # Existing client; expand initially
│   ├── models.py               # Verified run/result/log contracts
│   └── proxy.py                # Suggested if streaming helpers grow
├── common/http/                # Suggested only when duplication appears
│   ├── client.py               # Shared client lifecycle
│   └── upstream.py             # Error/header utilities
├── runs/router.py              # Suggested legacy-compatible routes
├── files/router.py
├── results/router.py
└── ontologies/router.py
```

Do not create all packages immediately. First extend `BiosimService`, add one or two cohesive routers, and extract helpers after repeated behavior is concrete.

Useful abstractions are a BioSimulations client, shared application-lifetime HTTP client, upstream error mapper, streaming utility, and the existing Auth0/ACL dependencies. Avoid a universal proxy that accepts arbitrary URLs.

The repository currently uses both `aiohttp` and `httpx`. Standardizing eventually would help, but incremental consistency matters more than rewriting working code during an endpoint ticket.

## Testing strategy

### Unit tests

- Exact upstream URL and query parameters.
- Segment and file-path encoding.
- Timeout configuration.
- JSON parsing.
- Download chunk iteration.
- Response cleanup after completion or cancellation.

### FastAPI tests

- Status code and body.
- Authentication and authorization.
- Error translation.
- Content headers.
- Streaming bytes.

For ordinary endpoint tests, override `get_current_user` with an `AuthenticatedUser`. Test actual JWT cryptography only in `test_auth0.py`.

### Project-summary priorities

1. Successful unchanged response.
2. Anonymous access.
3. No credential forwarding.
4. 404 mapping.
5. Other upstream 4xx mapping.
6. Upstream 5xx/connection mapping.
7. URL encoding.
8. Missing service.
9. Timeout mapping.
10. Non-JSON upstream response.
11. Stored-fixture contract/parity test.

For remaining routes also test 400, 401, 403 or hidden 404, upstream timeout/failure, query preservation, binary identity, content headers, streaming behavior, client disconnects, Unicode/spaces, traversal attempts, encoded output IDs, and authorization for `DELETE`.

> [!note] Test-run status
> The focused project-summary/Auth0 tests were inspected. A fresh test run was attempted, but `uv` first required access to its external cache and subsequent runs stalled without output in the execution environment, so no fresh passing result was claimed.

## Developer learning plan

### Priority 1 — Learn immediately

- **FastAPI routing and dependencies:** required for every endpoint ticket.
- **HTTP semantics:** needed for correct 400/401/403/404/502/503/504 behavior.
- **OAuth 2.0 and Auth0 access tokens:** browser login alone does not authenticate API calls.
- **JWT validation:** audience, issuer, expiration, `kid`, JWKS, and signature verification.
- **Repository authorization:** `sub` is ownership identity; email is contact metadata.
- **Python async/await:** Mongo, HTTP, storage, and Temporal are asynchronous.
- **Async HTTP clients:** sessions, pooling, timeout, cancellation, and cleanup.
- **Testing and dependency overrides:** necessary to test proxies without production calls.

### Priority 2 — Learn next

- **Pydantic v2:** validation, aliases, optional fields, and response models.
- **MongoDB aggregation:** `$lookup`, `$facet`, `$text`, `$skip`, and `$limit`.
- **Materialized views:** why search projections cannot automatically serve full details.
- **Streaming HTTP:** needed for OMEX and ZIP downloads.
- **Temporal basics:** simulation requests are workflows rather than synchronous operations.
- **Configuration management:** Pydantic settings, Nuxt runtime config, ConfigMaps, and secrets.
- **API security:** SSRF, traversal, credential forwarding, safe errors, and log hygiene.

### Priority 3 — Deeper backend knowledge

- Materialized-data consistency and cache invalidation.
- Retry, backoff, circuit breakers, and dependency isolation.
- Structured logs, request IDs, latency/error metrics, and tracing.
- Contract testing between Nuxt, FastAPI, and the legacy API.
- API versioning and migration.
- Docker/Kubernetes lifecycle and graceful shutdown.
- Domain modularity and avoiding premature abstraction.

## Five-day practical reading order

### Day 1 — Application shape

Read `backend/CLAUDE.md`, `api/main.py`, `dependencies.py`, `config.py`, and `simulations/router.py`. Trace one request and distinguish processing IDs, internal run IDs, BioSimulations run IDs, and project IDs.

### Day 2 — Models and persistence

Read `simulations/models.py`, `simulations/database.py`, `projects/models.py`, `projects/search.py`, and `projects/database.py`. Focus on aliases, Mongo shape, ownership, visibility, source collections, and materialization.

### Day 3 — Frontend/backend contracts

Read `frontend/nuxt.config.ts`, both detail pages, `models/simulation.ts`, `FilesOutputsTable.vue`, and `useVisualizations.ts`. Record each URL, parameter, dereferenced field, and expected content type.

### Day 4 — Auth0 and authorization

Read `common/auth/auth0.py`, `common/auth/roles.py`, `rbac_demo/router.py`, `tests/common/test_auth0.py`, relevant simulation ACL tests, and the frontend Auth0 plugin/middleware. Then build a small authenticated-fetch experiment.

### Day 5 — Proxy implementation and tests

Study `projects/router.py`, `biosim_service.py`, and `test_project_summary.py`. Switch frontend URLs only in a properly scoped ticket. Add timeout/non-JSON tests, implement one JSON proxy such as logs, and then implement one streaming download before extracting shared helpers.

## Main conclusion

Preserve the current project-summary proxy because the platform's materialized project search view cannot reproduce the frontend's complete legacy detail contract. For protected proxy operations, the next prerequisite is frontend access-token propagation and consistent reuse of the backend's existing authorization policies—not a second JWT implementation.
