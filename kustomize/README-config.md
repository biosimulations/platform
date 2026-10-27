# Cluster configuration: what goes where

Two channels reach an `api` container, and choosing correctly between them is the whole of
this document.

| Channel | Source | Committed? | Encrypted? | Use for |
| --- | --- | --- | --- | --- |
| ConfigMap `api-config` | `config/<cluster>/api.env` + `shared.env` | yes, plain text | **no** | non-secret configuration |
| Secret `shared-secrets` | `overlays/<cluster>/secrets.dat` → `secrets.sh` → `secret-shared.yaml` | only the sealed form | yes | credentials |

Wiring: `base/api.yaml:29-31` (`envFrom: configMapRef: api-config`) and
`base/api.yaml:35-39` (`env: valueFrom: secretKeyRef`).

## The rule

> A value is a **secret** if possessing it grants a capability.
> A value is **configuration** if it merely identifies something.

A ConfigMap is readable by anyone with `get configmap` in the namespace, is stored
unencrypted in etcd, and is committed here as plain text. Assume everything in
`config/**` is public.

## api.env vs shared.env

`shared.env` feeds **both** `api-config` and `worker-config`
(`config/<cluster>/kustomization.yaml`). Put a value there only if the worker needs it too.
The worker has no HTTP surface and never validates a token, so **no `AUTH0_*` value belongs
in `shared.env`.**

## Auth0 variables

| Variable | Class | Home |
| --- | --- | --- |
| `AUTH_REQUIRED` | non-secret | `api.env` |
| `AUTH0_DOMAIN` | non-secret — public DNS, appears in every token's `iss` | `api.env` |
| `AUTH0_AUDIENCE` | non-secret — public API identifier, appears in every token's `aud` | `api.env` |
| `AUTH0_ISSUER` | non-secret | `api.env` |
| `AUTH0_JWKS_URI` | non-secret — a public endpoint serving public keys | `api.env` |
| `AUTH0_ROLES_CLAIM` | non-secret — a namespace URI in every token | `api.env` |
| `AUTH0_EMAIL_CLAIM` | non-secret | `api.env` |
| `AUTH0_EMAIL_VERIFIED_CLAIM` | non-secret — a namespace URI in every token | `api.env` |
| `AUTH0_PERMISSIONS_CLAIM` | non-secret — Auth0 RBAC claim name (default `permissions`) | `api.env` |
| `AUTH0_TRUSTED_ISSUERS` | non-secret — JSON issuer→audience map | `api.env` |
| `AUTH0_AUTH_TIME_CLAIM` | non-secret — claim name, default `auth_time` | `api.env` |
| `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` | non-secret policy flag (default `false`) | `api.env` |
| `AUTH0_PASSWORD_RESET_MAX_AUTH_AGE_SECONDS` | non-secret policy number (default `300`) | `api.env` |
| `AUTH0_PASSWORD_RESET_CLIENT_ID` | non-secret — the SPA's public client ID; blank disables ticket issuance (503) | `api.env` |
| `AUTH0_MANAGEMENT_CLIENT_ID` | treat as secret (pairs with the secret) | **sealed secret** |
| `AUTH0_MANAGEMENT_CLIENT_SECRET` | **SECRET** — grants `update:users`/`delete:users` (and `create:user_tickets` once authorized for password reset) on the whole tenant | **sealed secret** |

Auth0 **Action** secrets (`M2M_CLIENT_SECRET`, `DEFAULT_ROLE_ID`, …) are not Platform
configuration and never enter Kubernetes. See `auth0/README.md`.

### Password-reset ticket issuance is off in every overlay

As of 2026-10-02 no overlay sets `AUTH0_PASSWORD_RESET_CLIENT_ID` or the Management API
credentials, so `POST /api/v1/me/password-reset` returns 503 everywhere. That is the only
condition under which leaving `AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH` at its `false`
default is acceptable. **An overlay that sets `AUTH0_PASSWORD_RESET_CLIENT_ID` must set
`AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH=true` in the same change**; anything else is a
deployment blocker, and checklist step 6 below fails on it. The tenant and client
prerequisites are in `backend/CLAUDE.md` → "Password-reset enablement".

## Rate-limit variables

| Variable | Class | Home |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | non-secret | `api.env` |
| `RATE_LIMIT_WINDOW_SECONDS` | non-secret | `api.env` |
| `RATE_LIMIT_AUTHENTICATED_PER_WINDOW` | non-secret | `api.env` |
| `RATE_LIMIT_ANONYMOUS_PER_WINDOW` | non-secret | `api.env` |
| `RATE_LIMIT_PASSWORD_RESET_PER_WINDOW` | non-secret — password-reset budget, per pod | `api.env` |
| `RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS` | non-secret — password-reset window, per pod | `api.env` |
| `RATE_LIMIT_PAGE_PER_WINDOW` | non-secret — page-aggregation budget, per pod, per client IP | `api.env` |
| `RATE_LIMIT_PAGE_WINDOW_SECONDS` | non-secret — page-aggregation window, per pod | `api.env` |
| `RATE_LIMIT_LEGACY_PER_WINDOW` | non-secret — shared six-route legacy proxy budget per process per IP (default `60`, positive) | `api.env` |
| `RATE_LIMIT_LEGACY_WINDOW_SECONDS` | non-secret — independent legacy fixed window in seconds (default `60`, positive) | `api.env` |
| `UPSTREAM_MAX_RESPONSE_BYTES` | non-secret — ceiling on one upstream response body (decoded for page aggregations; raw bytes for the legacy proxy's buffered responses), per pod | `api.env` |
| `LEGACY_DOWNLOAD_MAX_CONCURRENT` | non-secret — legacy run downloads in flight at once, per pod (default `32`); beyond it 503 + `Retry-After` | `api.env` |

The legacy 60/60 defaults are provisional; tune for polling, downloads and shared NATs.
They are independent of `RATE_LIMIT_PAGE_*` and apply regardless of caller tokens.
Local exhaustion returns 429 with Retry-After before proxy work; the separate download
concurrency cap returns 503, and admitted requests can still relay upstream 429s.
`RATE_LIMIT_ENABLED=false` disables **all** budgets, not only legacy. Prefer tuning the
legacy settings for normal-client throttling. Counters are process-local (multiple workers
or replicas multiply allowances), reset on restart, and permit fixed-window boundary bursts.
This reduces shared-egress upstream risk but does not guarantee upstream availability or
stop distributed abuse. Verify the existing ingress trust assumptions before relying on IP
identity; see `backend/CLAUDE.md` for deployment caveats and monitoring.

None of these grant a capability by themselves -- they are policy numbers, not credentials
-- so per this document's own rule ("a value is a secret if possessing it grants a
capability"), every value in this table is ordinary ConfigMap configuration.

**PER-POD, NOT GLOBAL.** `common/ratelimit.py` keeps its counters in each pod's own memory,
so every `_PER_WINDOW` value above is enforced independently by each pod. `base/api.yaml`
declares 3 replicas, but every overlay overrides `api` to **1** (`overlays/<cluster>/
kustomization.yaml` → `replicas:`), so today each value is the cluster-wide ceiling. If an
overlay runs `N` replicas, set the value to `G / N` for a GLOBAL ceiling `G`. See
`backend/CLAUDE.md` → "Rate Limiting" for the full explanation.

## Format requirements

`AUTH0_DOMAIN` must be a **bare hostname**: `tenant.us.auth0.com`.
Not `https://tenant.us.auth0.com`, not a trailing slash, no whitespace.
`config.py` derives the issuer (`https://{domain}/`) and the JWKS URL
(`https://{domain}/.well-known/jwks.json`) from it; a URL here produces an issuer that
matches no token. Since P0 #5 this is caught at startup, but catch it in review first.

`AUTH0_TRUSTED_ISSUERS` is optional. When unset, the single-issuer
`AUTH0_DOMAIN`/`AUTH0_AUDIENCE` (or `AUTH0_ISSUER`/`AUTH0_JWKS_URI`) shape is used.
When set, it must be a JSON **object** mapping each issuer URL to
`{"audiences": ["..."], "jwks_uri": "https://..."}`. This is a pairing, not two
independent allowlists: an audience listed under issuer A is not valid for issuer B.
See `backend/docs/auth0-tokens-claims-endpoints.md`. Resource ownership (`owner_sub`) is
subject-only: do not trust a second issuer for owned resources until ownership is
issuer-bound (see `roles.is_owner`).

Every `.env` file here is `KEY=VALUE`, one per line, `#` comments, **and must end with a
newline.**

## Before you apply — the checklist

Run from the repo root. `kubectl kustomize` needs no cluster.

```bash
CLUSTER=biosim-rke     # or biosim-gke, biosim-local

# 1. The manifest renders at all.
kubectl kustomize "kustomize/overlays/$CLUSTER" > /tmp/rendered.yaml || exit 1

# 2. The Auth0 configuration is present and complete.
grep -E 'AUTH0_DOMAIN|AUTH0_AUDIENCE|AUTH_REQUIRED' /tmp/rendered.yaml

# 3. AUTH0_DOMAIN is a bare hostname.
grep -E 'AUTH0_DOMAIN: *(https?://|.*/)' /tmp/rendered.yaml && \
  echo "FAIL: AUTH0_DOMAIN must be a bare hostname" && exit 1

# 4. No secret leaked into a ConfigMap.
awk '/^kind: ConfigMap/,/^---/' /tmp/rendered.yaml \
  | grep -Ei 'secret|password|private[_-]?key' && \
  echo "FAIL: secret-looking value in a ConfigMap" && exit 1

# 5. Authentication is not disabled in a production overlay.
if [ "$CLUSTER" != "biosim-local" ]; then
  grep -q 'AUTH_REQUIRED: *"\?false' /tmp/rendered.yaml && \
    echo "FAIL: AUTH_REQUIRED=false in $CLUSTER" && exit 1
fi

# 6. Password-reset tickets are never enabled without the recent-authentication gate.
grep -qE '^ *AUTH0_PASSWORD_RESET_CLIENT_ID: *"?[^" ]' /tmp/rendered.yaml && \
  ! grep -qiE '^ *AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH: *"?true"?$' /tmp/rendered.yaml && \
  echo "FAIL: password reset enabled without AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH=true in $CLUSTER" && exit 1

echo "OK: $CLUSTER configuration looks sane"
```

Steps 3–6 exit non-zero on failure, so this is directly usable as a CI or pre-commit step
if the team wants one.

## Adding a new secret

`kustomize/scripts/sealed_secret_shared.sh` currently accepts exactly three positional
arguments and emits a Secret with two keys. Adding a third means editing, in order:

1. `kustomize/scripts/sealed_secret_shared.sh` — new positional argument + `--from-literal`
2. `kustomize/overlays/<cluster>/secrets.sh` — pass it through, for **all three** overlays
3. `kustomize/overlays/<cluster>/secrets.dat.template` — document the key, for all three
4. `kustomize/base/api.yaml` — a new `env: valueFrom: secretKeyRef` entry
5. Re-run `./secrets.sh` in each overlay and commit the regenerated `secret-*.yaml`

Miss step 2 or 3 in one overlay and that cluster gets an empty value with no error.
