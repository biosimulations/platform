# CLAUDE.md - Platform Frontend Development Guide

This guide covers the webapp. For monorepo orientation (backend, kustomize, deployment), see the root `CLAUDE.md`.

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
```

CI runs the same two commands on every push (see `.github/workflows/ci.yml`). There is no unit/E2E test suite yet — verify UI changes manually in `npm run dev`.

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
├── public/                     # Static assets (images, fonts, lottie)
├── nuxt.config.ts
├── package.json
├── package-lock.json           # Authoritative lockfile (npm)
├── tsconfig.json
├── eslint.config.mjs
└── .github/workflows/ci.yml    # Separate from root CI; lint + typecheck
```

## Runtime Config

Defined in `nuxt.config.ts` → `runtimeConfig.public`. Read in components via `useRuntimeConfig().public`.

| Variable                 | Purpose |
|--------------------------|---------|
| `BASE_URL`               | Public origin the app is served from (used by `@nuxtjs/seo`) |
| `API_URL`                | Platform backend API base URL — points at the FastAPI service in `../backend/` |
| `BIOSIMULATIONS_API_URL` | Public biosimulations.org project DB API base URL (different service from the platform backend) — used by `pages/biosim-db.vue` |

Set via env (or a `.env` file — `dotenv` is a dependency). Example:
```
BASE_URL=https://biosim.biosimulations.org
API_URL=https://biosim.biosimulations.org
BIOSIMULATIONS_API_URL=https://api.biosimulations.org
```

## Backend API Integration

Backend endpoints consumed (see `../backend/CLAUDE.md` for the full list):

| Page | Calls |
|------|-------|
| `pages/simulations/run.vue` | `POST /compatibility/check`, `POST /simulations/run` |
| `pages/simulations/check-status/[processing_id].vue` | `GET /simulations/{processing_id}` |
| `pages/simulations/index.vue` | `GET /runs` (currently commented out) |
| `pages/biosim-db.vue` | `GET /projects` (against `runtimeConfig.public.biosimulations_api_url` — the public biosimulations.org project DB API, not the platform backend) |

## Key Patterns

- **File-based routing** — pages under `app/pages/` become routes; `[param]` segments are dynamic.
- **Data fetching** — uses Nuxt's `$fetch` (Ofetch). Prefer `useFetch`/`useAsyncData` for SSR-friendly fetches; `$fetch` is fine for client-only event handlers.
- **Styling** — Nuxt UI 4 components + Tailwind utility classes. Theme tokens (`primary`, `neutral`) configured in `app.config.ts`.
- **Icons** — `@iconify-json/*` packages (Lucide, Simple Icons, Fluent, Mage, MynaUI, Gravity UI, SVG Spinners). Reference as `<UIcon name="i-lucide-..."/>`.
- **Animations** — `nuxt-aos` for scroll-triggered, `nuxt-lottie` for `.lottie` assets in `public/animations/`, `lenis` for smooth scroll.
- **Validation** — Zod is available for form schemas.

## CI

`.github/workflows/ci.yml` runs on every push:
1. `npm ci`
2. `npm run lint`
3. `npm run typecheck`

This workflow lives under `frontend/.github/workflows/` rather than the repo-root `.github/workflows/` — it was carried over from Harrison's standalone repo and is **not yet consolidated** with the root CI. The root `ci.yaml` is path-filtered to `backend/**` and does not run on frontend-only changes.

## Deploy

**In progress on `frontend-backend-int`.** Plan: `docs/frontend-backend-integration-plan.md`. Decisions made: independent version streams (frontend at `0.1.0`, tagged `frontend-vX.Y.Z`), runtime-only Dockerfile (build outside Docker, image just runs `node .output/server/index.mjs`), shared Ingress with subdomain split, separate `frontend-dev` namespace on RKE for the frontend dev's branch deploys.

As of writing, still TBD: `frontend/Dockerfile`, `kustomize/base/frontend/` sub-package, `kustomize/scripts/build_and_push.sh frontend`, frontend image registry choice (GHCR vs. dockerhub).

## Important Notes

1. **Node version matters** — several deps (notably oxc-parser native bindings) require Node 22+. On older Node, `npm install` silently skips optional platform bindings and `nuxt prepare` fails on `require()` of bindings. Use `nvm install 22 && nvm use 22` if needed.
2. **No `frontend/Dockerfile`** — see Deploy section above.
3. **Pre-existing lint/typecheck failures** — the in-tree CI workflow (`frontend/.github/workflows/ci.yml`) has never run in the monorepo (GitHub Actions only discovers workflows at the repo-root `.github/workflows/`), so lint and typecheck have accumulated many failures (1500+ lint errors, several TS errors). Cleanup is its own task; consolidating the workflow to repo root is part of Phase 2.
4. **README is project-specific** — `frontend/README.md` was replaced with a project-specific quickstart on `frontend-backend-int`; the original Nuxt starter README is gone.

## External Services

- **`../backend/` FastAPI service** — primary backend for simulations and verification.
- **api.biosimulations.org** — referenced from `app.vue` footer link; not currently called from app code.
