# Auth0 tenant configuration

Version-controlled source for the Auth0-side configuration that the Platform backend
depends on at runtime. **Nothing in this directory is deployed by CI or by `kubectl`.**
Auth0 Actions are dashboard-managed; these files are the reviewed source of truth that the
dashboard is expected to match.

```
auth0/
├── README.md              this file
└── actions/
    └── post-login.js      Post-Login Action: stamps the roles + email claims
```

## Why this exists

The backend reads three custom claims from every access token:

| Claim | Backend setting | Consumed by |
| --- | --- | --- |
| `https://api.biosimulations.org/roles` | `AUTH0_ROLES_CLAIM` (`biosim_server/config.py`) | `common/auth/roles.py` — `require_roles`, `require_owner_or_admin` |
| `https://api.biosimulations.org/email` | `AUTH0_EMAIL_CLAIM` | `require_owner_or_admin`'s legacy-email ownership fallback |
| `https://api.biosimulations.org/email_verified` | `AUTH0_EMAIL_VERIFIED_CLAIM` | `require_owner_or_admin` — an unverified email must not grant ownership |

Auth0 puts **none** of these on an access token by default. `actions/post-login.js` does.
The claim names in that file MUST use backtick template literals
(`` `${NAMESPACE}/roles` ``). Single-quoted `'${NAMESPACE}/roles'` is a literal
JavaScript string and will stamp a claim the backend never reads.

**If the Action is absent, disabled, erroring, or deployed from a copy that still
uses single-quoted claim names:** every `require_roles` endpoint returns 403, no
admin exists, and owners cannot cancel or delete their own runs via the email
fallback. The backend logs a WARNING naming this as the likely cause
(`common/auth/auth0.py::_warn_roles_claim_absent`), but the HTTP responses are
indistinguishable from a legitimate permissions denial.

**REQUIRES EXTERNAL ACTION — redeploy after any edit to `post-login.js`.**
Committing the file does not update the tenant. Paste the file into the Auth0
Dashboard Action, Deploy, confirm it is bound to the Login flow, then decode a
real access token and confirm all three namespaced URIs are present.

## Required Auth0-side configuration

All of this is dashboard state. **REQUIRES EXTERNAL ACTION** — none of it can be applied
from this repository.

### 1. Roles

Create three tenant Roles (Auth0 Dashboard → User Management → Roles), matching
`common/auth/roles.py:9-11`:

| Role | Purpose |
| --- | --- |
| `admin` | Full access; bypasses ownership checks |
| `publisher` | Elevated, non-admin |
| `user` | Default, assigned to new sign-ups by the Action |

### 2. Machine-to-Machine application

Create an M2M application authorized for the **Auth0 Management API** with exactly these
scopes — no more:

- `read:roles`
- `create:role_members`

This application exists solely so the Action can assign the default role. It is **not** the
same as the (currently unconfigured) M2M application discussed in TODO #23 for
`PATCH`/`DELETE /api/v1/me`, which needs `update:users` / `delete:users`. Keep them
separate: different scopes, different blast radius.

### 3. Action secrets

Dashboard → Actions → Library → *Platform Post-Login* → Secrets. **Never commit these
values; never paste them into an issue, a PR, or this file.**

| Secret | Value |
| --- | --- |
| `AUTH0_DOMAIN` | `<AUTH0_DOMAIN>` — the tenant domain, e.g. `tenant.us.auth0.com` |
| `M2M_CLIENT_ID` | `<M2M_CLIENT_ID>` — from the application in step 2 |
| `M2M_CLIENT_SECRET` | `<M2M_CLIENT_SECRET>` — from the same application |
| `DEFAULT_ROLE_ID` | `<DEFAULT_ROLE_ID>` — the `rol_...` id of the `user` Role |

### 4. Action dependency

Dashboard → the Action → Dependencies → add `auth0` (any 4.x). Actions do not bundle it.

### 5. Flow binding

Dashboard → Actions → Flows → **Login** → drag the Action into the flow → **Apply**.
An Action that is saved and deployed but not bound to the flow **does not run**. This is the
single most common way for this configuration to silently stop working.

## Deploying a change

1. Edit `actions/post-login.js` in a branch. Get it reviewed like any other code.
2. Merge.
3. In the Auth0 dashboard, open the Action, **replace its entire body** with the file's
   contents, and click **Deploy**.
4. Confirm the Action is still bound to the Login flow.
5. Run the smoke check below.
6. Note the deployment (date, commit SHA, tenant) in the deploy runbook.

Deploying to the **wrong tenant** is the easy mistake. Confirm the tenant name in the
dashboard's top-left selector before you paste.

## Smoke check — run after every Action or tenant change

**REQUIRES EXTERNAL ACTION.** Needs a test user who has at least one role assigned.

```bash
# 1. Obtain an access token for a known-roled user.
#    Use the frontend login and copy the token from devtools, or, if a
#    Resource Owner Password grant is enabled on the tenant:
#      curl -s https://<AUTH0_DOMAIN>/oauth/token \
#        -H 'content-type: application/json' \
#        -d '{"grant_type":"password",
#             "username":"<TEST_USER>","password":"<TEST_PASSWORD>",
#             "audience":"<AUTH0_AUDIENCE>",
#             "client_id":"<CLIENT_ID>","scope":"openid email"}'

TOKEN='<ACCESS_TOKEN>'

# 2. Decode the payload and confirm BOTH claims are present and non-empty.
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
#    expect:
#      "https://api.biosimulations.org/roles": ["user"]      <- non-empty
#      "https://api.biosimulations.org/email": "..."         <- present

# 3. Confirm the backend agrees.
curl -s -H "Authorization: Bearer $TOKEN" https://<API_HOST>/api/v1/me
#    expect: 200 with the user's id/email

# 4. Confirm role gating works end to end.
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" https://<API_HOST>/api/v1/demo/private/animal
#    expect: 200 for a roled user, 403 for a role-less one
```

If step 2 shows the roles claim **missing**, the Action is not deployed, not bound to the
Login flow, or uses a different namespace. If it shows `[]`, the Action is running but the
user has no roles and the default-role assignment failed — check Auth0 Monitoring → Logs for
`default role assignment failed`.

## Preventing divergence between this repo and the tenant

There is no automated sync today. Three overlapping defences:

1. **Review.** Changes to `actions/post-login.js` go through PR review like any code.
2. **Runtime assertion.** `common/auth/auth0.py::_warn_roles_claim_absent` logs a WARNING
   when a validated token carries no roles claim — which is what a deleted, unbound, or
   renamespaced Action looks like from the backend. It cannot detect *logic* drift, only
   claim absence.
3. **Procedure.** The smoke check above is part of the deploy runbook
   (`backend/CLAUDE.md` → Deploy).

**REQUIRES DECISION — automation.** Auth0 Deploy CLI (`a0deploy`) or the Auth0 Terraform
provider could make this directory the actual deployed artifact, giving real drift
detection. Both need tenant-admin credentials in CI, which is a new secret with a large
blast radius. Not adopted here; recorded so the decision is deliberate. If adopted later,
this file becomes its input.
