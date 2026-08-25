# Which token, which claims, which endpoint

This is the Platform API's contract for authentication and authorization (P3 #26).
It describes the implementation in `biosim_server/common/auth/`, not a future design.

No credential value appears in this document.

---

## Which token?

OAuth 2.0 / OpenID Connect issues three different tokens. They are not interchangeable.

| Token | What it is | Who it is for | Platform API |
| --- | --- | --- | --- |
| **Access token** | Authorization credential for a *resource server* (this API). Carries `aud` equal to the API identifier. | The API, on every protected request | **Required** as `Authorization: Bearer <access_token>` |
| **ID token** | An OIDC *identity* assertion for the client application. Carries `aud` equal to the SPA/native client id. May include `nonce`, `email`, profile claims. | The frontend, to display who logged in | **Must not** be sent to this API |
| **Refresh token** | A credential used at the authorization server to obtain new access tokens | The Auth0 SDK / token endpoint only | Never sent here; this API does not accept or store refresh tokens |

The Platform API is an OAuth 2.0 **resource server**. It validates **access tokens** only.

`get_current_user` (`common/auth/auth0.py`) requires:

1. An `Authorization: Bearer …` header.
2. An RS256 signature matching a key from the configured JWKS URL for that token's issuer.
3. `iss` matching a configured issuer.
4. `aud` matching an audience **explicitly allowed for that same issuer**.
5. A non-empty string `sub`.
6. `exp` / `nbf` within a 60-second clock-skew leeway.

An ID token fails step 4: its `aud` is the application client id, not `AUTH0_AUDIENCE` / the issuer's configured API identifier. That rejection is the intended control, not an accident.

### Do this

```bash
# Access token from the SPA (audience = the Platform API identifier)
curl -s -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  https://api.biosim.biosimulations.org/api/v1/me
```

The frontend obtains that token with the Auth0 SPA SDK by passing the API audience
(`NUXT_PUBLIC_AUTH0_AUDIENCE` / `AUTH0_AUDIENCE`), not by reading `id_token` out of the
login response.

### Do not do this

```bash
# WRONG: the OIDC ID token is not an API credential
curl -s -H "Authorization: Bearer ${ID_TOKEN}" \
  https://api.biosim.biosimulations.org/api/v1/me
# Expected: 401 Invalid claims
```

Do not paste an ID token from jwt.io, from the Auth0 `/userinfo` flow, or from
`id_token` in a code-exchange response into `Authorization`.

### PyVCell cautionary example

PyVCell's Auth0 integration is a known source of "how not to do this" lessons for this
repository. `common/auth/discovery.py` already records one: PyVCell fetched OIDC discovery
**without a timeout**, which turns an identity-provider hang into an authentication-path
hang. Platform's discovery client uses an explicit timeout and a negative cache instead.

A second, independent class of mistake — the one this section exists to prevent — is
**ID-token / access-token confusion**: treating the OIDC identity token as if it were an
OAuth access token for a resource server.

That confusion typically looks like:

* the client sends `id_token` in `Authorization: Bearer …`;
* the API validates signature and issuer but is configured with the **SPA client id** as
  `audience`, so an ID token verifies;
* roles, email, or `email_verified` from the ID token are then used as API authorization
  inputs.

Platform does **not** have that implementation. Audience is the API identifier
(`AUTH0_AUDIENCE`, default `https://api.biosimulations.org`). ID tokens, which are
audienced to the SPA client, are rejected. Roles, email, and `email_verified` used for
authorization are read from the **access token**, where the Post-Login Action
(`auth0/actions/post-login.js`) stamps them as namespaced custom claims. They are not
read from an ID token, and they are not fetched from `/userinfo` on the request path.

The lesson is the OIDC vs OAuth split:

* **ID token** = "this browser session is user *sub*". For the client.
* **Access token** = "this caller may invoke API *aud* as user *sub*, with these roles and
  permissions". For this resource server.

---

## Which claims?

Claim names below are the ones the code actually reads. Do not invent others.

### Token validation (security-sensitive)

Checked by `jwt.decode` (python-jose) after RS256 signature verification against the
issuer's JWKS. A failure is HTTP **401**.

| Claim | Source | Rule |
| --- | --- | --- |
| `iss` | JWT | Must equal the configured issuer (`AUTH0_ISSUER`, or `https://{AUTH0_DOMAIN}/`, or an `AUTH0_TRUSTED_ISSUERS` map key). Unknown issuers are rejected **before** any JWKS fetch. |
| `aud` | JWT | Must include an audience allowed **for that issuer**. Missing `aud` is rejected. An audience configured for issuer B is not valid on a token from issuer A. |
| `exp` | JWT | Must not be in the past (60s leeway). |
| `nbf` | JWT | If present, must not be in the future (60s leeway). |
| `alg` (header) | JWT header | Allowlist is the module constant `_ALLOWED_ALGORITHMS = ("RS256",)` — not configurable. `alg:none` and HS256 are rejected. |
| `kid` (header) | JWT header | Must match an RSA key in that issuer's JWKS. Unknown kids trigger one cooldown-guarded refresh, then 401. |

`AUTH0_TRUSTED_ISSUERS` (P3 #27), when set, is a JSON object:

```json
{
  "https://tenant-a.auth0.com/": {
    "audiences": ["https://api.biosimulations.org"],
    "jwks_uri": "https://tenant-a.auth0.com/.well-known/jwks.json"
  },
  "https://tenant-b.auth0.com/": {
    "audiences": ["https://api.staging.example/"],
    "jwks_uri": "https://tenant-b.auth0.com/.well-known/jwks.json"
  }
}
```

This is an **issuer → audience(s)** map, not two independent allowlists. Omit the
variable to keep the existing single `AUTH0_DOMAIN` + `AUTH0_AUDIENCE` (or
`AUTH0_ISSUER` + `AUTH0_JWKS_URI`) configuration. Malformed JSON fails the startup
gate when `AUTH_REQUIRED=true`, and fails closed at request time otherwise.

Signing keys are cached **per JWKS URL**. Issuer A's keys cannot verify issuer B's
tokens.

### Identity and ownership (security-sensitive)

| Claim | Config | Used for |
| --- | --- | --- |
| `sub` | standard | Stable user id. Required. Persisted as `owner_sub` on simulation runs. Primary ownership key (`roles.is_owner`). |
| `https://api.biosimulations.org/email` | `AUTH0_EMAIL_CLAIM` | Email. Fallback: plain `email` (Keycloak test tokens). Informational on `/api/v1/me`; **authorization** only via the verified-email ownership fallback. |
| `https://api.biosimulations.org/email_verified` | `AUTH0_EMAIL_VERIFIED_CLAIM` | Whether that email is verified. Fallback: plain `email_verified`. Missing → `False` (fail closed). A legacy run without `owner_sub` is owned only when this is true **and** the emails match. |

These namespaced claims are stamped onto the **access token** by
`auth0/actions/post-login.js`. They are not present on Auth0 access tokens by default.

### Roles (security-sensitive authorization)

| Claim | Config | Used for |
| --- | --- | --- |
| `https://api.biosimulations.org/roles` | `AUTH0_ROLES_CLAIM` | List of role names. Missing or non-list → `[]` (fail closed). Does **not** grant permissions. |

Recognized role names (`common/auth/roles.py`): `admin`, `publisher`, `user`.

`require_roles(*allowed)` — caller needs **any one** of `allowed`.
`require_all_roles(*required)` — caller needs **every** `required`.
Empty allowed/required lists fail closed (403).

### Permissions / scopes (security-sensitive authorization)

| Claim | Config | Used for |
| --- | --- | --- |
| `permissions` | `AUTH0_PERMISSIONS_CLAIM` (default `permissions`) | Auth0 RBAC API permissions array when "Add Permissions in the Access Token" is enabled on the API. Missing or non-list → no permissions from this claim. |
| `scope` | OAuth 2.0 standard (not configurable) | Space-separated scopes. Merged with `permissions`. Non-string → ignored. |

`AuthenticatedUser.permissions` is the union of those two, de-duplicated. A role never
implies a permission. A permission never implies a role.

`require_permissions(*allowed)` — **any one**.
`require_all_permissions(*required)` — **every one**.
Empty lists fail closed. Missing permissions on the token fail closed.

### Informational only

| Claim | Notes |
| --- | --- |
| `email` (plain) | Fallback identity display when the namespaced claim is absent (non-Auth0 OIDC). Not proof of ownership unless `email_verified` is true. |
| `iat` | Validated as an integer if present; not used for authorization. |
| `azp` / `gty` / profile claims | Ignored. |

`/api/v1/me` may enrich `name` / `email_verified` from the Auth0 Management API when
configured. That enrichment is **not** an authorization input.

---

## Which endpoint?

All protected routes take the **access token** in `Authorization: Bearer`. ID tokens
return 401.

| Endpoint | Authn | Authz | Claims that matter |
| --- | --- | --- | --- |
| `GET /api/v1/me` | `get_current_user` | any valid access token | `sub`, namespaced email (display) |
| `PATCH` / `DELETE /api/v1/me` | `get_current_user` | same, plus Management API configured | `sub` (Management API user id) |
| `GET /api/v1/demo/private/me` | `get_current_user` | any valid access token | email or `sub` (gated by `ENABLE_RBAC_DEMO`) |
| `GET /api/v1/demo/private/animal` | `get_current_user` | `require_roles(admin, publisher, user)` | namespaced **roles** |
| `GET /api/v1/demo/private/permission` | `get_current_user` | `require_permissions("demo:read")` | **permissions** / `scope` |
| `DELETE /api/v1/simulations/{id}` | `get_current_user` | `require_roles(admin, publisher)` then `require_owner_or_admin` | roles, then `sub` / verified email vs `owner_sub` |
| `POST /simulations/run` | `get_optional_user` | anonymous allowed; token `sub` stored as owner when present | `sub` if a valid access token is sent |
| `POST /verify/omex`, `POST /verify/runs` | `get_current_user` | any valid access token | `sub` |
| `POST /projects/reindex` | shared secret, **not** Auth0 | `PROJECT_REINDEX_TOKEN` | n/a |

Ownership (`require_owner_or_admin`): `admin` role bypasses; otherwise `user.sub` must
equal `record.owner_sub`. Only if `owner_sub` is missing (legacy rows) does a
**verified** email match count. An unverified email never grants ownership.

### Request example (role-protected)

```bash
curl -s -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  https://api.biosim.biosimulations.org/api/v1/demo/private/animal
# 200 if the access token's roles claim includes admin, publisher, or user
# 401 if the token is missing, an ID token, wrong aud/iss, or bad signature
# 403 if the access token is valid but the roles claim is missing/empty/wrong
```

### Request example (permission-protected)

```bash
curl -s -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  https://api.biosim.biosimulations.org/api/v1/demo/private/permission
# 200 if permissions or scope includes demo:read
# 403 if the caller is only an admin (roles do not satisfy permission checks)
```

Demo routes are mounted only when `ENABLE_RBAC_DEMO=true` (off in production).

---

## Configuration reference

See `backend/CLAUDE.md` → Authentication (Auth0) and `kustomize/README-config.md`.
Non-secret values belong in each overlay's `api.env`.

| Variable | Role |
| --- | --- |
| `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` | Single-issuer production shape |
| `AUTH0_ISSUER` / `AUTH0_JWKS_URI` | Non-Auth0 OIDC (Keycloak tests); must be set together |
| `AUTH0_TRUSTED_ISSUERS` | Optional JSON issuer → `{audiences, jwks_uri}` map |
| `AUTH0_ROLES_CLAIM` | Default `https://api.biosimulations.org/roles` |
| `AUTH0_EMAIL_CLAIM` | Default `https://api.biosimulations.org/email` |
| `AUTH0_EMAIL_VERIFIED_CLAIM` | Default `https://api.biosimulations.org/email_verified` |
| `AUTH0_PERMISSIONS_CLAIM` | Default `permissions` (Auth0 RBAC access-token claim) |
