# Review of the Authentication and Page Endpoints Audit & Improvement Plan

Review date: 2026-09-17. Branch: `chore/api-improvements`. Reviewed document:
`.agents/authentication-and-page-endpoints-audit-improvement-plan.md`.

This is a second-pass review of that audit, not a replacement for it. Its load-bearing
claims were independently verified against the code at the cited lines and all held.
What follows is only what the audit got wrong or left out, plus a value ranking that
differs from the audit's own priority assignments.

## Three corrections and two gaps

1. **It left a decisive fact unverified that's sitting in this repo.** The plan lists
   "Active issuer trust/exposure — deployed mappings not inspected" under *Items
   Requiring Runtime Verification*, and on that basis assigns AUTH-MAJ-001 P1 and
   declines to assign any P0. But `AUTH0_TRUSTED_ISSUERS` appears in no overlay —
   `kustomize/config/{biosim-gke,biosim-rke,biosim-local}/api.env` set only
   `AUTH0_DOMAIN` and `AUTH0_AUDIENCE`. Every cluster runs single-issuer. Combined with
   `AUTH0_MANAGEMENT_CLIENT_ID` being unset everywhere (so the Management routes 503),
   AUTH-MAJ-001's exploit needs two preconditions that hold in zero environments. That's
   a static check, not a runtime one. It belongs in the evidence, and it changes the
   ranking.

2. **AUTH-MAJ-002's suggested fix is half unimplementable.** It says "add required-exp
   options or equivalent explicit validation." python-jose has no `require_exp` — this
   was checked against `_validate_claims`, and the option does not exist. Only the
   explicit check works.

3. **Numbering has holes** — §5.2 jumps to §5.4, §6.1 to §6.3. Presumably empty sections
   were dropped, but it reads like content was lost.

**Missed, and it matters:** both page endpoints are unauthenticated and unrate-limited
(`get_project_page` and `get_run_page` take only the HTTP client dependency), and each
request fans out to 3–4 calls against `api.biosimulations.org`. That's a free 3–4×
amplifier pointed at a third party. The plan discusses payload size and pool pressure,
but never the amplification vector or a concurrency bound toward upstream.

**Also missed:** the run page's 60 s budget is effectively dead code. `httpx.Timeout(30.0)`
caps every phase at 30 s, and the run page's four calls all run in parallel — so the inner
assembly can't exceed ~30 s and the 60 s `wait_for` never fires. The project page (30 s
identity then 30 s satellites = 60 s > 45 s) genuinely can hit its budget. The plan
correctly notes `Timeout(30.0)` isn't a total deadline but never draws this conclusion.

## Auth — ranked by value

1. **Scope the browser bearer to the API origin** (AUTH-MAJ-003). The only P1 here that is
   live right now with no configuration precondition — it fires on every logged-in page
   load. `profile.vue:70` demonstrably ships an `api.biosimulations.org`-audience token to
   the Auth0 domain, and `useVisualizations.ts:52,100` ship it to arbitrary upstream file
   URLs. Fix is a scoped `$fetch` instance plus an explicitly unauthenticated client for
   files/visualizations/Auth0 — contained, no backend change, no config gate.

2. **Require `exp` on access tokens** (AUTH-MAJ-002). Best value-to-effort ratio in the
   document. A signed token with no expiry is accepted forever, today, in every cluster,
   regardless of issuer count. The fix is an explicit presence check before `jwt.decode` —
   roughly three lines and one test. Do the subject-normalization half
   (`str_strip_whitespace` silently rewriting identity keys) in the same change.

3. **Bound Management-token refresh failure** (AUTH-MAJ-005). Ship this with password
   reset, not after. The moment `AUTH0_MANAGEMENT_CLIENT_ID` is set, a cold Auth0 outage
   turns N concurrent `/api/v1/me` callers into N serialized 10 s token POSTs — and those
   routes have no deadline at all, unlike the pages. Share a failed refresh for a short
   cooldown, validate `payload["access_token"]`/`["expires_in"]` before caching (currently
   an unguarded `KeyError` on a malformed 200), and put a monotonic budget around
   lock-wait + token + request.

4. **Decide the step-up policy for password reset now** (AUTH-MAJ-004). Cheap while the UI
   doesn't exist, expensive once it does. This is a design decision, not code — and the
   plan is right that a refreshed token or `iat` is not evidence of recent interactive
   auth.

5. **Add the issuer guard to the Management routes** (AUTH-MAJ-001, demoted from the plan's
   P1). Not reachable in any deployed environment today. Still worth doing, because it's
   ~one line in `_require_management_api` mirroring the rule the reset route already
   applies, and it's a landmine for whoever first sets `AUTH0_TRUSTED_ISSUERS`. Treat the
   `owner_sub` → issuer+subject migration as a separate, much larger piece with a real
   backfill policy — not P1.

6. **Namespace the reset quota by issuer and give it its own ceiling** (AUTH-MIN-002).
   Folds naturally into 3 and 5.

7. **Privacy headers on dependency failures** (AUTH-MIN-001), **pooled Auth0 transport**
   (AUTH-MIN-004, pairs with 3), **OpenAPI error documentation** (AUTH-MIN-003). Small,
   independent, do them opportunistically.

8. **Reset UI plus a mounted signed-JWT→ticket test** (AUTH-MAJ-006). Correctly sequenced
   last — it depends on 1 and 4.

## Endpoints — ranked by value

1. **Cancel and drain project satellites** (PROJECTS-MAJ-001). The one confirmed endpoint
   defect, and the fix already exists twelve lines below it. Mirror `_cancel_and_drain`
   from the run page: explicit tasks, `finally`, drain. Keep the identity barrier and the
   satellite-404 fallback, and don't swap in a `TaskGroup` — an `ExceptionGroup` would
   turn your 404/502/504 into 500.

2. **Rate-limit the two page endpoints** (addition, not in the plan). Unauthenticated,
   unmetered, 3–4 upstream calls against a third-party API you don't own.
   `workflow_rate_limit` already exists and `client_identity` already handles anonymous IP
   keying — this is a one-line dependency per route, and it's the cheapest protection in
   either list.

3. **Instrument the page phases** (SHARED-MIN-001). Promote this above payload bounds — you
   can't choose a byte budget you haven't measured, and `bench_pages.py` gives you
   end-to-end latency with no phase breakdown. Note `log_config.py::JsonFormatter` only
   passes three explicit auth extras through, so new structured fields need a formatter
   change first.

4. **Bound upstream payloads** (SHARED-MAJ-001). Real gap — `fetch_upstream_json_value`
   buffers the whole body then parses synchronously, with no limit anywhere. But it's the
   most expensive item here and needs 3's numbers to size it. Limit decoded bytes, not
   `Content-Length`.

5. **Fix the timeout asymmetry** (addition). The run page's 60 s budget can't fire behind a
   30 s per-phase httpx timeout on parallel calls; the project page's 45 s can. Either set
   the budgets from the real serial depth (project ≈ 2 hops, run ≈ 1) or drop the run
   budget and document that httpx bounds it — right now the constant implies a guarantee
   it doesn't provide.

6. **Pin mixed-failure precedence with tests** (RUN-MIN-001). Cheap, and it locks in the
   deliberate "identity error wins" semantics before someone refactors it into fail-fast.

7. **Module-level `TypeAdapter`s** (SHARED-MIN-002). Trivial and real. Don't advertise it as
   a measured win without measuring it.

The plan's own Phase 1→5 ordering is sound with two swaps: demote AUTH-MAJ-001 out of
phase 1, and pull PROJECTS-MAJ-001 forward — it's a ten-line fix with an existing
template, sitting behind a step-up policy debate in the current sequencing.
