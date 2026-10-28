# CLAUDE.md - Platform Frontend Development Guide

This guide covers the webapp. For monorepo orientation (backend, kustomize, deployment), see the root `CLAUDE.md`. For the integrated local-dev workflow (Mongo + Temporal in containers, backend + frontend native), see the **Local development** section of the root `README.md`.

All commands below assume the working directory is `frontend/` (i.e., `cd frontend` from the repo root first).

## Project Overview

The platform frontend is the [biosimulations.org](https://biosim.biosimulations.org) webapp — a Nuxt 4 SSR app that lets users upload OMEX archives, run simulations against the FastAPI backend, browse the biosim project DB, and consume related utilities.

**Stack:** Nuxt 4 (Vue 3, Nitro SSR), Nuxt UI 4, `@nuxtjs/seo`, `nuxt-aos`, `nuxt-lottie`, `lenis`, Zod
**Node:** 22 (use `nvm install 22 && nvm use 22` if your default is older — several deps require Node ≥ 22)
**Package manager:** npm (`package-lock.json` is authoritative; `pnpm-lock.yaml` removed)
**Dev port:** 4200

## Quick Commands

```bash
# Install dependencies
npm install

# Run dev server (http://localhost:4200, HMR, devtools)
npm run dev

# Lint
npm run lint

# Type check
npm run typecheck

# Production build
npm run build

# Preview production build
npm run preview
```

## Verification

Run these on every changeset before considering work complete:

```bash
npm run lint
npm run typecheck
npm run test:auth
npm run test:verification
npm run test:legacy-proxies
```

CI runs these checks on every PR and push to main (see `.github/workflows/frontend-ci.yaml`). Verify UI changes manually in `npm run dev`.

## Directory Structure

```
frontend/
├── app/
│   ├── app.vue                 # Root layout: header, nav, footer
│   ├── app.config.ts           # Nuxt UI theme (primary=blue, neutral=slate)
│   ├── assets/css/main.css     # Global styles (Tailwind via Nuxt UI)
│   ├── components/             # Reusable Vue components
│   │   ├── AppLogo.vue
│   │   ├── Loading.vue
│   │   └── TemplateMenu.vue
│   ├── composables/
│   │   └── useRafBus.ts        # requestAnimationFrame bus
│   ├── functions/
│   │   └── functions.ts        # Misc helpers
│   ├── models/                 # TypeScript types
│   │   ├── create-project.ts
│   │   └── simulators.ts
│   └── pages/                  # File-based routing
│       ├── index.vue           # Landing page
│       ├── error.vue
│       ├── biosim-db.vue       # Browse biosimulations.org project DB
│       ├── simulations/
│       │   ├── index.vue                       # List runs
│       │   ├── run.vue                         # Submit a new simulation (calls /simulations/run)
│       │   ├── validate.vue
│       │   ├── [id].vue                        # Run detail
│       │   └── check-status/[processing_id].vue  # Poll run status
│       ├── simulators/         # get-started, validate, suggest, index
│       └── utilities/          # validate-model, validate-metadata, etc.
├── server/
│   ├── routes/biosimulators-api/[...path].get.ts # Allowlisted GET proxy
│   ├── routes/biosimulations-api/runs/[id].get.ts # Uncached run record GET
│   ├── routes/simdata-api/datasets/[id]/metadata.get.ts # Uncached metadata GET
│   ├── utils/upstream-passthrough.ts           # Fetch and relay without credentials
│   ├── utils/legacy-run-id.ts                 # Single-segment run ID validation
│   └── utils/legacy-simulators-proxy.ts         # Path rules and cache eligibility
├── public/                     # Static assets (images, fonts, lottie)
├── nuxt.config.ts
├── package.json
├── package-lock.json           # Authoritative lockfile (npm)
├── tsconfig.json
├── eslint.config.mjs
└── tests/                     # Auth, verification, and simulator proxy unit tests
```

## Runtime Config

Defined in `nuxt.config.ts` → `runtimeConfig`. Public values live under `runtimeConfig.public` and are read in components via `useRuntimeConfig().public`.

Use Nuxt's `NUXT_PUBLIC_<KEY>` (public) and `NUXT_<KEY>` (server-only) env-var naming — those are the names Nuxt looks for at process startup to override the build-time `runtimeConfig` values. Bare names (`API_URL`, etc.) are only picked up at *build time* and are too early for a runtime-configured container image.

| Variable                          | Side             | Purpose |
|-----------------------------------|------------------|---------|
| `NUXT_LEGACY_RUNS_API_URL` | server only | Run-precheck proxy upstream; defaults to `https://api.biosimulations.org`. |
| `NUXT_SIMDATA_API_URL` | server only | Run-precheck dataset metadata upstream; defaults to `https://simdata.api.biosimulations.org`. |
| `NUXT_LEGACY_SIMULATORS_API_URL` | server only | Legacy simulator proxy upstream; defaults to `https://api.biosimulators.org`. |
| `NUXT_PUBLIC_LEGACY_SIMULATORS_API_URL` | browser + server | Defaults to `/biosimulators-api`. Pointing it at the upstream breaks CORS on deployed hosts. |
| `NUXT_PUBLIC_BASE_URL`            | browser + server | Public origin the app is served from (used by `@nuxtjs/seo`) |
| `NUXT_PUBLIC_API_URL`             | browser + server | Platform backend API base URL — points at the FastAPI service in `../backend/` |
| `NUXT_API_URL`                    | server only      | In-cluster URL Nitro uses for SSR-time fetches to the backend (e.g., `http://api:8000`). Skips the public ingress when the frontend is deployed alongside `api`. Read via `useRuntimeConfig().apiUrl` from server-side code (`import.meta.server`); not exposed to the browser. |
| `NUXT_PUBLIC_BIOSIMULATIONS_API_URL` | browser + server | Public biosimulations.org project DB API base URL (different service from the platform backend) — used by `pages/biosim-db.vue` |

Local dev: copy `frontend/.env.example` → `frontend/.env` (or let `scripts/dev-up.sh` seed it). Loaded by Nuxt's built-in dotenv at dev-server startup. Deployed: the corresponding ConfigMap (`kustomize/config/<overlay>/frontend.env`) supplies the same names.

## Backend API Integration

Backend endpoints consumed (see `../backend/CLAUDE.md` for the full list):

| Page | Calls |
|------|-------|
| `pages/simulations/run.vue` | `POST /compatibility/check`, `POST /simulations/run` (optional `cache_buster`) |
| `pages/simulations/check-status/[processing_id].vue` | `GET /simulations/{processing_id}` (workflow-query + DB-fallback hybrid, since PR #53) |
| `pages/simulations/index.vue` | `POST /simulations/runs` — **not wired on `main`** (renders hardcoded mock data via `setTimeout`); partial wire-up exists on remote branch `origin/feature/runs-page` with a known response-shape bug. See `docs/simulation-runs-api-plan.md` for the status. |
| `pages/biosim-db.vue` | `GET /projects` (against `runtimeConfig.public.biosimulations_api_url` — the public biosimulations.org project DB API, not the platform backend) |

## Key Patterns

- **File-based routing** — pages under `app/pages/` become routes; `[param]` segments are dynamic.
- **Data fetching** — uses Nuxt's `$fetch` (Ofetch). Prefer `useFetch`/`useAsyncData` for SSR-friendly fetches; `$fetch` is fine for client-only event handlers.
- **Styling** — Nuxt UI 4 components + Tailwind utility classes. Theme tokens (`primary`, `neutral`) configured in `app.config.ts`.
- **Icons** — `@iconify-json/*` packages (Lucide, Simple Icons, Fluent, Mage, MynaUI, Gravity UI, SVG Spinners). Reference as `<UIcon name="i-lucide-..."/>`.
- **Animations** — `nuxt-aos` for scroll-triggered, `nuxt-lottie` for `.lottie` assets in `public/animations/`, `lenis` for smooth scroll.
- **Validation** — Zod is available for form schemas.

## CI

The repo-root `.github/workflows/frontend-ci.yaml` runs on every PR and push to main:
1. `npm ci`
2. `npm run lint`
3. `npm run typecheck`
4. `npm run test:auth`
5. `npm run test:verification`
6. `npm run test:legacy-proxies`

## Deploy

Shipped and in production. The frontend versions independently of the backend
(`frontend/package.json` → tag `frontend-vX.Y.Z` → image
`ghcr.io/biosimulations/platform-frontend:frontend-X.Y.Z`). `frontend/Dockerfile`
is runtime-only — CI runs `npm ci && npm run build` on the host and the image
just runs `node .output/server/index.mjs`. Served at
`biosim.biosimulations.org`, with `api.biosim.biosimulations.org` for the
backend; SSR fetches inside the cluster use `NUXT_API_URL=http://api:8000` to
skip the public ingress.

**`biosim-gke` is GitOps-managed by Flux CD — merging a commit is the deploy.**
Flux reconciles `kustomize/overlays/biosim-gke` from `main` every minute, so
`kubectl apply` against that namespace gets reverted. Releasing is **two PRs**:

1. **Release PR** — bump the version. `scripts/bump-frontend.sh` is
   **patch-only**; for a minor/major run `npm version minor --no-git-tag-version`
   and commit `package.json` + `package-lock.json` yourself. Merge.
2. **Tag the merge commit on `main`** and push (`frontend-vX.Y.Z`) — this
   triggers `.github/workflows/release.yaml`, which builds the image (Node 24,
   amd64) and cuts a GitHub Release. The bump script tags the *branch* commit,
   so re-tag `main`.
3. **Verify the image published** — the release run's job conclusion is the
   gate. The GHCR package is private, so anonymous manifest checks return 403
   whether or not the tag exists, and a failed push still leaves the git tag and
   the Release behind. This exact failure silently stranded `frontend-0.1.1` for
   three weeks.
4. **Deploy PR** — bump `newTag: frontend-X.Y.Z` in
   `kustomize/overlays/biosim-gke/kustomization.yaml`. Merging deploys it.
   Edit the line directly rather than using `kustomize edit set image`, which
   reorders the block.

Verify with `flux get kustomizations -A` (READY=True at the new revision) and
`kubectl -n biosim-gke rollout status deploy/frontend`; `flux reconcile
kustomization platform-biosim-gke --with-source` skips the poll interval. To
roll back, revert the deploy commit — Flux restores the previous state within a
minute.

`kustomize/overlays/biosim-rke-frontend-dev/` (namespace `frontend-dev` on RKE,
`biosim-dev.cam.uchc.edu`) is **not** under Flux and is applied by hand. It
points at the RKE backend, and exists for WIP previews and catching
production-build SSR bugs that `npm run dev` hides — not for personal iteration.

> **SSR is where deploy-only frontend bugs live.** `/login` shipped a 500 in
> `frontend-0.2.0` because `plugins/auth0.client.ts` is client-only, so
> `useAuth0()` is undefined during SSR and `login.vue`'s top-level destructure
> threw. In-app navigation never SSRs a page, so it looked fine everywhere
> except a direct hit or refresh. Fixed by `'/login': { ssr: false }` in
> `routeRules`. After any deploy, curl the affected routes directly rather than
> clicking through the app.

## Important Notes

1. **Node version matters** — several deps (notably oxc-parser native bindings) require Node 22+. On older Node, `npm install` silently skips optional platform bindings and `nuxt prepare` fails on `require()` of bindings. Use `nvm install 22 && nvm use 22` if needed. CI and the release image build on Node 24.
2. **`npm run build` may not complete on macOS/arm64.** `@nuxt/image` pulls `ipx` as an **optional** dependency (`ipx@3.1.1` in `package-lock.json`), npm skips it there, and the build then dies at the prerender step — which exists only because of `'/': { prerender: true }` in `routeRules` — with `Cannot find package 'ipx'`. Installing it by hand is a trap: a newer major breaks on `createIPXH3Handler`, and the locked version needs `node-gyp` to compile `sharp`. Linux CI installs the optional dep normally, so this is a local-environment limit, not a code problem — verify with `npm run dev` (port 4200, no prerender) or let CI build it. A mismatched `ipx` in `node_modules` breaks the dev server too; `rm -rf node_modules/ipx` to recover.
3. **`frontend/Dockerfile` is runtime-only** — the Nuxt build happens on the host (`npm ci && npm run build`) and the image just runs `node .output/server/index.mjs` over `.output`. Building the image without a fresh `.output` ships stale code. See Deploy above.
4. **Pre-existing lint/typecheck failures** — frontend CI was off in the monorepo until Phase 2 of the integration plan moved the workflow to `.github/workflows/frontend-ci.yaml`. By then ~1500 lint errors and several TS errors had accumulated. Expect `npm run lint` and `npm run typecheck` to fail on `main` until cleanup. Cleanup is its own task.
5. **README is project-specific** — `frontend/README.md` was replaced with a project-specific quickstart on `frontend-backend-int`; the original Nuxt starter README is gone.

## External Services

- **`../backend/` FastAPI service** — primary backend for simulations and verification.
- **api.biosimulations.org** — project list and detail API, also linked from the footer. The verify-model run precheck reads `/runs/{id}` through the same-origin `/biosimulations-api/runs/{id}` proxy.
- **simdata.api.biosimulations.org** — the run precheck reads HDF5 metadata through `/simdata-api/datasets/{id}/metadata`. Both run-precheck proxies are GET-only, validate IDs as single literal segments, and never cache responses or forward caller credentials. They relay only status, content type and body; transport failures become 502.
- **api.biosimulators.org** — simulator registry and ontologies, reached through the same-origin `/biosimulators-api` proxy because its CORS allowlist excludes our hosts. Only the registry list and four ontology dumps are cached for 10 minutes per pod (five fixed keys, about 2 MB), with stale-while-revalidate. Simulator details are uncached; request credentials are never forwarded.
