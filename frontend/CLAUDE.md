# CLAUDE.md - Platform Frontend Development Guide

This guide covers the webapp. For monorepo orientation (backend, kustomize, deployment), see the root `CLAUDE.md`.

All commands below assume the working directory is `frontend/` (i.e., `cd frontend` from the repo root first).

## Project Overview

The platform frontend is the [biosimulations.org](https://biosim.biosimulations.org) webapp — a Nuxt 4 SSR app that lets users upload OMEX archives, run simulations against the FastAPI backend, browse the biosim project DB, and consume related utilities.

**Stack:** Nuxt 4 (Vue 3, Nitro SSR), Nuxt UI 4, `@nuxtjs/seo`, `nuxt-aos`, `nuxt-lottie`, `lenis`, Zod
**Node:** 22 (matches CI)
**Package manager:** pnpm (CI uses pnpm; `package-lock.json` is also present but `pnpm-lock.yaml` is authoritative)
**Dev port:** 4200

## Quick Commands

```bash
# Install dependencies
pnpm install

# Run dev server (http://localhost:4200, HMR, devtools)
pnpm dev

# Lint
pnpm lint

# Type check
pnpm typecheck

# Production build
pnpm build

# Preview production build
pnpm preview
```

## Verification

Run these on every changeset before considering work complete:

```bash
pnpm lint
pnpm typecheck
```

CI runs the same two commands on every push (see `.github/workflows/ci.yml`). There is no unit/E2E test suite yet — verify UI changes manually in `pnpm dev`.

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
├── pnpm-lock.yaml              # Authoritative lockfile
├── package-lock.json           # Also present (legacy)
├── tsconfig.json
├── eslint.config.mjs
└── .github/workflows/ci.yml    # Separate from root CI; lint + typecheck
```

## Runtime Config

Defined in `nuxt.config.ts` → `runtimeConfig.public`. Read in components via `useRuntimeConfig().public`.

| Variable   | Purpose |
|------------|---------|
| `BASE_URL` | Public origin the app is served from (used by `@nuxtjs/seo`) |
| `API_URL`  | Backend API base URL — should point at the FastAPI service in `../backend/` |

Set via env (or a `.env` file — `dotenv` is a dependency). Example:
```
BASE_URL=https://biosim.biosimulations.org
API_URL=https://biosim.biosimulations.org
```

## Backend API Integration

Backend endpoints consumed (see `../backend/CLAUDE.md` for the full list):

| Page | Calls |
|------|-------|
| `pages/simulations/run.vue` | `POST /compatibility/check`, `POST /simulations/run` |
| `pages/simulations/check-status/[processing_id].vue` | `GET /simulations/{processing_id}` |
| `pages/simulations/index.vue` | `GET /runs` (currently commented out) |
| `pages/biosim-db.vue` | `GET /projects` (against `runtimeConfig.public.api_base`, not the platform backend) |

**Known issue — hardcoded API URLs:** Several pages call `https://biosim.biosimulations.org/...` directly instead of using `useRuntimeConfig().public.api_url`. Locations:
- `pages/simulations/run.vue:123` (`/compatibility/check`)
- `pages/simulations/run.vue:185` (`/simulations/run`)
- `pages/simulations/check-status/[processing_id].vue:26` (`/simulations/{id}`)

These need to migrate to runtime config before the app can target a non-prod backend.

**Note on runtime keys:** `biosim-db.vue:209` reads `runtimeConfig.public.api_base`, which is **not declared** in `nuxt.config.ts` (only `base_url` and `api_url` are). Either declare it or change the call site.

## Key Patterns

- **File-based routing** — pages under `app/pages/` become routes; `[param]` segments are dynamic.
- **Data fetching** — uses Nuxt's `$fetch` (Ofetch). Prefer `useFetch`/`useAsyncData` for SSR-friendly fetches; `$fetch` is fine for client-only event handlers.
- **Styling** — Nuxt UI 4 components + Tailwind utility classes. Theme tokens (`primary`, `neutral`) configured in `app.config.ts`.
- **Icons** — `@iconify-json/*` packages (Lucide, Simple Icons, Fluent, Mage, MynaUI, Gravity UI, SVG Spinners). Reference as `<UIcon name="i-lucide-..."/>`.
- **Animations** — `nuxt-aos` for scroll-triggered, `nuxt-lottie` for `.lottie` assets in `public/animations/`, `lenis` for smooth scroll.
- **Validation** — Zod is available for form schemas.

## CI

`.github/workflows/ci.yml` runs on every push:
1. `pnpm install`
2. `pnpm run lint`
3. `pnpm run typecheck`

This workflow lives under `frontend/.github/workflows/` rather than the repo-root `.github/workflows/` — it was carried over from Harrison's standalone repo and is **not yet consolidated** with the root CI. The root `ci.yaml` is path-filtered to `backend/**` and does not run on frontend-only changes.

## Deploy

**Not yet wired up.** As of the merge into the monorepo (2026-05-15):

- No `frontend/Dockerfile` exists.
- The frontend is not built or pushed by `kustomize/scripts/build_and_push.sh`.
- `kustomize/base/` has no deployment/service for the frontend.
- `frontend/package.json` version is decoupled from `backend/biosim_server/version.py`.

When wiring this up:
1. Add `frontend/Dockerfile` (multi-stage: `pnpm build` → run `node .output/server/index.mjs`).
2. Extend `kustomize/scripts/build_and_push.sh` to build/push `platform-frontend` for amd64 + arm64.
3. Add a `frontend.yaml` deployment/service to `kustomize/base/` and reference it from each overlay's `kustomization.yaml`.
4. Decide whether to couple `frontend/package.json` version to `backend/biosim_server/version.py` for unified releases.

## Important Notes

1. **Package manager** — Use `pnpm`, not `npm`. The presence of `package-lock.json` alongside `pnpm-lock.yaml` is incidental; CI uses pnpm.
2. **Package name still `"website"`** — `package.json` `"name"` field was not updated when the directory was renamed from `website/` → `frontend/`.
3. **No `frontend/Dockerfile`** — see Deploy section above.
4. **Hardcoded backend URLs** — see Backend API Integration section above; migrate to runtime config before deploying against a non-prod backend.
5. **README is project-specific** — `frontend/README.md` was replaced with a project-specific quickstart on `frontend-backend-int`; the original Nuxt starter README is gone.

## External Services

- **`../backend/` FastAPI service** — primary backend for simulations and verification.
- **api.biosimulations.org** — referenced from `app.vue` footer link; not currently called from app code.
