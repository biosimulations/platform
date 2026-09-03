# Passthrough Proxy — Implementation & Verification Plan

**Endpoint:** `GET /projects/{id}/summary` (with `GET /runs/{run_id}/summary` as a separable sibling)
**Branch:** `feature/passthrough-proxy-api` · **Base:** `main` (`3d0b5f0`)
**Status:** implemented — `a7ebfef` (project summary), `6aa63d9` (run summary)
**Scope:** backend only; `frontend/` must remain untouched

---

## 1. Goal

Prepare a clean, narrowly scoped commit landing `GET /projects/{id}/summary` as a byte-faithful passthrough proxy over the shared `common/proxy.py` helper and the pooled `get_http_client()` dependency, with regression tests pinning path encoding and encoded-slash rejection. Resolve the orphaned `run_summary_response.json` fixture so nothing dead is committed, optionally forward `Vary`, and verify with the three required commands. Keep the sibling `GET /runs/{run_id}/summary` work and the `.gitignore` housekeeping in separate commits so the functional project-summary change stays reviewable in isolation.

---

## 2. Confirmed decisions

| # | Decision | Status | Evidence |
|---|---|---|---|
| 1 | `GET /projects/{id}/summary` is a **passthrough proxy**, not a projection | **Settled — do not reopen** | Governing instruction; implemented at `projects/router.py:121-148` |
| 2 | The upstream summary response is preserved verbatim; no local projected representation | Settled | `common/proxy.py:103-107` returns `downstream.content` unmodified |
| 3 | The endpoint complements, not replaces, the existing `GET /projects` / `GET /projects/stats` query endpoints | Settled | Both remain in `projects/router.py`; `test_project_summary.py:156-170` pins that `/projects/stats` is not shadowed |
| 4 | Shared abstraction is the pooled `httpx.AsyncClient` injected via `Depends(get_http_client)` | Settled | `dependencies.py:103-127`, lifecycle at `:157` / `:200-202` |
| 5 | Error policy: mirror 2xx/3xx/4xx; 5xx→502, timeout→504, transport→502; upstream host logged, never echoed | Settled | `common/proxy.py:83-101` |
| 6 | `response_model=None`; route returns `fastapi.Response` | Settled | Required for byte passthrough; `projects/router.py:123` |
| 7 | No frontend changes in this work | Settled | `git status --porcelain frontend/` is empty and must stay empty |
| 8 | `git add .` is prohibited | Settled | Six unrelated untracked paths exist, none gitignored |

**Baseline (verified, unchanged):** `pytest -m "not integration"` → 213 passed / 15 skipped / 4 deselected · `ruff check .` → clean · `mypy biosim_server tests` → 126 files clean.

---

## 3. Preflight inspection

Run every inspection from the repository root unless noted. **No file should be edited until items 1–5 are answered.**

| # | Inspect | Question it answers | Action that depends on the answer |
|---|---|---|---|
| 1 | `common/proxy.py:41-43` (`upstream_url`) | Does path encoding already exist and is it correct? | **Answered: yes.** `quote(segment, safe="")` per segment. Probed: `a%b→a%25b`, `a b→a%20b`, `a#b→a%23b` — no double-encoding through httpx's URL merge. ⇒ **Patch 1 needs no production change; it is tests-only.** Re-confirm unchanged before writing tests |
| 2 | `common/proxy.py:28-38` (`SAFE_RESPONSE_HEADERS`) | Is `vary` forwarded? | Not present. Determines whether Patch 3 is in scope |
| 3 | `projects/router.py:121-148` | Is the route already complete and correct? | **Answered: yes** — `response_model=None`, `upstream_url("projects", project_id, "summary")`, raw query from `request.scope["query_string"]`, no auth dependency, no caller headers. ⇒ **No source edit required for the endpoint itself** |
| 4 | `grep -rn "run_summary_response" backend/` | Is the fixture referenced? | **Answered: no output — orphaned.** Forces the Patch 2 decision |
| 5 | `json.load()` the fixture, list top-level keys | Does the real capture contain `unknownFutureField`? | **Answered: no.** Keys are `['id','name','tasks','outputs','run','metadata','submitted','updated']`. ⇒ Wiring it in is **not** a drop-in swap |
| 6 | `git diff backend/biosim_server/api/main.py` | Which endpoint does this change serve? | **Answered: exclusively run-summary** (import + `include_router(run_summary_router)`). The projects router was already registered on `main`. ⇒ **`api/main.py` does not belong in a project-summary-only commit** |
| 7 | `git diff` on `simulations/__init__.py` and `simulations/router.py` | Same question | **Answered: exclusively run-summary** |
| 8 | `git diff backend/biosim_server/dependencies.py` | Is `get_http_client` shared by both endpoints? | **Answered: yes.** Prerequisite for whichever endpoint lands first ⇒ belongs in the project-summary commit |
| 9 | `tests/projects/test_project_summary.py` (170 LOC) | What is covered, and what conventions must new tests follow? | Governs Patch 1's shape: reuse `proxy_client(handler)` (`:29-37`), `anyio_backend` (`:18-20`), autouse `clear_overrides` (`:23-26`) |
| 10 | `tests/simulations/test_run_summary.py:17-30` and `:106` | Where is the inline `RUN_SUMMARY` stub, and what asserts `unknownFutureField`? | Governs Patch 2's edit surface |
| 11 | `kustomize/overlays/biosim-gke/ingress.yaml` + `api/main.py:119-123` | Is there a shared cache that makes `Vary` load-bearing? | **Answered: no.** nginx-ingress declares only `proxy-body-size: 20m`; no CDN or response cache. `CORSMiddleware` uses an explicit origin list, so Starlette emits its own `Vary: Origin`. ⇒ **Patch 3 is desirable, not necessary** |
| 12 | `git status --porcelain`, `git diff --cached --stat` | What is dirty, and is anything staged? | **Answered:** 5 modified tracked, 5 unrelated untracked dirs + `docs/directory_structure.md`, 4 untracked feature files, **nothing staged** |
| 13 | `.gitignore` (tail) | What comment convention do ignore entries follow? | Shapes the optional housekeeping commit — entries carry a short rationale comment |
| 14 | `backend/CLAUDE.md` → Verification | From which directory must validation commands run? | **`cd backend` first.** `git` commands run from the repo root. Mixing these up is the most likely execution error |

---

## 4. Dependency order

```
[P0] Preflight inspection 1-14                       BLOCKING — nothing starts until done
        |
        +--> [P1] DECISION: commit split (see section 5)          BLOCKING for all staging
        |
        +--> [P2] Patch 2 decision: wire fixture in OR delete     BLOCKING for staging path list
        |           |
        |           +--> [P2a] Edit tests/simulations/test_run_summary.py   (only if "wire in")
        |
        +--> [P3] Patch 1: add encoding + %2F tests               INDEPENDENT of P2/P4
        |           (tests/projects/test_project_summary.py — tests only, no source edit)
        |
        +--> [P4] Patch 3: `vary` (OPTIONAL)                      INDEPENDENT
                    (common/proxy.py + one test)
                            |
    +-----------------------+-----------------------+
    v                                               v
[V] Validation: pytest -> ruff -> mypy       BLOCKING for staging
    |
    v
[S] Stage explicitly by path                 BLOCKING for scope check
    |
    v
[C] Scope check: frontend/ clean + cached diff exact
    |
    v
[X] Commit A (project summary) --> Commit B (run summary) --> Commit C (.gitignore, optional)
```

| Task | Blocking? | Notes |
|---|---|---|
| P0 preflight | **Blocks everything** | Items 1–8 already answered; re-confirm 1, 2, 4 have not drifted |
| P1 commit-split decision | **Blocks staging** | Determines the path list; cannot be deferred |
| P2 fixture decision | **Blocks staging** | Determines whether there is a 9th staged path |
| P3 Patch 1 | Not blocking, **strongly recommended** | Tests-only; zero risk to production behavior |
| P4 Patch 3 | **Optional** | One-word source change + one test |
| V validation | **Blocks staging** | All three must pass |
| S staging | **Blocks scope check** | |
| C scope check | **Blocks commit** | |
| Commit C (`.gitignore`) | **Optional, must not be mixed in** | |

**P3 and P4 are mutually independent.** **P2a and P3 touch different test files** and cannot conflict.

---

## 5. Scope mismatch (flagged)

The stated goal is a passthrough proxy for `GET /projects/{id}/summary`. Four of the eight staged paths serve a *different* endpoint, `GET /runs/{run_id}/summary`, and serve nothing else.

| Staged path | Why it is expected to change | Endpoint served |
|---|---|---|
| `backend/biosim_server/common/proxy.py` | **New file** — shared helper. Changes further **only if Patch 3** is taken | **Both** (shared) |
| `backend/biosim_server/dependencies.py` | **Already modified** — adds `get_http_client` + lifecycle. **No further edit needed** | **Both** (shared) |
| `backend/biosim_server/projects/router.py` | **Already modified** — adds the passthrough route. **No further edit needed** | **Project summary** |
| `backend/tests/projects/test_project_summary.py` | **New file** — changes with **Patch 1** | **Project summary** |
| `backend/biosim_server/api/main.py` | Already modified — *entire diff* is `run_summary_router` import + `include_router` | **Run summary only** |
| `backend/biosim_server/simulations/__init__.py` | Already modified — *entire diff* is the `run_summary_router` export | **Run summary only** |
| `backend/biosim_server/simulations/router.py` | Already modified — adds the `/runs/{run_id}/summary` route | **Run summary only** |
| `backend/tests/simulations/test_run_summary.py` | New file — changes with **Patch 2** | **Run summary only** |

**Five of the eight paths need no further modification at all** — they are already in their final state in the working tree. Only `test_project_summary.py` (Patch 1), `test_run_summary.py` (Patch 2), and optionally `proxy.py` (Patch 3) will actually be edited.

### Resolution options

- **Option A — split into two commits (RECOMMENDED).** Verified feasible: the run-summary changes are cleanly separable, and a tree containing only Commit A is self-consistent (the projects router is already registered on `main`; nothing references `run_summary_router`). Matches the stated goal, minimizes diff size, keeps each commit reviewable.
  - **Commit A — project summary (4 paths):** `common/proxy.py`, `dependencies.py`, `projects/router.py`, `tests/projects/test_project_summary.py`
  - **Commit B — run summary (4 paths, +fixture if retained):** `api/main.py`, `simulations/__init__.py`, `simulations/router.py`, `tests/simulations/test_run_summary.py`
- **Option B — one commit for all eight paths.** Also defensible, since both routes share the helper. If chosen, the commit subject must name **both** endpoints; do not describe it as a project-summary commit.

Take Option A unless both endpoints must ship atomically. The rest of this plan is written for Option A and notes Option B deltas inline.

---

## 6. Implementation steps

### Step 1 — Re-confirm preflight state (no edits)

- **Objective:** confirm the working tree has not drifted since this analysis.
- **Files:** none edited.
- **Verify exactly:** `git status --porcelain` matches the 15 entries in preflight item 12; `grep -rn "run_summary_response" backend/` still returns nothing; `sed -n '41,43p' backend/biosim_server/common/proxy.py` still shows `quote(segment, safe="")`; `git diff --cached --stat` is empty.
- **Completion:** all four confirmations hold.
- **Risk:** the tree has drifted mid-session before. If anything differs, re-run the full preflight table rather than proceeding on these findings.

### Step 2 — Patch 1: path-encoding and `%2F`-rejection regression tests

- **Objective:** pin two currently-unpinned, security-adjacent guarantees. **Tests only — no production code changes.**
- **Files:** `backend/tests/projects/test_project_summary.py` (append only).
- **Verify first:** `upstream_url` is unchanged (Step 1). The tests pin existing behavior; they do not introduce it.
- **Behavior to pin:**
  1. A caller-supplied id is quoted into exactly one upstream path segment and is **not** double-encoded — parametrize `a%25b`, `a%20b`, `a%23b`, asserting `request.url.raw_path` per case.
  2. An id containing an encoded slash is rejected **before any upstream call** — assert HTTP 404 **and** that the handler recorded zero requests.
- **Tests to add:** two, reusing the existing `proxy_client(handler)` helper and the autouse `clear_overrides` fixture.
- **Completion:** both pass; `test_project_summary.py` rises from 9 to ~13 tests (parametrization); `pytest -m "not integration"` still 213+ passed.
- **Risks / edge cases:** the `%2F` rejection is a **Starlette routing** property (the ASGI server hands the app a decoded path, so `/projects/a%2Fb/summary` never matches `/{project_id}/summary`), pinned at the ASGI layer via `ASGITransport`. It is *not* a property of `proxy_get` — say so in the test docstring so a future reader does not relocate the assertion.

### Step 3 — Patch 2: resolve the orphaned fixture

- **Objective:** ensure nothing dead is committed. See section 8 for the decision procedure.
- **Files:** `backend/tests/simulations/test_run_summary.py` (if wiring in) **or** `backend/tests/fixtures/local_data/run_summary_response.json` (if deleting).
- **Belongs to:** **Commit B** — the fixture and its test serve the run-summary endpoint.
- **Completion:** `grep -rn "run_summary_response" backend/` returns a real reference, **or** the file no longer exists. Never both zero references and file present.
- **Risks:** the real capture has **no `unknownFutureField`**; a naive swap breaks the assertion at `test_run_summary.py:106`. That assertion must move to a small separate test with a synthetic body.

### Step 4 — Patch 3 (OPTIONAL): forward `Vary`

- **Objective:** forward the upstream `Vary` alongside the cache validators already forwarded (`cache-control`, `etag`, `expires`, `last-modified`).
- **Files:** `backend/biosim_server/common/proxy.py:28-38`; one test in `backend/tests/projects/test_project_summary.py`.
- **Belongs to:** **Commit A** (touches the shared helper).
- **Completion:** `vary` present in `SAFE_RESPONSE_HEADERS`; a test asserts an upstream `Vary` reaches the caller.
- **Risks:** near zero — one frozenset member.

### Step 5 — Validation

- **Objective:** prove the tree is green before anything is staged.
- **Completion:** all three commands in section 10 pass at or above the recorded baseline.
- **Risk:** **the three commands run from `backend/`; all `git` commands run from the repo root.**

### Step 6 — Stage explicitly, then verify scope

- **Objective:** stage only intended paths; prove the cached diff matches expectation exactly.
- **Completion:** `git status --porcelain frontend/` empty; `git diff --cached --stat` lists exactly the planned paths and no others.
- **Risk:** brace expansion is bash/zsh-specific; expand manually in any POSIX-`sh` context.

### Step 7 — Commit, in order

Commit A, then Commit B, then optional Commit C. See section 12.

---

## 7. Patch 1 — path encoding and `%2F` rejection

**Purpose:** restore path-encoding coverage that existed in the pre-rewrite suite (`test_load_project_summary_quotes_hostile_id`) and was dropped when the tests were rewritten for passthrough. It guards the boundary where a caller-supplied id becomes part of an upstream URL.

**Verify first — the production behavior is already correct.** `common/proxy.py:41-43`:

```python
def upstream_url(*segments: str) -> str:
    return "/" + "/".join(quote(segment, safe="") for segment in segments)
```

Probed end-to-end through the route and httpx's URL merge:

| Caller path | Upstream `raw_path` | Result |
|---|---|---|
| `/runs/a%25b/summary` (id `a%b`) | `/runs/a%25b/summary` | no double-encoding |
| `/runs/a%20b/summary` (id `a b`) | `/runs/a%20b/summary` | correct |
| `/runs/a%23b/summary` (id `a#b`) | `/runs/a%23b/summary` | fragment delimiter neutralized |
| `/projects/..%2Fruns%2Fsecret%3Fx=1/summary` | — | **404, zero upstream calls** |

⇒ **Patch 1 introduces no source change.** It converts verified-but-unpinned behavior into a regression guard.

**Why `%2F` needs explicit handling and a test.** An encoded slash is the classic path-traversal vector against a proxy: if it survived to `upstream_url`, `quote(..., safe="")` would neutralize it; if it were decoded *before* quoting and passed through unquoted, `../runs/secret?x=1` would let a caller redirect the proxy to an arbitrary upstream resource. Today two independent layers close this — ASGI routing rejects it first, and `quote` would neutralize it second. **A test is required because the outer layer is a routing artifact, not an explicit decision**: changing the route to `{project_id:path}`, or adding a normalizing middleware, would silently remove it with no other test failing.

**Tests to add** (append to `backend/tests/projects/test_project_summary.py`; reuse `proxy_client`, `anyio_backend`, `clear_overrides`):

```
test_project_id_stays_one_encoded_path_segment
    parametrize ("a%25b", b"a%25b"), ("a%20b", b"a%20b"), ("a%23b", b"a%23b")
    assert seen[0].url.raw_path == b"/projects/" + expected + b"/summary"

test_encoded_slash_in_id_is_rejected_before_any_upstream_call
    GET /projects/..%2Fruns%2Fsecret%3Fx=1/summary
    assert response.status_code == 404
    assert seen == []          # zero upstream calls — the load-bearing assertion
```

**Scope note:** these belong in the **projects** test file (Commit A). Mirroring them for `/runs/{run_id}/summary` is optional and would belong to Commit B; the helper is shared, so one endpoint's coverage substantially protects the other.

---

## 8. Patch 2 — resolve `run_summary_response.json`

**Purpose:** `backend/tests/fixtures/local_data/run_summary_response.json` is untracked, 4,838 bytes, and referenced by nothing (`grep -rn "run_summary_response" backend/` → no output). It must not be committed orphaned.

**Established facts:**

| Fact | Evidence |
|---|---|
| It is a genuine live capture | Body begins `{"id":"61fea483f499ccf25faafc4d","name":"Budding yeast cell cycle (Irons, J Theor Biol, 2009; SBML-qual; BoolNet; synchr…` |
| Top-level keys | `['id','name','tasks','outputs','run','metadata','submitted','updated']` |
| It has **no** `unknownFutureField` | Confirmed by key listing |
| The test uses an inline stub instead | `test_run_summary.py:19-30`, with a comment at `:17-18` stating it is *"representative test data, not a claimed R21 capture"* |

**Decision procedure:**

1. Confirm it is still unreferenced (Step 1).
2. Choose:

| | Option A — **wire it in (recommended)** | Option B — delete |
|---|---|---|
| Change | Replace the inline `RUN_SUMMARY` dict at `:19-30` with `RAW_BODY = (Path(__file__).parents[1] / "fixtures" / "local_data" / "run_summary_response.json").read_bytes()` and `RUN_SUMMARY = json.loads(RAW_BODY)` | `rm backend/tests/fixtures/local_data/run_summary_response.json` |
| Gain | Byte fidelity asserted against a real 4.8 KB upstream payload rather than a 9-key hand-written stub — a materially stronger passthrough guarantee | Smallest possible diff |
| Cost | The `unknownFutureField` assertion at `:106` must move to a separate test with a synthetic body, since the capture lacks that key. Adds one staged path | Discards the strongest available fidelity evidence |
| Staging | Adds the fixture as a **9th path**, in **Commit B** | Path list unchanged |

3. Whichever is chosen, the completion criterion is identical: **`grep -rn "run_summary_response" backend/` returns a real reference, or the file does not exist.**

**Do not** leave it on disk untracked and unstaged as a middle path — it will resurface in the next `git status` and eventually be swept into an unrelated commit.

---

## 9. Patch 3 — optional `Vary` forwarding

**Purpose:** add `"vary"` to `SAFE_RESPONSE_HEADERS` (`common/proxy.py:28-38`), which currently forwards `cache-control`, `content-disposition`, `content-type`, `etag`, `expires`, `last-modified`, `location`.

**What determines whether it is worth including — resolved by repository evidence:**

| Question | Evidence | Conclusion |
|---|---|---|
| Is there a shared cache that could serve a wrong variant? | `kustomize/overlays/biosim-gke/ingress.yaml` declares only `nginx.ingress.kubernetes.io/proxy-body-size: 20m` — **no response caching, no CDN** | No live risk today |
| Does the platform emit its own `Vary`? | `api/main.py:119-123` configures `CORSMiddleware` with an explicit `APP_ORIGINS` list (not `"*"`), so Starlette emits `Vary: Origin` | The CORS dimension is already covered |
| Is the proxy response origin- or header-dependent? | No caller headers are forwarded (`proxy.py:79`); the response depends only on path + query | No hidden variance |

⇒ **Desirable, not necessary.** Forwarding `Cache-Control`/`ETag` without the upstream's `Vary` is theoretically incorrect if a caching intermediary is ever introduced; today nothing is at risk.

**Recommendation:** include it. One frozenset member plus one test, it belongs to Commit A (shared helper), and it removes a latent correctness gap before any cache layer appears. **Defer it** only if Commit A's diff must be confined strictly to what the goal statement names — in which case file it as a follow-up rather than dropping it silently.

**If implemented, test it** in `backend/tests/projects/test_project_summary.py`: have the mock upstream return `Vary: Accept-Encoding` alongside `ETag`, and assert `response.headers["vary"] == "Accept-Encoding"`. Extending the existing `test_project_summary_fidelity_query_headers_and_credentials` header block is acceptable and keeps the diff smaller than a new test. Confirm the assertion does not collide with any `Vary: Origin` that `CORSMiddleware` may add — the fidelity test issues no `Origin` header, so it should not, but assert on the exact value and adjust to a substring check if the run shows otherwise.

---

## 10. Verification

**Working directory: `backend/`.** All three commands assume `cd backend` from the repo root (per `backend/CLAUDE.md` → Verification).

```bash
cd backend
uv run pytest -m "not integration"
uv run ruff check .
uv run mypy biosim_server tests
```

| Command | Success criterion | Recorded baseline |
|---|---|---|
| `uv run pytest -m "not integration"` | Exit 0. Passed count **≥ 213**, rising by the number of tests Patches 1–3 add; **skipped stays 15, deselected stays 4**; **zero failures, zero errors**. A drop in passed count means a test was removed, not fixed | `213 passed, 15 skipped, 4 deselected` in 28.88s |
| `uv run ruff check .` | Exit 0, literally `All checks passed!`. Ruff's default `F` ruleset includes F401, so this is also the unused-import gate for new test imports (`Path`, `json`) | `All checks passed!` |
| `uv run mypy biosim_server tests` | Exit 0, `Success: no issues found in N source files`. **N should be 126 or 127** — 126 today, +1 only if a new module is added (none is planned). A different N means an unexpected file entered scope | `Success: no issues found in 126 source files` |

**Optional targeted run while iterating** (not a substitute for the full suite):

```bash
uv run pytest tests/projects/test_project_summary.py tests/simulations/test_run_summary.py -q
```

Baseline `21 passed`; expect ~25–27 after Patches 1–3.

**Optional per-commit rigor (Option A only).** The three commands validate the *working tree*, not Commit A's tree in isolation. To confirm Commit A stands alone, after committing A: `git stash push --include-untracked` the Commit B paths, re-run the three commands, then `git stash pop`. Worth doing only if bisectability matters; the split has already been verified structurally.

**Do not stage until all three pass.**

---

## 11. Staging and scope control

Run all commands below **from the repository root**, not from `backend/`.

### Option A — Commit A, project summary (recommended)

```bash
git add backend/biosim_server/common/proxy.py \
        backend/biosim_server/dependencies.py \
        backend/biosim_server/projects/router.py \
        backend/tests/projects/test_project_summary.py
```

### Option A — Commit B, run summary

After Commit A is made. Add the fixture path **only** if Patch 2 chose "wire it in".

```bash
git add backend/biosim_server/api/main.py \
        backend/biosim_server/simulations/__init__.py \
        backend/biosim_server/simulations/router.py \
        backend/tests/simulations/test_run_summary.py \
        backend/tests/fixtures/local_data/run_summary_response.json   # ONLY if retained
```

### Option B — single commit, all eight paths

```bash
git add backend/biosim_server/{api/main.py,dependencies.py,common/proxy.py,projects/router.py,simulations/__init__.py,simulations/router.py} backend/tests/projects/test_project_summary.py backend/tests/simulations/test_run_summary.py
# plus, ONLY if Patch 2 retained it:
git add backend/tests/fixtures/local_data/run_summary_response.json
```

**Never `git add .`, `git add -A`, or `git add backend/`** — five unrelated untracked directories and `docs/directory_structure.md` are not gitignored and would be swept in. This plan file (`docs/passthrough-proxy.md`) is itself untracked and must likewise be staged deliberately, in its own commit or not at all.

### Scope verification

Run both, in this order, before every commit:

```bash
git status --porcelain frontend/
git diff --cached --stat
```

| Check | Must show | Must NOT show |
|---|---|---|
| `git status --porcelain frontend/` | **Nothing at all** (empty output) | Any `frontend/` path in any state — this work is backend-only |
| `git diff --cached --stat` (Commit A) | Exactly 4 paths: `common/proxy.py`, `dependencies.py`, `projects/router.py`, `tests/projects/test_project_summary.py` | `api/main.py`, either `simulations/` path, `test_run_summary.py`, the fixture, `.agents-new/`, `.cursor/`, `.freebuff/`, `.junie/`, `.vscode/`, `docs/`, `.gitignore`, anything under `frontend/` |
| `git diff --cached --stat` (Commit B) | Exactly 4 paths (+ fixture if retained) | Commit A's four paths, and every exclusion above |
| `git diff --cached --stat` (Option B) | Exactly the 8 listed paths (+ fixture if retained) | Every exclusion above |

**Expected magnitudes for cross-checking:** `common/proxy.py` ~107 lines added (~108 with Patch 3); `dependencies.py` +33/−0; `projects/router.py` +38/−2; `test_project_summary.py` ~170 lines plus Patch 1's additions. A materially different number means an unintended edit crept in — inspect with `git diff --cached <path>` before committing.

**If an unwanted path is already staged:** `git restore --staged <path>` (removes from the index, leaves the working tree untouched). Do not `git reset --hard`.

---

## 12. Commit strategy

Three commits, strictly in this order. Each is independently revertable and independently reviewable.

**Commit A — project summary passthrough (the goal of this plan).**
Paths: `common/proxy.py`, `dependencies.py`, `projects/router.py`, `tests/projects/test_project_summary.py` (+ Patch 3 if taken).
The message should state: the route returns upstream bytes unchanged; 2xx/3xx/4xx are mirrored while 5xx→502, timeout→504 and transport→502 are deliberate proxy policy; caller credentials and headers are never forwarded; the id is quoted into one path segment; `response_model=None` is required for byte fidelity and means the OpenAPI `200` body is intentionally unconstrained; the route complements rather than replaces `GET /projects` and `GET /projects/stats`.

**Commit B — run summary passthrough.**
Paths: `api/main.py`, `simulations/__init__.py`, `simulations/router.py`, `tests/simulations/test_run_summary.py` (+ fixture if retained).
The message should note it reuses the helper landed in Commit A, and that `includeData` is deliberately undeclared and forwarded opaquely because the upstream endpoint returns a byte-identical body with and without it.

**Commit C — `.gitignore` housekeeping (optional, separate, last).**
Adds `.agents-new/`, `.cursor/`, `.freebuff/`, `.junie/`, `.vscode/`. **Must not be mixed into A or B** — it touches no functional code, has a different reviewer audience, and mixing it would put six unrelated directories in the blame history of a proxy change. There is no repository-specific reason to combine them: nothing in the proxy work depends on those paths being ignored, and the staging discipline above already excludes them. Follow the existing `.gitignore` convention of a short rationale comment above each block. Decide separately what to do with `docs/directory_structure.md` and this plan file.

**If Option B is chosen:** A and B merge into one commit whose subject names **both** endpoints. Commit C stays separate regardless.

---

## 13. Rollback points

| # | Checkpoint | State | How to revert independently |
|---|---|---|---|
| R0 | Before any edit | Current working tree, nothing staged | Baseline. Optional safety net: `git stash push --include-untracked -m "pre-patch baseline"` (this also stashes untracked feature files; `git stash pop` to restore) |
| R1 | After Patch 1 (tests only) | `test_project_summary.py` extended | File is untracked; revert by deleting the appended tests. **Zero production risk** — no source file changed |
| R2 | After Patch 2 | Fixture wired in or deleted | If wired in: restore the inline `RUN_SUMMARY` at `test_run_summary.py:19-30`. If deleted: the file is unrecoverable via git (never committed) — **copy it elsewhere before `rm`** |
| R3 | After Patch 3 | `vary` in `SAFE_RESPONSE_HEADERS` + its test | Single-line revert in `common/proxy.py:28-38` plus the assertion. Independent of R1/R2 |
| R4 | After validation, before staging | Tree green, index empty | Everything above still individually revertable; nothing committed |
| R5 | After staging, before commit | Index populated | `git restore --staged <path>` per path. Working tree untouched |
| R6 | After Commit A | Project summary committed | `git revert <A>` or `git reset --soft HEAD~1` (local only, not pushed) |
| R7 | After Commit B | Both endpoints committed | `git revert <B>` alone — verified safe: nothing in Commit A references `run_summary_router` |
| R8 | After Commit C | `.gitignore` updated | `git revert <C>` — touches no functional code |

**Highest-risk irreversible action in this plan:** deleting the fixture at R2. It exists nowhere in git history. Copy it out first if the delete branch of Patch 2 is taken.

---

## 14. Final checklist

### Preflight

- [ ] 1. `cd` to repo root. Confirm branch is `feature/passthrough-proxy-api` and `git diff --cached --stat` is empty
- [ ] 2. Confirm `git status --porcelain` matches the recorded 15 entries; investigate any drift before proceeding
- [ ] 3. Confirm `upstream_url` still reads `quote(segment, safe="")` (`common/proxy.py:41-43`)
- [ ] 4. Confirm `grep -rn "run_summary_response" backend/` still returns nothing
- [ ] 5. Confirm `git status --porcelain frontend/` is empty
- [ ] 6. **Decide the commit split** — Option A (two commits, recommended) or Option B (one commit)
- [ ] 7. **Decide Patch 2** — wire the fixture in (recommended) or delete it (copy it out first)
- [ ] 8. **Decide Patch 3** — include `vary` (recommended) or file as follow-up

### Implement

- [ ] 9. **Patch 1** — append the encoding parametrization and the `%2F`-rejection test to `backend/tests/projects/test_project_summary.py`. No source file changes
- [ ] 10. **Patch 2** — wire the fixture into `backend/tests/simulations/test_run_summary.py` (moving the `unknownFutureField` assertion to a separate synthetic-body test) **or** delete the fixture
- [ ] 11. **Patch 3 (optional)** — add `"vary"` to `SAFE_RESPONSE_HEADERS` in `backend/biosim_server/common/proxy.py` and assert it in the project fidelity test

### Validate (from `backend/`)

- [ ] 12. `uv run pytest -m "not integration"` → 0 failures, passed ≥ 213, skipped 15, deselected 4
- [ ] 13. `uv run ruff check .` → `All checks passed!`
- [ ] 14. `uv run mypy biosim_server tests` → `Success: no issues found in 126 source files`

### Stage and scope-check (from repo root)

- [ ] 15. Stage Commit A's four paths explicitly — never `git add .` / `-A` / `backend/`
- [ ] 16. `git status --porcelain frontend/` → **empty**
- [ ] 17. `git diff --cached --stat` → **exactly** Commit A's four paths, nothing else; spot-check line counts against the expected magnitudes
- [ ] 18. Commit A with a message covering the passthrough contract, error policy, credential non-forwarding, and the intentional `response_model=None`

### Commit B

- [ ] 19. Stage Commit B's four paths (+ fixture only if retained)
- [ ] 20. Re-run checks 16–17 for Commit B's expected path list
- [ ] 21. Commit B, noting helper reuse and the deliberately opaque `includeData`

### Optional housekeeping — separate commit

- [ ] 22. Add `.agents-new/`, `.cursor/`, `.freebuff/`, `.junie/`, `.vscode/` to `.gitignore` with a rationale comment matching the file's existing convention
- [ ] 23. Stage `.gitignore` **alone**; confirm `git diff --cached --stat` shows only that file; commit
- [ ] 24. Decide separately what to do with `docs/directory_structure.md` and `docs/passthrough-proxy.md`

### Final

- [ ] 25. `git status --porcelain` → only the intentionally-unstaged unrelated paths remain; nothing from the feature is left behind
- [ ] 26. `git log --oneline -3` → the commits read as a clean, separable sequence

---

## 15. Open decisions

Two items require a human decision before implementation starts:

1. **The commit split.** The eight-path staging list is a two-endpoint change, while the stated goal names only `GET /projects/{id}/summary`. See section 5. - Option A
2. **Patch 2's fixture disposition.** Wiring the real 4.8 KB capture in is the stronger option but is not a drop-in swap. See section 8. - Option A

Everything else in this plan is determined by repository evidence and needs no further judgment.
