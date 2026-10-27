> **New feature request as of 07/16/2027 (low priority)**:
> Add support for showing a simulation run's project (if it belongs to one), so that a column can be filtered on and displayed.
> Project names would then need to be sortable and filterable like everything else.

> **Status (updated 2026-08-25): listing API is live and consumed by the
> frontend Browse Simulations page** (`frontend/app/pages/simulations/index.vue`
> → `POST /simulations/runs`). Older notes below that mention mock `setTimeout`
> data or “real auth is not wired” are stale.
>
> `POST /simulations/runs` lives in `backend/biosim_server/simulations/` and
> returns `{ runs: SimulationRun[], pagination: { page, perPage, _total } }`.
> Run records are persisted (collection `BiosimSimulationRuns`) on
> `POST /simulations/run` and updated to `SUCCEEDED`/`FAILED` by
> `SimulationRunWorkflow`.
>
> **Auth / listing contract:**
> - `type: all` is public. `email` is redacted unless the caller is the owner
>   or an admin.
> - `type: user` requires an access token. Non-admins are scoped to token
>   `sub` (`owner_sub`); a verified-email match is only a fallback for legacy
>   rows that have no `owner_sub`. Client-supplied `user` / `owner_sub` in the
>   body are not trusted for non-admins.
> - `perPage` is capped at 100.
> - Sort/filter allowlist includes `biosimulationsRunId`. Date filter values
>   must be ISO-8601 strings (or datetime); other shapes return 400.
>
> **Identifier trinity** (do not conflate these):
> - `id` — platform per-simulator `run_id` (uuid4 hex)
> - `biosimulationsRunId` — biosimulations.org ObjectId (legacy detail/export)
> - `processingId` / path `{id}` on `GET /simulations/{id}` — Temporal workflow id
>
> **Deferred** (no source yet — returned as zero/empty): `cpus`, `memory`, `maxTime`,
> `envVars`, `purpose`, `runtime`, `projectSize`, `resultsSize`.
>
> **Frontend:** Browse Simulations listing is wired to this API. Run
> detail/delete/export/viz still use the legacy `api.biosimulations.org` API,
> keyed by `biosimulationsRunId`. The SPA does not yet attach Auth0 access
> tokens; “My Runs” and owned-run mutations need that separately. Existing
> UI-created rows stay `owner_sub = NULL` until a backfill is approved.

## To-Do:

### Summary:
* Create `POST /simulations/runs` (or more semantically applicable verb given POST body's nature containing table filtration, sort, and pagination parameters) page, whose purpose is to show all or user-submitted simulations (linked via user-provided e-mail address)
___

Wishlist for aforementioned "*Create `POST /simulations/runs`*" task:
* Distinguish between user-submitted and anonymously submitted data (auth)
* Server-side functions:
  * Data return (based on who owns it and whether the request specifies "show MY data" or "show ALL data"
  * Pagination
  * Sorting
  * Filtering
  * Returns data as an array of `SimulationRun` objects, as detailed below:
```ts
export interface SimulationRun {
  id: string // uuid
  name: string
  simulator: string
  simulatorVersion: string
  simulatorDigest: string
  cpus: number
  memory: number
  maxTime: number
  envVars: string[]
  purpose: string
  email: string
  status: string
  runtime: number
  projectSize: number
  resultsSize: number
  submitted: string
  updated: string
}
```
With the following POST body:
```ts
{
    "type": fetch_user.value ? 'user' : 'all', 
    "user": fetch_user.value ? user_email.value : undefined,
    "sort": table_sort.value, // typeof TableSort
    "filters": valid_filters, // typeof TableFilter[]
    "pagination": table_pagination.value // typeof TablePagination
}
```

Based on the following classes:
```ts
export interface TableSort {
  id: string | undefined
  direction: 'asc' | 'desc' | undefined
}

export interface TableFilter {
  id?: string
  operator?: 'starts_with' | 'ends_with' | 'contains' | 'less_than' | 'equal' | 'greater_than' | 'before' | 'after' | 'on' | 'is_any'
  value?: any | any[]

  _filterType?: 'text' | 'date' | 'number' | 'boolean' | 'enum'
  _filterOptions?: any[]
}

export interface TablePagination {
  page: number
  perPage: number

  _total?: number
}
```

All in a new endpoint in the https://api.biosim.biosimulations.org API environment
