> **Status (2026-05-29): backend implemented.** `POST /simulations/runs` is live in
> `backend/biosim_server/simulations/` — owner-scope (`type: all|user` + `user` email),
> pagination, sorting, and filtering, returning `{ runs: SimulationRun[], pagination: { page, perPage, _total } }`.
> Run records are persisted (collection `BiosimSimulationRuns`) on `POST /simulations/run`
> and updated to `SUCCEEDED`/`FAILED` by `SimulationRunWorkflow`.
>
> **Deferred** (no source yet — returned as zero/empty): `cpus`, `memory`, `maxTime`,
> `envVars`, `purpose`, `runtime`, `projectSize`, `resultsSize`. Real auth is not wired —
> owner-scoping trusts the supplied email. **Frontend follow-up:** wire
> `frontend/app/pages/simulations/index.vue` (currently mocked) to this endpoint;
> read `runs` / `pagination._total` from the response.

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
