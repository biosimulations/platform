# Backend Contributions Audit — 2026-09-30

**Repo:** biosimulations/platform
**Branch:** `chore/verify-workflow-id` (HEAD `22ab61f`)
**Scope:** `backend/` (FastAPI API + Temporal workers, v0.10.0)
**Baseline confirmed:** `ruff` clean · `mypy` clean (172 files) · tests 908 passed / 15 skipped (`-m "not integration"`)

Audit of the full backend surface. Nothing below changes API contracts or
Temporal workflow logic — all items are minor, self-contained fixes. Every
finding cites `file:line` and is either verified against current code
(**CODE-PROVEN**) or confirmed with a runtime probe (**MEASURED**).

---

## P1 — Verified bugs (small, safe fixes)

**Progress: 7 of 7 done** (the whole P1 section is fixed and verified).

### 1. Every log line from `api/main.py` is emitted TWICE — MEASURED — ✅ FIXED
`biosim_server/log_config.py:24-41` — `setup_logging()` added the same console
handler to **both the root logger and the named logger**, so each record printed
via the child handler *and* propagated to the root handler. Reproduced: one
`info()` → two identical JSON lines. Impact: doubled log volume in production
(`kubectl logs`, CloudWatch).

**Fix:** drop the `logger.addHandler(console_handler)` line — root propagation
already covers the named logger.

**Status: FIXED** — applied in `backend/biosim_server/log_config.py` (one line
deleted, working tree as of 2026-09-30; not yet committed). Re-verified after
the change:

| | root handlers | named handlers | `propagate` | lines from one `info()` |
|---|---|---|---|---|
| Before | 1 | 1 | True | **2** (bug reproduced) |
| After | 1 | 0 | True | **1** |

- `ruff check` on `log_config.py` + `api/main.py` → all checks passed.
- `mypy log_config.py` → no issues.
- `pytest tests/common/test_auth_observability.py tests/common/test_roles.py` → 31 passed.
- Both callers (`api/main.py:64`, `projects/reindex_cli.py:32`) unaffected; the
  `logger` argument is now unused but kept for caller compatibility.
- No other `addHandler` / `propagate = False` in `biosim_server` that could
  reintroduce duplication.

**Residual nits (out of scope of this fix, not blocking):**
- `log_config.py:39` comment still says *"root logger and uvicorn logger"* — no
  uvicorn handler was ever added here; reword.
- `setup_logging()` is not idempotent: calling it twice in one process adds a
  second identical root handler and brings the duplication back. Guard with
  `if not root_logger.handlers`.

### 2. Malformed `simulator` query param returns 500 instead of 400 — MEASURED — ✅ FIXED
`biosim_server/api/main.py:334` — `name, version = simulator.split(":")`. A
param like `copasi:4:34` → `ValueError: too many values to unpack` → unhandled
500 (reproduced against the real app with the test fixtures).

**Fix:** `split(":", 1)` / `partition(":")` so the existing 400 "Simulator not
found" path handles the malformed value.

**Status: FIXED** — `api/main.py:334` is now
`name, version = simulator.split(":", 1)` (working tree as of 2026-09-30; not
yet committed). A malformed value now falls through to the pre-existing
`HTTPException(400, f"Simulator {simulator} not found.")` at line 347.

Regression tests added in `backend/tests/api/test_main.py`:
- `test_verify_omex_malformed_simulator_returns_400_not_500` — parametrized over
  `copasi:4:34`, `copasi:4.34.251:extra`, `copasi:`, `:4.34.251`, `:`; asserts
  400, the value echoed in `detail`, and that no ledger row is written and no
  workflow is started.
- `test_verify_omex_wellformed_simulator_version_still_resolves` — control that
  `copasi:4.34.251` still resolves and starts the workflow (guards against
  over-splitting).
- Shared `_post_verify_omex()` helper keeps the mock scaffolding in one place.

Verified the test actually catches the bug: reverting line 334 to `split(":")`
makes the two multi-colon cases fail with 500; restoring `split(":", 1)` makes
all 7 pass. The three single-colon cases (`copasi:`, `:4.34.251`, `:`) pass
either way — they document the contract rather than reproduce the crash.

- `ruff check` + `mypy` on `api/main.py` and `tests/api/test_main.py` → clean.
- Full suite: **914 passed, 15 skipped** (`-m "not integration"`), up from the
  908/15 baseline by exactly the 6 new tests — no regressions.

**Scope check:** `split(":")` appears only here in production code
(`projects/database.py:158` and `pages/mapping.py:239` are unrelated URN /
language parsing using `[-1]`), so there is no sibling site needing the same fix.

### 3. Wrong variable in comparison log message — CODE-PROVEN — ✅ FIXED
`biosim_server/biosim_verify/activities.py:136` —

```
"Comparing {simulation_version_j}:run={run_id_i} and {simulation_version_j}:run={run_id_j}"
```

used `simulation_version_j` twice; the first should be `simulation_version_i`
(in scope at line 68). One-line fix.

**Status: FIXED** — `activities.py:136` now reads
`f"Comparing {simulation_version_i}:run={run_id_i} and {simulation_version_j}:run={run_id_j} ..."`
(working tree as of 2026-09-30; not yet committed). Previously the log paired a
version from one simulator with a run id from a different one.

Verified the variable pairings are now consistent, not just syntactically valid:
- `run_id_i` (:66) and `simulation_version_i` (:68) are both bound in the outer
  `run_index_i` loop.
- `run_id_j` (:96) and `simulation_version_j` (:97) are both bound in the inner
  `run_index_j` loop.
- This now matches the `ComparisonStatistics(simulator_version_i=…,
  simulator_version_j=…)` object constructed two lines above (:102–104), which
  was always correct.

No sibling instances: swept every other log in the file for the same
wrong-variable pattern — the four `logger.error` calls (:74, :106, :114, :123)
and the `err_msg`/error-message strings all reference `_i`/`_j` correctly.

- `ruff check` + `mypy` on `activities.py` → clean.
- `pytest tests/biosim_verify/test_generate_statistics.py` → 3 passed.
- Full suite: **914 passed, 15 skipped** — unchanged, no regressions.

**Caveat:** unlike items 1–2, this fix has **no test proving it** — the string is
only observable through the activity's logger and no test captures it. A `caplog`
assertion that the emitted line pairs `simulation_version_i` with `run_id_i`
would close that gap; not added here.

### 4. `get_sim_run()` ignores the configured API base URL — CODE-PROVEN — ✅ FIXED
`biosim_server/biosim_runs/biosim_service.py:84` — read
`os.environ.get('API_BASE_URL')`, an env var that is set **nowhere** (not in
kustomize, `config.py`, or tests), while every other method uses
`get_settings().biosimulations_api_base_url`. Any staging/mock override
silently didn't apply to the polling path.

**Fix:** use `get_settings().biosimulations_api_base_url` (and drop the
always-true `assert` on line 85).

**Status: FIXED** — `biosim_service.py:84` is now
`api_base_url = get_settings().biosimulations_api_base_url`; the always-true
`assert` and the now-unused `import os` are gone. All five methods of
`BiosimServiceRest` now read the same configured base URL.

Test: `test_get_sim_run_uses_configured_api_base_url` in
`tests/biosim_runs/test_biosim_service.py` — fakes the session, points the
settings at `https://staging.example.org`, and asserts the request URL is
`https://staging.example.org/runs/abc123`; it then re-runs with
`API_BASE_URL=https://ignored.example.org` in the environment and asserts the
env var is still ignored. Reverting to the old line makes the test fail.

### 5. `shutdown_standalone()` never clears the OMEX db service — CODE-PROVEN — ✅ FIXED
`biosim_server/dependencies.py:210-234` — cleared every other global except
`set_omex_database_service(None)`; after lifespan shutdown,
`get_omex_database_service()` still returned a service bound to a closed Motor
client. Mattered for app restarts / test isolation.

**Fix:** one line — add `set_omex_database_service(None)`.

**Status: FIXED** — added to `shutdown_standalone()` alongside the other
`set_*_service(None)` calls, under the existing "shares the motor client closed
via db_service above" comment.

Tests: new `tests/common/test_dependencies.py`
- `test_shutdown_standalone_clears_every_service_handle` — asserts all ten
  globals are `None` afterwards, OMEX included.
- `test_shutdown_standalone_closes_shared_services` — asserts the db/file
  services that own the shared client are actually `close()`d, not just unset.

Reverting the one line makes the first test fail.

### 6. Blocking DNS resolution on the event loop — CODE-PROVEN — ✅ FIXED
`biosim_server/compatibility/router.py:56` — `socket.getaddrinfo()` ran
synchronously inside the async `_download_archive`/`check_compatibility` path;
a slow resolver stalled the whole pod's event loop for seconds.

**Fix:** `await asyncio.to_thread(socket.getaddrinfo, ...)`.

**Status: FIXED** — `assert_archive_url_safe` is now `async` and the lookup is
`await asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)`;
the single call site (inside `_download_archive`'s redirect loop) awaits it.
The pre-existing SSRF tests are unchanged and still pass — the patch target
(`router.socket.getaddrinfo`) is unaffected by the thread hop.

Test: `test_archive_dns_resolution_does_not_block_event_loop` in
`tests/compatibility/test_router.py` — runs the request under
`httpx.AsyncClient` + `ASGITransport` while a background ticker counts loop
iterations, with `getaddrinfo` stubbed to `time.sleep(0.5)`. Asserts >=5 ticks
during the lookup; a blocking lookup yields ~0. Ran 5x consecutively, stable.

### 7. Unbounded archive download into memory — CODE-PROVEN — ✅ FIXED
`biosim_server/compatibility/router.py:94` — `await resp.read()` with no size
cap; a hostile `archive_url` could OOM a pod (redirects are followed, so it's
attacker-influenceable). Related: uploads were read fully into memory with no
cap (`biosim_omex/omex_storage.py:44`, `compatibility/router.py:126`).

**Fix:** reject on oversized `Content-Length` and cap the streamed read.
Suggested cap ~100 MB (constant choice is the only "decision" here —
rubber-stamp).

**Status: FIXED** — the cap is the rubber-stamped **100 MB**
(`MAX_OMEX_MB` / `MAX_OMEX_BYTES` in `biosim_omex/omex_storage.py`, one knob
for both paths). All three ingestion paths are covered:
*Corrected 2026-10:* the cap is 100 **MiB** (104,857,600 bytes), not 100 MB.
"All three ingestion paths" means the three caller-reachable HTTP paths;
internal local/raw helpers and worker archive reads are not capped. The
multipart cap originally ran only after FastAPI had parsed and spooled the
whole form; receipt is now bounded by `common/upload_limit.py` (PR #120, B1).
- **Download** (`compatibility/router.py:_download_archive`) — rejects an
  oversized declared `Content-Length` up front, then streams via
  `resp.content.iter_chunked(1 MiB)` with a running total, so an absent or
  lying `Content-Length` is still caught mid-stream.
- **Upload to `/compatibility/check`** — `read_upload_capped(uploaded_file)`
  reads in 1 MiB chunks and aborts past the cap. `HTTPException` is re-raised
  ahead of the generic `except Exception` so the 413 isn't flattened to 400.
- **Upload to `/verify/omex`** — `get_cached_omex_file_from_upload` now goes
  through the same helper instead of `await uploaded_file.read()`.

Over-limit requests return **413** with a `...MB limit` detail.
`read_upload_capped` resolves its cap at call time (`max_bytes: int | None =
None`), so the limit stays a single module-level knob rather than a constant
duplicated across modules.

Tests — `tests/compatibility/test_router.py` (fake `aiohttp` session/response):
- `test_check_compatibility_rejects_oversized_declared_content_length`
- `test_check_compatibility_rejects_oversized_stream` (no `Content-Length`)
- `test_check_compatibility_accepts_archive_under_the_cap` (control)
- `test_check_compatibility_rejects_oversized_upload`

and `tests/biosim_omex/test_omex_storage.py`:
- `test_read_upload_capped_returns_body_under_the_limit`
- `test_read_upload_capped_rejects_oversized_body`
- `test_get_cached_omex_file_from_upload_is_capped` (asserts nothing is written
  to storage when the cap trips)

Reverting items 6+7 together makes all 5 router tests fail; restoring them makes
all pass.

**Deliberately not capped:** `get_cached_omex_file_from_local`
(`omex_storage.py:96`) reads our own cache directory, not caller input, and was
outside the finding.

**No client impact:** 413 is a new status on these paths, but the frontend does
not reference `/compatibility`, so nothing there needs updating.

**Gates for items 4-7:** `ruff check .` clean - `mypy biosim_server tests`
clean (173 files) - full suite **925 passed, 15 skipped**
(`-m "not integration"`), up from 914 by exactly the 11 new tests - no
regressions.

---

## P2 — Small robustness / consistency fixes

### 8. Deprecated `pydantic_encoder` in the Temporal converter — MEASURED side effect
`biosim_server/common/temporal/converter.py:32` — `default=pydantic_encoder`
is `PydanticDeprecatedSince20`; replace with
`pydantic_core.to_jsonable_python`. This is most of the ~4k deprecation
warnings in the test run. (Note: this converter is currently only wired into
tests — see Observations.)

### 9. Auth0 Management API user id not URL-quoted — MEASURED
`biosim_server/common/auth/auth0_management.py:187,204,220` — `sub` values like
`auth0|123` go into the path raw; verified that httpx does **not**
percent-encode `|`.

**Fix:** `quote(user_id, safe="")` in the three call sites.

### 10. Upstream error text leaked to clients — CODE-PROVEN
`biosim_server/compatibility/router.py:174` —
`detail=f"Failed to fetch simulator information: {e}"` returns raw upstream
exception text; inconsistent with the sanitizer discipline in
`common/upstream.py`.

**Fix:** generic 503 detail, keep the exception in the log. (Lines 99/141 are
caller-input parse errors — fine to keep as-is.)

### 11. Deprecated `activity.logger.warn`
`biosim_server/biosim_runs/activities.py:55` → `.warning`.

### 12. No-op truthy check in simulator filter — CODE-PROVEN
`biosim_server/biosim_runs/biosim_service.py:196` —

```python
if 'image' in sim and 'url' and sim['image'] and 'url' in sim['image'] and ...
```

The bare `'url'` string is always truthy; remove it (it was clearly meant to be
a real check or nothing).

### 13. Activities bypass dependency injection — CODE-PROVEN
- `biosim_server/biosim_verify/activities.py:31-33` constructs
  `BiosimServiceRest()` directly (plus a dead `is None` check on a constructor
  call).
- `biosim_server/biosim_runs/activities.py:83` re-constructs it when the
  injected service is already in hand at line 47.

The worker calls `init_standalone()` (`worker/worker_main.py:24`) so
`get_biosim_service()` is always set — switching to DI matches the other
activities and makes both testable via overrides
(`tests/biosim_verify/test_generate_statistics.py:37` and one spot in test
files need their mocks updated).

### 14. Redundant ownership clause — CODE-PROVEN
`biosim_server/api/main.py:480` —
`original != workflow_input or original.owner_sub != owner_sub`: model
equality already covers `owner_sub`; second clause is dead.

### 15. Preflight HDF5 fetches are sequential — CODE-PROVEN
`biosim_server/api/main.py:808-827` (`_load_hdf5_metadata_for_preflight`)
awaits each run id in turn — N upstream RTTs serialized; safe to
`asyncio.gather(..., return_exceptions=True)` since failures are already
per-run caught.

### 16. Cosmetic / polish batch
- `biosim_server/api/main.py:858` — unreachable `logger.info("Server started")`
  after `uvicorn.run`.
- `biosim_server/biosim_runs/models.py:70-76` — trailing commas make
  `'QUEUED',` a 1-tuple; Python's enum machinery normalizes it (verified: all
  members compare equal to their names), but it's fragile — drop the commas.
- `biosim_server/biosim_verify/runs_verify_workflow.py:72` — logs the whole
  `biosimulations_run_ids` list inside the per-run loop; log the single id.
- `biosim_server/biosim_runs/database.py:85,100` — dead
  `type(document) is list` checks (`find().to_list` always returns list) and a
  duplicated doc-mapping loop worth extracting to a helper.
- `biosim_server/worker/worker_main.py:16,22` — dead `interrupt_event` and
  pointless `random.seed(667)`.
- `biosim_server/common/storage/gcs_aio.py:104-111` — leftover debug `main()`
  in a library module.
- `biosim_server/api/main.py:304,715` — descriptions missing a closing paren:
  `*atol_scale.`.

---

## Observations (real, but each needs a decision — excluded from the minor list)

- **Child execution timeout inconsistency.** `biosim_verify/omex_verify_workflow.py:62`
  caps children at `execution_timeout=10min`, but the child's own poll activity
  allows `start_to_close=20min` (+5min submit). A legitimately slow simulation
  kills the whole verification. `SimulationRunWorkflow`
  (`simulations/workflow.py:79`) gives the same children 30min. Probably an
  oversight, but changing it alters verification behavior.
- **One failed simulator fails the whole verification.** `omex_verify_workflow.py:66-67`
  gathers child workflows without `return_exceptions=True` — one failed child
  fails everything even if the others succeeded (`SimulationRunWorkflow`
  handles per-child failures). Partial-results semantics = product decision.
- **`/verification_ids` unbounded.** `biosim_verify/database.py:88`
  (`to_list(length=None)`) returns every ledger row with no pagination — fine
  at today's volume, a contract change later.
- **No HTTP connection pooling.** `BiosimServiceRest` opens a fresh
  `aiohttp.ClientSession` per call (all five methods) — a session-lifecycle
  refactor is worthwhile but not "minor".
- **Bare-name simulator picks the LAST matching version** (`api/main.py:340-342`),
  trusting upstream list order.
- **Dead production modules.** `common/hpc/slurm_service.py` and
  `common/ssh/ssh_service.py` are imported by nothing in production code (only
  their own tests) — removal is a scope call. Similarly,
  `biosim_verify/hdf5_compare.py` is unused in production (kept as the
  reference implementation cited by `biosim_verify/activities.py:161`).
- **Converter test/prod mismatch.** Production API/worker use temporalio's
  default converter (which hits its deprecated pydantic-v1-compat path per
  request) while tests use the custom `pydantic_data_converter` — aligning them
  is the right long-term fix but touches payload serialization → Temporal
  history compatibility.
- **DNS-rebinding TOCTOU in `_download_archive`** (re-resolve can differ from
  the checked IP) — a real fix means pinning the resolved IP, beyond a minor
  patch.

---

## Suggested execution order

1. **First PR:** items 1–5 + 8–11 as one batch — all tiny, independent, each
   with an obvious test. (Items 1–5 are already applied in the working tree and
   verified; they just need to ride along in this batch.)
2. **Second PR (hardening):** items 6–7. (Also already applied and verified in
   the same working tree — 11 new tests cover them.)
3. **Third PR (polish):** items 12–16.
4. Observations → separate issues/discussions; each needs a product or
   architecture decision first.
