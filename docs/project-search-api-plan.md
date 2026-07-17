# Project Search API — two-week plan (→ CRBM demo 2026-07-30)

Milestone: demo the **project page + detail pages** at the CRBM group meeting on
**Wed 2026-07-30**. This plan covers the backend **project search API** (Jim);
Harrison owns the detail pages against the contract below.

Source of the ask: `docs/biosim-db-api-wishlist-and-fixes.md`.

---

## Where we are today

- The frontend `frontend/app/pages/biosim-db.vue` calls the **legacy external API**
  (`LEGACY_API_URL` → `api.biosimulations.org`) at `GET /projects/summary_filtered`,
  then makes a **per-project** `GET /files/{simulationRunId}` call to find a `.png`
  thumbnail. So one page render = 1 + N HTTP calls to a service we don't control.
- The platform backend has **no** projects module yet. It does have the pieces we
  need: `motor` async Mongo (`dependencies.py`), a `simulations/` module to mirror
  (router / models / database), and config for Mongo + `biosimulations_api_base_url`.

### How the legacy endpoint actually works (reference: `~/workspace/biosimulations`)
`apps/api/src/projects/` —
- `Projects` collection is thin: `{ id, simulationRun, owner, created, updated }`
  (`project.model.ts`, collection `'Projects'`).
- The rich summary (title, abstract, taxa, simulators, model format, reports,
  keywords, thumbnail) is assembled by **joining each project to its SimulationRun
  + metadata** (`_getProjectSummaries` → `getProjectSummary`), cached in memory.
- Search is **in-memory elasticlunr** over that cached set (`projects.search.ts`),
  not a Mongo query. Facet counts (`queryStats`) are computed in JS over the whole
  set (`projects.filter.ts` → `gatherFilterValueStatistics`).
- **Pagination bug root cause:** `getProjectSummariesWithoutSearch` loads a large
  fixed slice (`maxNumRecordsToTextSearch`, page 0), sorts, then re-slices by
  `pageIndex*pageSize`. Total-count and slice semantics differ between the
  no-filter / filter / search branches, and the frontend sends a **1-indexed**
  `pageIndex` where the API assumes **0-indexed** — past page 1 the window and the
  reported total disagree, so "too many results" leak through.

---

## Day 1 outcome (2026-07-16) — data source decided: GO on direct Mongo

Probed the live cluster from inside a running `api` pod (no credential exposure).
**The platform's existing `MONGODB_URI` already reaches the data.** In
`biosimulations-prod`:
- `Projects` — 1392 docs, thin schema `{id, simulationRun, owner, created, updated}`.
- `projectSummary` — **527 docs, but a DEAD ORPHAN.** Strict subset of `Projects`;
  every `updated` falls in a frozen window **2022-02-05 .. 2022-06-10**. The 865
  projects it lacks were all created 2023+. No current code writes it — the
  biosimulations API service injects only `ProjectModel` (`Projects`) and builds
  summaries from an **in-memory** cache; the platform and sibling tools never
  reference it. It was populated by a materialization job that stopped ~June 2022
  and was abandoned when the service moved to in-memory caching. **Do not depend
  on it, and do not backfill it.**
- `Metadata` — **272,849 docs, per-run, keyed by `simulationRun`**, same nested
  `metadata[0]` shape (title/abstract/thumbnails/taxa/keywords/…). **200/200**
  sampled projects that `projectSummary` misses DO have a `Metadata` doc. This is
  the live, complete source: join `Projects` → `Metadata` on `simulationRun`.
- Also present: `Metadata`, `Specifications`, `Files`, `Simulation Runs` (note the
  space), `BiosimSimulationRuns`.

Consequences:
- **No new sealed secret needed for deployed read access** — same cluster/DB the
  platform already uses. (The `../deployment` / `../secrets` creds matter for the
  *local-dev* story instead — see the secrets-refactor follow-up.)
- **Coverage gap to resolve:** `projectSummary` covers 527 of 1392 projects (likely
  published-only or a warm cache). Confirm the intended denominator; decide whether
  to compute-on-miss (legacy live join) or treat `projectSummary` as authoritative
  for the demo.
- `model_format` and `image_url` are **not** in the metadata — now joined in:
  `model_format` from `Specifications.models[].language` (SED-ML URN → acronym)
  via a page-scoped `$lookup`; `image_url` from `metadata.thumbnails[0]` built as
  `{api}/files/{runId}/{file}/download/?thumbnail=browse` (verified 200 live). Both
  degrade cleanly when absent.

**Scaffold landed** (`backend/biosim_server/projects/`): `models.py`,
`database.py` (async `ProjectDatabaseService` ABC + Mongo impl reading
`projectSummary`), `router.py` (`GET /projects`, `GET /projects/stats`), wired
into `config.py`, `dependencies.py`, `api/main.py`. 15 tests (pure + endpoint +
testcontainers pagination) green; ruff + mypy clean. Pagination is 1-indexed with
a real `count_documents` — the legacy bug is fixed by construction.

## The one decision that gated the plan: data source

**Recommendation — read the hosted Mongo directly (read-only), don't proxy the
legacy HTTP API.** Rationale: the wishlist wants a *slim, fast, decoupled*
endpoint; proxying inherits the legacy latency and the N+1. We already speak
`motor`. Direct Mongo lets us do real `skip/limit/count` pagination and (later)
`$facet` aggregation for the stats.

Open questions to resolve **Day 1** (these are the schedule risk):
1. Is the hosted `Projects` + `SimulationRuns` Mongo reachable from our backend
   (network + read-only credentials)? If not, fall back to **proxy-and-reshape**
   the legacy endpoint for the demo, and defer direct DB to post-demo.
2. Do we replicate the summary-assembly join, or is there a materialized summary we
   can read? (Legacy assembles it live from SimulationRun metadata.)
3. Thumbnail: can we resolve `image_url` from SimulationRun metadata server-side so
   the frontend's per-project `/files` call goes away? (Design goal: yes.)

---

## Target contract (from the wishlist)

Split the one endpoint into two, so the heavy facet computation is decoupled from
paging through results.

**`GET /projects` — slim, paginated results**
- query: `page`, `perPage`, `filters` (JSON `ValueFrequency[]`), `searchTerm`
- returns: `{ items: ProjectStub[], total: number }`
```ts
interface ProjectStub {
  id: number; simulationRun: string; created: string; updated: string;
  name: string; summary: string; model_format: string; image_url?: string;
}
```

**`GET /projects/stats` (facets) — tags & categories**
- query: same shape (`page, perPage, filters, searchTerm`) so counts can reflect
  the active query
- returns: `ProjectQueryStat[]` = `{ target, valueFrequencies: {value,count}[] }`

Keep field names aligned with `frontend/app/models/projects.ts` so Harrison's types
don't drift.

---

## Two-week breakdown

### Week 1 (07-16 → 07-22): make it real
- **D1 — de-risk the data source.** Answer the 3 questions above; confirm Mongo
  reachability + creds, or commit to the proxy fallback. *This is the go/no-go for
  direct-DB.* Everything below is written to work either way behind a service iface.
- **D1–2 — scaffold the module.** `backend/biosim_server/projects/` mirroring
  `simulations/`: `models.py` (ProjectStub, ProjectQueryStat, ValueFrequency),
  `database.py` (ProjectDatabaseService iface + Mongo impl), `router.py`, wire into
  `api/main.py` + `dependencies.py`. Add `mongodb_collection_projects` to config.
- **D2–3 — `GET /projects` happy path** with **correct** server-side pagination
  (real `count` + `skip/limit`, 0-indexed, documented). Ship the pagination fix
  by construction. Unit tests for page boundaries (the legacy bug).
- **D3–4 — summary assembly + `image_url`.** Join to SimulationRun metadata; resolve
  thumbnail server-side; kill the frontend N+1.
- **D5 — `GET /projects/stats`** facet counts (start in app code mirroring
  `gatherFilterValueStatistics`; optimize to `$facet` later). Filters applied.

### Week 2 (07-23 → 07-29): correctness, search, integration
- **D6 — `searchTerm`.** Simplest correct thing first: Mongo text/regex over
  title+abstract+model language. (elasticlunr parity is a post-demo nice-to-have.)
- **D6–7 — filters × search interaction**, and make `stats` reflect the active
  query. Table-driven tests over filter/search/paging combinations.
- **D7–8 — frontend integration with Harrison.** Point `biosim-db.vue` at the new
  endpoints behind a flag; drop the per-project `/files` call. Verify the detail
  pages get what they need. **Freeze the contract here.**
- **D8–9 — perf + caching.** Whatever keeps the demo snappy: cache facet stats,
  index the queried fields. Load-check against realistic project counts.
- **D9 — buffer / polish / deploy** a backend Harrison can point at. Dry-run the
  demo path end-to-end.

Demo **07-30**.

---

## Risks
- **Mongo access (highest).** If creds/networking slip, fall back to proxy-reshape
  for the demo; direct-DB becomes post-demo. Decide D1, don't let it float.
- **Summary-assembly cost.** The live join is why legacy is slow. If assembling on
  our side is too slow, cache aggressively for the demo and optimize after.
- **Contract churn vs Harrison.** Freeze names by D7–8; the wishlist `ProjectStub`
  is the anchor. Every day of drift is a day off his detail-page work.
- **biosim-client impact.** New/changed endpoints may affect the external client —
  check before opening the PR.

## Explicitly out of scope for the demo
elasticlunr-quality relevance ranking, write/publish endpoints, auth on the new
endpoints (read-only public data), full `$facet` optimization.
