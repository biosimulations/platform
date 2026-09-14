# Auth0 P3 — Decisions Register

Engineering decisions for P3 items #25, #26, #27, and #28. No credential value
appears in this document.

Last updated: 2026-08-25.

| ID | Question | Status | Decision | Item |
| --- | --- | --- | --- | --- |
| D-P3-1 | How should multiple issuers/audiences be expressed? | **DECIDED** | Explicit `AUTH0_TRUSTED_ISSUERS` JSON map of issuer → `{audiences, jwks_uri}`. Not `issuer ∈ S AND audience ∈ T`. Unset → existing single-issuer `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` (or `AUTH0_ISSUER`/`AUTH0_JWKS_URI`). | #27 |
| D-P3-2 | Per-issuer JWKS cache? | **DECIDED** | `JwksCache` state is keyed by JWKS URL so signing keys are never shared across issuers. | #27 |
| D-P3-3 | Which access-token claim is a permission? | **DECIDED** | Auth0 RBAC `permissions` array (`AUTH0_PERMISSIONS_CLAIM`, default `permissions`) union the OAuth `scope` string. Roles do not imply permissions. | #25 |
| D-P3-4 | `AuthenticatedUser` mutability? | **DECIDED** | Pydantic model, `frozen=True`, `extra="forbid"`. Empty `sub` and non-string role/permission entries are rejected at construction. | #28 |

Contract documentation: `docs/auth0-tokens-claims-endpoints.md`.
