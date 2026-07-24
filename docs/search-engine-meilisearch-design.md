# Project search — phased design (Mongo `$text` now, Meilisearch later)

Two phases:

- **Phase 1 — MongoDB `$text`.** Real stemmed, multi-term, relevance-*ranked*
  search using Mongo's built-in full-text, shippable soon with no new
  infrastructure. Replaces today's unranked substring+recency match.
- **Phase 2 — self-hosted Meilisearch.** A DB-agnostic search engine in the
  cluster that (a) removes the dependency on Atlas so we can **migrate off Atlas**,
  and (b) serves a **second, non-Mongo project** from the same engine. Adds
  typo-tolerance/fuzzy and facets-from-the-engine; needs an indexer + a deploy.

Phase 1 buys ranked search immediately; Phase 2 is the durable answer. Crucially,
**Phase 1's index-builder is Phase 2's indexer** if we take the materialized-
collection option below — so Phase 1 is a stepping stone, not throwaway.

---

## Phase 1 — MongoDB `$text`

### What it gives
`$text` provides stemming (`running` ~ `run`), multi-term OR/AND, and a relevance
score (`$meta: "textScore"`) to sort by. Not typo-tolerant (that's Phase 2), but a
real ranked retrieval instead of the current boolean substring + `updated`-desc.

### The constraint that shapes it (from probing prod)
- **No text index exists** on `Metadata`, `Projects`, or `Specifications` today.
- The searchable text (`title`/`abstract`/`description`) lives in **`Metadata`**
  (nested `metadata[0]`), **not** in the thin `Projects` collection.
- `$text` requires a text index **on the collection being queried** and must be
  the **first** aggregation stage.

⇒ Search must be **`Metadata`-rooted** (today's read path is `Projects`-rooted).
So `query_project_stubs` branches:
- **no `searchTerm`** → current `Projects`-rooted pipeline, sorted by `updated`.
- **`searchTerm`** → `Metadata`-rooted pipeline:
  1. `$match {$text: {$search: term}}` (uses the text index),
  2. `$addFields` `textScore` + `_meta0` (= `metadata[0]`),
  3. `$match` structured filters on `_meta0` (reuses existing filter clauses),
  4. `$lookup Projects` on `simulationRun` + `$match` non-empty — **inner join to
     keep only published projects** (Metadata has 272k per-run docs; only ~1392
     are projects),
  5. `$facet`: items `[sort by textScore, skip, limit, $lookup Specifications for
     model_format, project → ProjectStub]`, total `[$count]`.
`/projects/stats` uses the **same match set** (Metadata-rooted `$text` + filters)
so facet counts agree with the results. Local dev / no-index falls back to the
current regex substring match, so it degrades gracefully.

### Where the text index lives — the one decision
`$text` needs a text index somewhere. Two options:

- **1A — index the shared `biosimulations-prod.Metadata`.** Fastest to stand up
  (one `createIndex`). Downsides: it's a **write to a biosimulations-owned, live
  collection** (breaks the read-only decoupling we've kept), and it indexes all
  **272k** run-metadata docs when only ~1392 are projects. Reversible (drop it),
  online build on Atlas.
- **1B — platform-owned materialized `project_search` collection (recommended).**
  A small (~1392-doc) collection *we own*, holding the enriched `ProjectStub`
  documents, built by **reading** Projects+Metadata+Specifications (the exact
  aggregation we already have) — no writes to biosimulations collections. We put
  the text index on *our* collection (tiny, ours to manage). This is precisely the
  **indexer → index** shape of Phase 2, so the Phase-1 builder becomes the Phase-2
  Meilisearch indexer. Downside: a build/refresh step (CronJob or Temporal), and a
  short staleness window.

1B aligns with the migrate-off-Atlas goal and makes Phase 1 a stepping stone; 1A
is faster but couples us harder to the shared DB. **Recommendation: 1B.**

### Config / wiring
Either way the query lives behind the existing `ProjectDatabaseService` interface
(swap-in impl, no router change). 1B adds a `mongodb_collection_project_search`
setting + a refresh entrypoint; 1A adds a text-index name only.

### Operations (1B, implemented)
The materialized collection is a point-in-time snapshot, so it needs refreshing —
though the source `Projects` collection has been static for 17+ months, so this
is a safety net more than a live need.
- **Weekly reindex CronJob** (`kustomize/base/reindex-cronjob.yaml`) runs
  `python -m biosim_server.projects.reindex_cli` (direct Mongo, no HTTP) in the
  api image. It also absorbs any enrichment/index-logic change shipped since the
  last run, so deploys don't require a manual reindex.
- **`POST /projects/reindex` is token-gated** (`project_reindex_token`; empty
  default = disabled/503) so it can't be triggered over the public ingress.
  Ad-hoc admin reindex: `kubectl exec … python -m biosim_server.projects.reindex_cli`.
- Rebuild is non-atomic (delete + insert ~1392 docs, ~1-2s window); acceptable
  given how rarely it runs. Build-to-temp + swap is a future refinement.

---

## Phase 2 — self-hosted Meilisearch

Goal: a **DB-agnostic, self-hosted** search engine in our Kubernetes cluster that
(a) gives real ranked + typo-tolerant search for the biosimulations project DB
without depending on Atlas Search, so we can **migrate off Atlas** later, and
(b) serves a **second, non-Mongo project** from the same engine.

## Why Meilisearch fits both goals

- **Source-agnostic.** Meilisearch is a standalone engine: you *push* JSON
  documents to it; it doesn't care where they came from (Mongo now, Postgres or
  files later, or the other project's datastore). The search layer stops being
  coupled to the database — which is exactly the "migrate off Atlas" lever.
- **Batteries included.** Typo-tolerance (fuzzy), prefix/as-you-type, relevance
  ranking, **filtering and faceting** — all built in. For our project search it
  can replace *both* the ranked search *and* the `/projects/stats` facet counts.
- **Cheap to run.** Single Go binary / one container, one PVC. Much lighter ops
  than Elasticsearch/OpenSearch; comparable to Typesense.
- **Multi-tenant by index.** One instance holds many indexes (like tables) with
  per-index scoped API keys — so the biosimulations `projects` index and the
  other project's index live side by side, isolated by key.

Main tradeoff to accept up front: **Meilisearch OSS is single-node** (no native
clustering/HA). For a research platform that's usually fine — run one instance
with a PVC and periodic dumps; restore is fast. If you later need HA, that's the
point you'd consider Meilisearch Cloud or a second replica behind a rebuild-on-
restart indexer.

## Architecture: indexer + query, DB decoupled

```
   source of truth                 Meilisearch (k8s)              consumers
 ┌────────────────┐   push docs   ┌──────────────────┐   HTTP    ┌──────────────┐
 │ biosimulations │ ────────────▶ │ index: projects  │ ◀──────── │ platform API │
 │ Mongo (Atlas   │   (indexer)   │  - searchable     │  /search  │ /projects,   │
 │  → later: any) │               │  - filterable     │           │ /projects/stats
 └────────────────┘               │  - facets         │           └──────────────┘
 ┌────────────────┐               ├──────────────────┤   HTTP    ┌──────────────┐
 │ other project  │ ────────────▶ │ index: <other>   │ ◀──────── │ other project│
 │ (non-Mongo)    │   (indexer)   └──────────────────┘           └──────────────┘
```

Two moving parts per project:

1. **Indexer (write path).** A job that reads the source of truth, shapes each
   record into a flat search document, and pushes batches to Meilisearch. For
   biosimulations the document is the `ProjectStub` plus the facet fields
   (`taxa`, `keywords`, `model_format`) and the searchable text
   (`title`, `abstract`, `description`). Options for *when* it runs:
   - **Backfill + CronJob** — full/periodic re-sync (simplest; fine at 1392 docs).
   - **Temporal workflow** — we already run Temporal; a `ReindexProjectsWorkflow`
     (full) + an on-publish activity (incremental) fits the existing stack.
   The indexer is the *only* thing that knows about Mongo/Atlas — swap its source
   when the DB migrates and nothing downstream changes.

2. **Query (read path).** The platform API calls Meilisearch's HTTP API
   (`POST /indexes/projects/search`) with the term, filters, page, and
   `facets: [...]`. Meilisearch returns ranked hits **and** facet distributions in
   one response — so a single call backs both `/projects` (hits + `estimatedTotalHits`)
   and `/projects/stats` (facet counts). Because the stored document already *is*
   the `ProjectStub`, no DB round-trip is needed to render results.

This also collapses our current two-collection `$lookup` aggregation + in-app
facet scan into "ask Meilisearch" — the join happens once, at index time.

## Kubernetes deployment (matches our kustomize conventions)

Mirror the `mongodb` pattern: a Deployment + Service in `kustomize/base/`, the PVC
added per overlay, the master key as a sealed secret (the flow we just built).

- **`kustomize/base/meilisearch/`** (new sub-package, add to `base/kustomization.yaml`):
  - **Deployment** — `getmeili/meilisearch:v1.x` (pin), one replica,
    `containerPort: 7700`, env `MEILI_ENV=production`,
    `MEILI_MASTER_KEY` from the sealed secret, `MEILI_DB_PATH=/meili_data`.
    Requests/limits sized to the corpus (start ~512Mi/1Gi; project DB is tiny,
    the other project drives real sizing). Liveness/readiness on `GET /health`.
  - **Service** — `ClusterIP` on 7700, in-cluster only. Never exposed publicly;
    the frontend never talks to Meilisearch directly (see keys below).
  - **PVC** at `/meili_data` — Meilisearch persists its index to disk, so this is
    required (per-overlay, like `biosim-local/mongodb-pvc.yaml`). Back up with
    periodic dumps (`POST /dumps`) to GCS.
- **Secret** — `MEILI_MASTER_KEY` via `sealed_secret_*` + the per-overlay
  `secrets.sh`/`secrets.dat` we just added. Master key is admin-only.
- **Scoped API keys** (created once via the API, not the master key):
  - a **search-only** key per index for read callers,
  - an **admin/index** key for that project's indexer.
  Each project's key is scoped to its own index → isolation on a shared instance.

Namespace: deploy into the app namespace (`biosim-gke`/`biosim-rke`) as another
in-cluster service, or a shared `search` namespace if the second project lives
elsewhere and you want one instance serving both across namespaces (Service DNS
`meilisearch.search.svc.cluster.local`).

## How it slots into the current backend

- Add a `ProjectSearchService` (or a Meilisearch-backed `ProjectDatabaseService`
  impl) behind the **same interface** the router already uses
  (`query_project_stubs`, `query_project_stats`). Swapping the Mongo-aggregation
  impl for the Meilisearch impl is a dependency-wiring change, not a router change.
- Keep the Mongo aggregation we built as the **indexer's read source** (it already
  produces exactly the enriched `ProjectStub` + facets) — so nothing is wasted:
  today's read path becomes tomorrow's index-build path.
- Config: `MEILISEARCH_URL` (in-cluster Service), `MEILISEARCH_API_KEY` (search
  key) added to `config.py`; the indexer gets the admin key.

## Open questions before building

- **The other project's stack** — its datastore + document shape decide whether
  one shared instance (multi-index) or a dedicated instance per project is better.
- **HA appetite** — single-node OSS acceptable for the demo/near-term? (Recommend
  yes; revisit if it becomes user-facing critical.)
- **Sync trigger** — CronJob full re-sync vs. Temporal on-publish incremental.
  Full re-sync is fine to start given the corpus size.
- **Consistency window** — the index lags the DB by the sync interval; acceptable
  for a browse/search page. Publish-time reindex closes the gap if needed.

## Sequencing vs the 07-30 demo

- **Phase 1 (`$text`)** is the near-term target — ranked search with no new infra.
  Doable before the demo if we settle the index-location decision (1A vs 1B).
- **Phase 2 (Meilisearch)** is the **post-demo** durable track (Atlas exit +
  second project); it adds an indexer + deploy that isn't on the demo critical
  path.
- If Phase 1 slips, the substring+recency search already on `main` still shows the
  page working for the demo.
