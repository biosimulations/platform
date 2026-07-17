# Project search API — frontend integration handoff (contract freeze)

The backend project search API is **done and on `main`** (PRs #61 + follow-ups).
This is the contract for wiring `frontend/app/pages/biosim-db.vue` off the legacy
biosimulations.org API onto the platform backend, and dropping the per-project
`/files` N+1. Owner coordination: backend = Jim, this page = Harrison.

## Endpoints (platform backend — `runtimeConfig.public.api_url`, i.e. `NUXT_PUBLIC_API_URL`)

Replace the two legacy calls (`legacy_api_url/projects/summary_filtered` and the
per-project `legacy_api_url/files/{runId}`) with:

### `GET /projects` — paginated results
Query params:
| param | type | notes |
|---|---|---|
| `page` | number | **1-indexed** |
| `perPage` | number | ≤ 200 |
| `filters` | string | JSON array of `{target, allowable_values}` (`ProjectSearchFilter[]`) |
| `searchTerm` | string | free text over title/abstract/description |

Response: `{ items: ProjectStub[], total: number }` where `ProjectStub` is
already the frontend's shape — **`image_url` and `model_format` come populated**,
so no `/files` call and no `Promise.all` per row:
```ts
interface ProjectStub {
  id: string            // NOTE: string (project ids are slugs), not number
  simulationRun: string
  created: string
  updated: string
  name: string
  summary: string
  model_format: string
  image_url: string | null
}
```

### `GET /projects/stats` — facet counts
Query params: `filters` (JSON, same shape) and `searchTerm`.
Response: `ProjectQueryStat[]` — `{ target, valueFrequencies: {value, count}[] }`
(camelCase `valueFrequencies`, already what `query_stats_to_filter_groups` expects).

## Three gotchas to get right

1. **Pagination is 1-indexed.** `biosim-db.vue`'s `table_pagination.page` starts at
   `0`. Send `page: table_pagination.value.page + 1` (and map back on the way in if
   the pager stays 0-based). The old API took a 0-indexed `pageIndex`.
2. **`ProjectStub.id` is a string.** `frontend/app/models/projects.ts` currently
   types it `number`; change to `string`. Column renderers and `visit_page`
   (`/projects/${row.id}`) already work with strings.
3. **Keep the facet menu stable — don't pass the active facet filters to
   `/projects/stats`.** Send only `searchTerm` (omit `filters`), so selecting a
   taxa value doesn't collapse the taxa menu to just that value. This matches the
   legacy `fullFilterStats` behavior. (`/projects` still gets the full `filters`.)

## Sketch of the new `fetch_projects()`

```ts
const populated = searched_filters.value.filter(f => f.allowable_values.length > 0)
const base = runtimeConfig.public.api_url

const { items, total } = await $fetch(`${base}/projects`, { query: {
  page: table_pagination.value.page + 1,
  perPage: table_pagination.value.perPage,
  filters: JSON.stringify(populated),
  searchTerm: fuzzy_search_term.value,
}}) as ProjectStubPage

const stats = await $fetch(`${base}/projects/stats`, { query: {
  searchTerm: fuzzy_search_term.value,   // NOT the structured filters — see gotcha #3
}}) as ProjectQueryStat[]

projects.value = items                    // ProjectStub[] maps 1:1; image_url already set
total_results.value = total
filter_suggestions.value = query_stats_to_filter_groups(stats)
```
Add `interface ProjectStubPage { items: ProjectStub[]; total: number }` to
`models/projects.ts`; the legacy `Projects` / `ProjectSummary` types can go once
nothing references them.

## Not yet handled (flag for the demo)
- **Deployed backend to point at.** These endpoints work locally against the
  hosted Mongo (set `MONGODB_URI` per `backend/.env.example`) but aren't deployed
  to a shared dev URL yet — needed for a non-local demo. See the plan's D9.
- Perf indexes on the hosted collections are a **shared-DB change** (affects the
  biosimulations service) — deliberately not done unilaterally.
