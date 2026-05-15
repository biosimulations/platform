# Biosimulations Platform — Frontend

Webapp for the [biosimulations/platform](../) monorepo. Calls the FastAPI backend in [`../backend/`](../backend) (production: <https://biosim.biosimulations.org/docs>).

## Stack

- [Nuxt 4](https://nuxt.com) (Vue 3, server-rendered via Nitro)
- [Nuxt UI 4](https://ui.nuxt.com)
- [`@nuxtjs/seo`](https://nuxtseo.com), `nuxt-aos`, `nuxt-lottie`, `lenis`
- pnpm (CI uses pnpm; `package-lock.json` is also present but `pnpm-lock.yaml` is authoritative)
- Node 22 (matches CI)

## Setup

```bash
pnpm install
```

## Development

```bash
pnpm dev        # http://localhost:4200
pnpm lint
pnpm typecheck
```

## Production build

```bash
pnpm build
pnpm preview
```

## Runtime config

Read from environment via `nuxt.config.ts` → `runtimeConfig.public`:

| Variable | Purpose |
|----------|---------|
| `BASE_URL` | Public origin the app is served from (used by `@nuxtjs/seo`) |
| `API_URL`  | Backend API base URL — should point at the FastAPI service from `../backend/` |

> **Note:** Some pages currently hardcode `https://biosim.biosimulations.org` instead of reading `API_URL`. Migrate those to `useRuntimeConfig().public.api_url` before deploying against a non-prod backend.

## CI

`.github/workflows/frontend-ci.yaml` (at repo root, path-filtered to `frontend/**`) runs `npm run lint` and `npm run typecheck` on every push and PR.

## Deployment

The frontend is **not yet wired into the shared kustomize pipeline** (no `Dockerfile`, no entry in `kustomize/base/`, no build step in `kustomize/scripts/build_and_push.sh`). See the parent [`CLAUDE.md`](../CLAUDE.md) for the current monorepo deploy story.
