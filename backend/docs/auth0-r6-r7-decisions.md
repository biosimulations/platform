# Auth0 R6 / R7 — Decision Register

Records blockers for privileged publish / project creation (R6) and the
status of role representation vs assignment (R7). No credential value appears
in this document.

Last updated: 2026-08-27.

These items are **not implemented** here. The repository must not invent a
publish workflow, a Project creator, a `site-admin` role, or new Auth0 claim
names to close them.

| ID | Question | Status | Decision | Notes |
|----|----------|--------|----------|-------|
| D-R6-1 | Publisher-only vs publishers + site-admins for publish / promotion? | **OPEN — product** | Not decided. Do not attach `require_roles` to a fictional publish route. | `PUBLISHER_ROLE` / `ADMIN_ROLE` exist in `common/auth/roles.py`. `site-admin` does **not** exist as a role name, Auth0 Action assignment, or claim value. |
| D-R6-2 | What is the publish / project-creation / promotion workflow? | **OPEN — product + API** | Not decided. No backend writer exists. | `POST /Publish` is absent. `docs/project-search-api-plan.md` defers write/publish. Frontend project create is an empty stub. Do not invent Project creation or stamp `owner`/`visibility` onto the external published corpus. |
| D-R7-1 | How are `admin` / `publisher` / `user` represented on the access token? | **DECIDED — existing claim** | Namespaced roles claim `https://api.biosimulations.org/roles` (`AUTH0_ROLES_CLAIM`), stamped by `auth0/actions/post-login.js`. `AuthenticatedUser.roles` + `require_roles` / `require_all_roles`. | Representation is not the blocker. |
| D-R7-2 | How are `publisher` and `admin` assigned? | **OPEN — operations** | Dashboard-manual (Auth0). The Post-Login Action auto-assigns `user` only. | Do not invent a provisioning API. |
| D-R7-3 | Does `site-admin` exist? | **UNRESOLVED — do not invent** | No. | Leave unresolved until D-R6-1 is decided. No tests, claims, or routes for `site-admin`. |

## What `publisher` currently gates

`DELETE /simulations/{id}` via `require_roles(admin, publisher)`, then the
visibility-aware mutation check (private runs are owner-only; an admin who is
not the owner cannot delete another user's private run).

That is **not** a publish gate.

## What will not be added until the blockers close

- A `POST /Publish` (or any promotion) route
- Project create / write APIs
- A `site-admin` role name or Auth0 claim
- ACLs, sharing, or ownership backfill
- Wrapping of unselected legacy endpoints (see `auth0-tokens-claims-endpoints.md` → R5)
