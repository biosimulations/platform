# Verification 500 / CORS Fix — Implementation Plan

Investigation date: 2026-10-23. **Revised: 2026-10-26** (see §0). Repository: `/Users/novrusshehaj/Github/UCHC/platform`. Branch: `fix/CORS-Error`. Originally inspected HEAD: `5e81f4d932c6301f5203ac93ed47390588f050b1`; revised against `2ca6e77` (PR #157). Deployed image at investigation time: `backend-0.10.2` (matches `backend/biosim_server/version.py`).

This is a planning artifact. Evidence labels: **CONFIRMED** = reproduced or read from current code/logs; **INFERRED** = consequence of inspected behavior, not reproduced end-to-end; **UNVERIFIED** = needs runtime or external confirmation.

---

## 0. Revision Notes (2026-10-26)

The root-cause diagnosis in the original plan was correct and is unchanged. These corrections came from a review against Cloud Logging (10 days), the code, and local reproductions:

1. **The scope was understated.** It isn't one run. **Six** verifications produced **127** NaN 500s in 10 days. Two of them are confirmed as COPASI 4.45.296 self-comparisons scoring `nan`. This keeps happening (§1, §2.2).
2. **The `1e10` sentinel (old §3.1) is withdrawn.** It pre-empted the `null` the plan itself recommended, and the Score column would show it as **"1000000000000.0%"** (§3.2).
3. **Field validators (old §3.2) are withdrawn.** They covered only `score` and `values`. Other float fields can also carry NaN, including a user-controlled one (`rel_tol=nan` is accepted). The fix belongs at the response boundary (§2.3, §3).
4. **The frontend retry bound (§5) must count failures that have no status,** not only 5xx. During the incident the browser saw no status at all.
5. **The catch-all middleware should re-raise after sending its 500** (§4.1).
6. **Fixes 1 and 2 and §3.3 are implemented in PR #157** (commits `2ca6e77` and `83e461a`). What remains is §3.4 (schema annotation, optional), §5 (frontend) and §6 (optional).
7. Evidence count corrected: all 75 errors in the original 48-hour pod window were run 212, not 74.

---

## 1. Executive Summary

**CONFIRMED — root cause.** `GET /verify/{workflow_id}` returns **500** for any verification whose stored output contains a non-finite float (`nan`/`inf`) **anywhere in the response tree**. In practice that is usually `workflow_results.comparison_statistics[...].score`, and it can also be raw data or settings (§2.3). The handler succeeds. Starlette fails while rendering the response body, because `JSONResponse.render` calls `json.dumps(..., allow_nan=False)` (`starlette/responses.py:184-190`). The raised `ValueError: Out of range float values are not JSON compliant: nan` propagates past `CORSMiddleware`, so the 500 is emitted **without** `Access-Control-Allow-Origin`, and the browser then reports it as a CORS error. The CORS error is a symptom; the NaN is the cause.

**CONFIRMED — evidence.**
- **Pod logs (48 h):** in the original window, 75 occurrences of that exact `ValueError`, **all 75** for `compose-api-prod-comparison-212-69b4e144-b3b3-4e94-94cc-3127e88ff460`, and zero 503s on `/verify`.
- **Cloud Logging (10 days):** **127 API 500s, all of them this `ValueError`**, across **six** verifications (`compose-api-prod-comparison-` 212 ×85, 82 ×10, 253 ×8, 211 ×8, 92 ×8, 83 ×8), from 2026-10-22 20:05 to 2026-10-23 20:40 UTC.
- **Worker logs:** run 212 (BIOMD0000000155, `autogen_report_for_task1`, variable 17 of 18 — 0-indexed 16) and run 82 (BIOMD0000000024) both show **COPASI 4.45.296 compared with its own run scoring `nan`**.
- **Reproduction:** reproduced locally against the repo's own `starlette 0.46.2` / `fastapi 0.115.14` with the real `VerifyWorkflowOutput` model: NaN in a `response_model` gives a 500 with no ACAO header.

**CONFIRMED — why the listing disagrees with the detail view.** `get_verify_output` records the terminal status in the verification ledger (step 5, `api/main.py`) *before* returning, and it is the **only** writer of that status. Serialization fails after that write. The ledger therefore says `COMPLETED`, `GET /verification_ids` shows `COMPLETED`, and every detail fetch for that ID fails. The detail dialog shows `PENDING` because of a frontend fallback (§5). This is the observed "list says Completed, detail shows PENDING/error" mismatch.

**Scope of the bug: recurring, not a one-off.** A COPASI output with a NaN variable is enough to trigger it, and one compose-api batch on 2026-10-22 hit it six times. It hides itself over time: each run turns into the ledger-backed `404 "results are no longer retained"` once its Temporal history ages out. Run 212 went from 500 to 404 at 2026-10-23 20:41:32 UTC, about 24 h after it completed (**INFERRED:** namespace retention ≈ 1 day). That is why the original sweep found 0 live 500s, even though six runs had failed.

**Fix order (revised):**

1. **Render non-finite floats as `null` at the response boundary.** App-wide, covers every field and already-stored results. **Done in PR #157.** No sentinel, no per-field validators.
2. **Defense in depth: unhandled errors carry CORS headers.** Middleware inside CORS that re-raises. **Done in PR #157.**
3. **Reject non-finite tolerances at the request** (`allow_inf_nan=False`). **Done in PR #157** (`83e461a`).
4. **Optional: schema honesty.** Annotate `score`/`values` as `list[float | None]`.
5. **Frontend:** stop polling after repeated failures, **including status-less ones**, and surface the error instead of `PENDING` forever.
6. **Optional:** JSON error body with a request ID.

---

## 2. Root Cause Detail

### 2.1 The failing path

```
GET /verify/{workflow_id}
  → get_verify_output()                       # api/main.py, succeeds
      → handle.query("get_output")            # returns VerifyWorkflowOutput
      → _authorize_verification_read(...)     # succeeds
      → _reconcile_terminal_status(...)       # succeeds
      → _record_terminal_status(...)          # LEDGER WRITE HAPPENS HERE
      → return workflow_output
  → FastAPI serializes via response_model     # pydantic → dict (mode="json" keeps nan as a float)
  → JSONResponse.render()                     # json.dumps(..., allow_nan=False)
  → ValueError: Out of range float values are not JSON compliant: nan
  → propagates past CORSMiddleware (no response started yet)
  → ServerErrorMiddleware emits plain-text 500, no ACAO header
```

The ledger write at step 5 is why the listing and the detail view disagree. It is not itself a bug; it is why the failure is invisible from the listing.

Note that Pydantic's own `model_dump_json()` would have rendered `nan` as `null` (`ser_json_inf_nan` defaults to `'null'`). FastAPI's path uses `model_dump(mode="json")`, which keeps the float, and then hands it to Starlette's strict encoder. **CONFIRMED** locally.

### 2.2 Where the NaN originates

`backend/biosim_server/biosim_verify/activities.py`, `calc_stats` (line 156), the vectorized comparison used by the workflow:

```python
score: NDArray1f = np.nanmax(a=np.divide(numerator, denominator), axis=1)   # line 189
...
is_close: NDArray1b = np.less(score, 1.0)
return is_close, score
```

`np.nanmax` returns `nan` when every element in a row is `nan`. That is what happened in run 212: a variable COPASI left all-NaN scored `nan` even against its own run. The worker logged it:

```
Comparing copasi:4.45.296:run=6ada6fdc041d31202095031d and copasi:4.45.296:run=6ada6fdc041d31202095031d
  for dataset BIOMD0000000155_url.sedml/autogen_report_for_task1
  score: [ 0.  0. …  0. nan  0.] is_close: [ True … False  True]
```

Two other routes to NaN:
- **`max1`/`max2` are NaN.** `atol_array` uses `np.max`, not `np.nanmax`, so a NaN tolerance passes through to the whole row.
- **Zero denominator.** `np.divide` can produce `inf` when `abs_tol_min` is 0 and the data is 0.

The caller then does `score.tolist()` (line 133) and assigns it to `ComparisonStatistics.score` (line 138), an `Optional[list[float]]` that accepts `nan` and `inf`.

`hdf5_compare.compare_arrays` (`hdf5_compare.py:42-43`) does guard (`return False, 1e10`), but **nothing in `biosim_server` calls it**. It is dead code, referenced only in `calc_stats`' docstring. It is not a live "legacy path" and not a convention the workflow follows.

**Side observation (CONFIRMED, out of scope):** in run 212, COPASI's datasets had shape `(3, 2)` / `(18, 2)` while PySCeS had `(3, 1001)` / `(18, 1001)`. So the cross-simulator comparisons failed on shape mismatch, and COPASI produced only two time points plus a NaN variable. See §8.

### 2.3 Every carrier, not just `score`

`VerifyWorkflowOutput` can carry a non-finite float in at least four places. Any one of them 500s the response:

| Carrier | Source | Notes |
|---|---|---|
| `workflow_results.comparison_statistics[...].score` | `calc_stats` (§2.2) | The observed case. |
| `workflow_results.sim_run_data[].data.values` (`Hdf5DataValues.values`) | Raw simulator output, when `include_outputs=true` | Simulators legitimately emit NaN. |
| `compare_settings.rel_tol` / `abs_tol_min` / `abs_tol_scale` | **The client**, via the query string on `POST /verify/omex` and `POST /verify/runs` (`api/main.py:350`, `:830`) | **CONFIRMED:** `rel_tol=nan` is accepted as a float NaN. One request creates a verification whose every GET 500s, and the POST's own response 500s too. |
| `workflow_results.sims_run_info[].hdf5_file...attributes[].value` (`HDF5Attribute.value`, `ATTRIBUTE_VALUE_TYPE` includes `float`/`list[float]`) | Upstream HDF5 metadata | **INFERRED:** not observed, but nothing prevents it. |

**Consequence:** guarding fields one by one cannot be made complete, and every new float field would reopen the bug. The fix belongs where every response passes through (§3.1).

### 2.4 Why CORS is missing (mechanism)

Starlette's middleware stack is built as (`starlette/applications.py:84-94`):

```
ServerErrorMiddleware  (outermost)
  → user middleware, in registration order (last-added is outermost)
      → CORSMiddleware
          → MultipartBodyLimitMiddleware
              → ExceptionMiddleware  (innermost)
                  → router → handler
```

`CORSMiddleware.send()` only decorates messages of type `http.response.start` (`cors.py:147-153`). Here `JSONResponse.render()` raises *inside the route handler's response construction*, so no `http.response.start` is ever sent through CORS. The exception unwinds to `ServerErrorMiddleware`, which sits **outside** CORS and writes its own plain-text 500, so no ACAO header is added. The browser sees a cross-origin response with no CORS header and reports a CORS failure, masking the 500. To the frontend, the request then has **no status at all** (§5).

Handled errors behave differently: `HTTPException` is caught by `ExceptionMiddleware` (inside CORS), which builds a normal response that then passes **through** CORS and gets decorated. That is why 401/404 carry the header and this 500 does not (confirmed live: run 212's current 404 carries ACAO).

---

## 3. Fix 1 — Non-Finite Floats Never Fail a Response (Primary)

### 3.1 Recommended (implemented): render non-finite floats as `null` at the response boundary

**Status: DONE in PR #157** (`2ca6e77`). **File:** `backend/biosim_server/common/json_response.py`.

`NonFiniteSafeJSONResponse` subclasses Starlette's `JSONResponse` and is the app's `default_response_class` (`api/main.py`):

```python
def render(self, content: Any) -> bytes:
    try:
        return super().render(content)            # unchanged fast path
    except ValueError:
        return super().render(_finite_or_none(content))   # nan/±inf → None, re-render
```

How it behaves:
- **Every carrier (§2.3) and every route.** That includes included routers and the POST responses.
- **Already-stored results** in Temporal are covered too, with no data migration.
- **Zero cost on ordinary responses.** The content is walked only after the strict encoder has rejected it.
- **Same output as Pydantic.** `null` is what Pydantic's `model_dump_json` emits for these values.
- **The frontend already handles `null`.** `formatScore` shows `—` (`VerificationTable.vue:67-69`), `useVerificationAnalytics.ts:298` uses `?? null`, and a `null` in chart data renders as a gap.
- **Other failures still raise.** A `TypeError` for a non-JSON type, or a circular reference, raises exactly as before.

At revision time the walk's cycle detection, which raises `ValueError("Circular reference detected")` instead of recursing to `RecursionError`, and its tests are **uncommitted local changes**. Commit them to PR #157.

### 3.2 Rejected: a finite sentinel in `calc_stats`

The original §3.1 proposed `score = np.where(np.isfinite(score), score, 1e10)`. **Do not implement it.**

- **It pre-empts `null`.** It runs before storage, so new runs never reach the boundary that would render `null`, and consumers see `1e10` instead. That contradicts the plan's own recommendation (`null`) in the original §3.2 and §8.
- **It is a visible UI regression.** `formatScore(1e10)` renders **"1000000000000.0%"** in the Score column (**CONFIRMED**), where the boundary fix shows `—`. The CSV export is worse in a different way: it writes the raw value (`r.score !== null ? r.score : ''`, `VerificationTable.vue:79`), so a sentinel would export as **`10000000000`** — an unformatted magnitude a reader would have to interpret.
- **It buys nothing.** `is_close` is already `False` for `nan`, because `np.less(nan, 1.0)` is False. And §3.1 already makes the value serializable.
- **The precedent it cited is dead code** (`hdf5_compare.compare_arrays` is unused; §2.2).

Leave `calc_stats` returning `nan`. A NaN score honestly means "not comparable", and the response layer renders it as `null`.

### 3.3 Recommended (implemented): reject non-finite tolerances at the request

**Status: DONE in PR #157** (`83e461a`). **File:** `backend/biosim_server/api/main.py`, the `rel_tol`, `abs_tol_min` and `abs_tol_scale` `Query(...)` declarations on `POST /verify/omex` (≈ line 350) and `POST /verify/runs` (≈ line 830). All six declarations are changed. Regenerating the OpenAPI spec produces no diff, because `allow_inf_nan` has no JSON Schema equivalent.

```python
rel_tol: float = Query(default=0.0001, allow_inf_nan=False, description=...)
```

**CONFIRMED** against `fastapi 0.115.14`: `nan` and `inf` return **422**, and finite values are unchanged. With §3.1 in place a NaN tolerance no longer 500s. But it still starts a workflow whose every score is NaN, and burns a rate-limit unit and N simulator jobs on a run that cannot produce a result. Rejecting it up front is cheaper and clearer.

### 3.4 Optional: make the schema say `null`

The OpenAPI spec still documents `ComparisonStatistics.score` and `Hdf5DataValues.values` as arrays of `number`, while responses can now contain `null`. Do the annotation only, with **no validators**:

```python
score: Optional[list[Optional[float]]] = None      # biosim_verify/models.py
values: list[Optional[float]]                      # biosim_runs/models.py
```

Then regenerate the committed spec with `uv run python -m scripts.generate_openapi` (`backend/scripts/generate_openapi.py`; `tests/api/test_openapi_endpoints.py` checks it). The frontend does not generate types from the spec, so no frontend change is required.

Why validators were rejected (**CONFIRMED** by local reproduction):
- **They do run on the GET path.** The API decodes the query result through `pydantic_data_converter`, which re-validates: `[0.0, nan]` became `[0.0, None]`.
- **They don't run on attribute assignment.** `activities.py:138` assigns `score` after construction, and `validate_assignment` is off.
- **They don't run on a model a handler builds and returns directly.** That still 500s.
- **They cover only the fields they are attached to,** which misses §2.3's other carriers.

They add a shared-model behavior change (`Hdf5DataValues` is used by `biosim_service`) for protection §3.1 already provides.

### 3.5 Tests

**Done in PR #157** (`backend/tests/common/test_json_response.py`):
- Run-212-shaped report (NaN/Inf in `score`, NaN in `sim_run_data` values) → `GET /verify/{id}` **200**, `null`s in place, ACAO present.
- Finite content renders byte-for-byte as Starlette does.
- Non-JSON types still raise `TypeError`.
- Circular references still raise `ValueError`; shared, non-cyclic references are kept (uncommitted, see §3.1).
- Verified to **fail without the fix** with the production `ValueError … nan`.

**Done in PR #157** (`backend/tests/api/test_verification_completion.py::test_non_finite_tolerance_rejects_before_any_work`):
- Each of `nan`, `inf` and `-inf`, in each of the three tolerances, on both POST endpoints (18 cases) → 422 with `loc == ["query", <param>]`, no upload, no OMEX insert, no ledger call, no workflow start.
- Verified to fail (18/18) without the `allow_inf_nan=False` change.

**To add:**
- `backend/tests/biosim_verify/test_generate_statistics.py`: `calc_stats` with an all-NaN row → that variable's score is `nan` and `is_close` is `False`. This documents the behavior that §3.2 deliberately keeps.

---

## 4. Fix 2 — CORS Headers on Unhandled Errors (Defense in Depth)

The goal is right; the mechanism matters. **A catch-all `add_exception_handler(Exception)` does not work.** Starlette routes the `Exception` key to `ServerErrorMiddleware` (`applications.py:84-94`), which is registered **outside** `CORSMiddleware`, so its 500 still lacks CORS headers.

Measured against the repo's own `starlette 0.46.2` / `fastapi 0.115.14` (**re-confirmed 2026-10-26**):

| Approach | 500 status | CORS header |
|---|---|---|
| No handler (original) | 500 | **absent** |
| `add_exception_handler(Exception, ...)` | 500 | **absent** |
| `add_exception_handler(ValueError, ...)` | 500 | **present** |
| Catch-all middleware registered *inside* CORS | 500 | **present** |

### 4.1 Recommended (implemented): catch-all middleware inside CORS

**Status: DONE in PR #157.** **File:** `backend/biosim_server/common/server_errors.py` (`ServerErrorCorsMiddleware`), registered in `api/main.py`:

```python
app.add_middleware(MultipartBodyLimitMiddleware)
app.add_middleware(ServerErrorCorsMiddleware)   # inside CORS, outside the multipart limit
app.add_middleware(CORSMiddleware, ...)          # outermost user middleware: decorates the 500
```

Two corrections to the original sketch:

- **Re-raise after sending the 500.** Don't swallow the exception, and don't log it yourself. Mirror `ServerErrorMiddleware` (`errors.py:155-187`): track `response_started`, send `{"detail": "Internal Server Error"}` only if nothing has been sent, then `raise`. The exception then still reaches `ServerErrorMiddleware` and uvicorn, which log the full traceback once. Test clients with `raise_server_exceptions=True` still see it: the full backend suite passed unchanged (1525 tests at `2ca6e77`, before the tolerance cases and cycle tests added below). And the 500 already went out through CORS, so `ServerErrorMiddleware` doesn't send a second response.
- **Place it outside `MultipartBodyLimitMiddleware`,** not innermost. That middleware can itself re-raise (`upload_limit.py`, its `except _BodyLimitExceeded: … raise` path), and an innermost catch-all would miss that.

`HTTPException` never reaches this middleware: `ExceptionMiddleware`, further in, handles it. So there is nothing to special-case.

### 4.2 Tests

**Done in PR #157** (`backend/tests/common/test_server_errors.py`):
- An unhandled `RuntimeError` in `GET /verify/{id}` → 500 **with** ACAO, generic body, no exception text leaked.
- The same exception still propagates to the server (`pytest.raises` through `ASGITransport`).
- An exception after `http.response.start` → no second response is sent.

**Optional to add:**
- Disallowed origin → 500 **without** ACAO.
- A preflight `OPTIONS` still returns 200.
- Existing 401/404 CORS behavior unchanged. This is already covered indirectly by the 413 CORS test in `test_upload_limit.py` and the suite passing.

---

## 5. Fix 3 — Frontend: Stop Polling, Show the Error

**File:** `frontend/app/components/verification/VerificationStatus.vue`

Current behavior (confirmed):

- `status` falls back to `'PENDING'` when `workflowData` is null (line 45), so a run that never loads displays `PENDING` forever.
- The catch block (lines 120-131) halts only on a non-transient **4xx with a known status**. Everything else is treated as transient and backs off from 5 s, doubling to a 60 s cap (`MAX_POLL_INTERVAL_SECONDS = 60`, line 24). It **never halts**. That includes 5xx and **failures with no status** (`err.response?.status ?? err.statusCode` is `undefined`).

Changes:

1. **Bound the retries, counting every failure that doesn't already halt.** Add a consecutive-failure counter, incremented in the backoff branch, so it counts both **5xx** and **`status === undefined`**. After N consecutive failures, set `halted = true`, stop timers and show the error. Reset the counter on any success. Counting only 5xx would **not** fix this incident:
   - The browser saw no status at all: a CORS-blocked response reaches `$fetch` as a network error.
   - ingress-nginx's own 502/503/504 pages carry no CORS headers, so they arrive status-less **even after Fix 2**.

   Keep the existing backoff for the first N failures, so a transient blip still self-heals.
2. **Account for `$fetch`'s built-in retry.** `$fetch` (ofetch) retries GETs once by default on 5xx and network errors. The logs show **2 GETs per poll** (same `_t`, about 200 ms apart). The component already has its own backoff, so pass `retry: 0` to this `$fetch`, or account for the doubled request count when choosing N.
3. **Distinguish "no data yet" from "failed to load".** When `halted` is true and `workflowData` is null, the badge must not read `PENDING`. Show an error/unknown state instead. The `UAlert` already renders `errorMsg`; the badge needs to follow.
4. **Keep `manualRefresh` as the retry escape hatch.** It already resets `halted` and restarts the timer (lines 166-172). Also reset the new counter there and in the `workflowId` watcher (lines 184-195). The label already reads `Retry` when halted (line 269).

**On the reported "retries every 50 s":** the steady-state interval is 60 s, not 50 s. **CONFIRMED** in production logs: polls for run 212 at exactly 60.0 s intervals from 20:36:31 to 20:40:32 UTC on 2026-10-23. The countdown starts when each answer arrives, so the true period is 60 s plus request latency. Several shorter intervals before 20:36 come from overlapping pollers: dialog re-opens, and the browser agent's own probing.

### Tests

- Component test: 5 consecutive 500s → polling stops, error shown, badge is not `PENDING`.
- Component test: 5 consecutive **status-less failures** (network/CORS error) → same.
- Component test: a 500 followed by a 200 → polling continues, data renders, counter resets.
- Component test: 404 (non-transient 4xx) still halts immediately, as today.
- If `retry: 0` is adopted: one failed poll issues exactly one request.

---

## 6. Fix 4 — Optional: Diagnosable 500 Bodies

Before PR #157, the 500 body was Starlette's plain-text `Internal Server Error` with no detail. It is now JSON, `{"detail": "Internal Server Error"}`, but still carries nothing to correlate. Even a working `curl` would not have revealed the cause: the traceback existed only in pod and Cloud Logging logs. That is why this took cluster access to diagnose.

If implemented, `ServerErrorCorsMiddleware` (§4.1) is the natural place:

- Accept or generate a request ID (`X-Request-ID` inbound, else a UUID).
- Include it in the JSON 500 body and in a log line emitted before the re-raise.
- Return `{"detail": "Internal Server Error", "request_id": "..."}`.

**No request-ID mechanism exists in the backend today** (grepped: no `X-Request-ID`, no request-id middleware). This is net-new plumbing, which is why it is ranked optional. If added, the frontend error path should surface the ID so a user can quote it in a report.

**Do not** return the exception message or traceback to clients; that leaks internals. The ID is the whole point: it correlates a client report with a log line.

---

## 7. Sequencing and Risk

| Step | Change | Risk | Status |
|---|---|---|---|
| 1 | Response-boundary `null` rendering (§3.1) | Low | **Done** (PR #157); commit the cycle-safe walk |
| 2 | Catch-all middleware inside CORS, re-raising (§4.1) | Medium (middleware ordering) | **Done** (PR #157); full suite green |
| 3 | `allow_inf_nan=False` on tolerance params (§3.3) | Low | **Done** (PR #157, `83e461a`) |
| 4 | `score`/`values` annotated `list[float \| None]` + regenerated spec (§3.4) | Low | Optional |
| 5 | Frontend retry bound, counting status-less failures (§5) | Low | To do; separate frontend PR |
| 6 | Request-ID plumbing (§6) | Low | Optional; net-new |

Steps 1–2 fix the reported bug and its class: a non-finite float can no longer fail a response, and any other unhandled error is now an honest, CORS-visible 500. Step 3 closes the user-controlled NaN input. Steps 4–6 are quality-of-life. Steps 1 and 2 ship together in PR #157, so the original "do not ship the CORS fix alone" concern no longer applies.

### Verification gates

- Backend: `uv run python -m pytest` (PR #157 plus the uncommitted cycle-safe walk: 1547 passed, 17 skipped — 1525 at `2ca6e77`, plus 18 tolerance cases from `83e461a` and 4 cycle tests still uncommitted), `uv run ruff check`, `uv run mypy biosim_server tests`.
- New tests from §3.5, §4.2 and §5 pass.
- Manual, after deploy: `curl -i -H "Origin: https://biosim.biosimulations.org" https://api.biosim.biosimulations.org/verify/<id>` against a live run with a NaN score → **200** with `Access-Control-Allow-Origin` and `null` scores. Run 212 can no longer be used: its history has aged out and it returns 404. Pick a fresh COPASI comparison or reproduce locally.
- Cloud Logging, after deploy: no new `Out of range float values are not JSON compliant` lines from the `api` container.
- CI: `backend-ci` (pytest, ruff, mypy), `frontend-ci` (lint, typecheck) and `smoke` green.

### Deploy note

Backend changes require the release → tag → deploy sequence (`backend/CLAUDE.md`, root `CLAUDE.md` → Release automation / Deploy). The next backend release (`backend-v0.10.3`) will also carry the unreleased verify-workflow change from #126 (`17e213a`). It doesn't change the Temporal command sequence, so **no worker drain** is needed (reviewed 2026-10-23). The frontend fix can ship independently. **Do not merge the deploy PR before the release workflow's image push is confirmed.** `api` runs `strategy: Recreate` at one replica, so an unpublished tag takes the API down rather than leaving old pods serving.

---

## 8. Open Questions

1. ~~**Sentinel vs `null` for `score`**~~ **Resolved: `null`,** rendered at the response boundary (§3.1). No sentinel (§3.2).
2. ~~**`Hdf5DataValues` validator scope**~~ **Resolved: no validators** (§3.4). The annotation-only change is optional.
3. **Retry bound value** (§5). 5 consecutive failures is a suggestion, not a measurement. Pick it based on how long a legitimate cold start takes, and remember each poll is 2 requests unless `retry: 0` is set.
4. **Request ID** (§6). Confirm whether the ingress or any upstream already sets `X-Request-ID` before adding generation logic.
5. **New: are COPASI 4.45.296's outputs correct?** In run 212 COPASI returned 2 time points where PySCeS returned 1001, plus an all-NaN variable. That suggests it isn't honoring the SED-ML time course, or is failing partially. This doesn't affect the 500 fix, but it means these comparisons can never succeed. Worth checking against the SED-ML and, if confirmed, reporting upstream.
6. **New: Temporal retention.** Results age out after about a day (**INFERRED** from run 212's 500 → 404 transition). Confirm the namespace retention setting and whether one day is intended for verification results that users revisit.

---

## 9. Evidence Index

| Claim | Source |
|---|---|
| `ValueError: Out of range float values are not JSON compliant: nan` ×75 in the 48 h pod window, all run 212 | `kubectl logs -n biosim-gke deploy/api --since=48h` (pod started 2026-10-23 20:10 UTC) |
| 127 API 500s in 10 days, all this `ValueError`, across six verifications | Cloud Logging: `resource.labels.container_name="api"`, `textPayload:"500 Internal Server Error"` / `"Out of range float values"`, `--freshness=10d` |
| COPASI 4.45.296 compared with its own run scored `nan` (runs 212 and 82) | Cloud Logging, `worker` container, `generate_statistics_activity` "Comparing … score:" lines |
| COPASI shape `(n, 2)` vs PySCeS `(n, 1001)` in run 212 | same, "Shapes of … do not match" lines |
| 0 × 503 on `/verify` (app and ingress) | API pod logs; `ingress-nginx` controller logs for run 212 |
| Run 212: 500 → 404 at 2026-10-23 20:41:32 UTC (~24 h after completion) | API access log, `_t` cadence analysis |
| Ledger write precedes serialization; `GET /verify` is its only writer | `backend/biosim_server/api/main.py`, `get_verify_output` step 5; `grep record_terminal_status` |
| `allow_nan=False` in response rendering | `starlette/responses.py:184-190` |
| Middleware order / `ServerErrorMiddleware` outside CORS | `starlette/applications.py:84-94` |
| `ServerErrorMiddleware` sends once then always re-raises | `starlette/middleware/errors.py:155-187` |
| CORS decorates only `http.response.start` | `starlette/middleware/cors.py:147-153` |
| Four-way handler comparison (table §4) | local reproduction, repo venv (`starlette 0.46.2`, `fastapi 0.115.14`), re-run 2026-10-26 |
| NaN in `response_model` → 500, no ACAO; `model_dump(mode="json")` keeps `nan`, `model_dump_json` emits `null` | local reproduction with real models |
| `calc_stats` unguarded `np.nanmax`; `np.max` on tolerances | `backend/biosim_server/biosim_verify/activities.py:176-189` |
| `hdf5_compare.compare_arrays` guards with `1e10` but is never called | `hdf5_compare.py:42-43`; `grep -rn compare_arrays biosim_server` |
| `rel_tol=nan` accepted as NaN; `allow_inf_nan=False` → 422 for `nan`/`inf` | local reproduction, `fastapi 0.115.14` |
| `HDF5Attribute.value` admits floats | `backend/biosim_server/biosim_runs/models.py:8,11-13` |
| Score validator runs on Temporal decode, not on assignment or on a returned instance | local reproduction with `pydantic_data_converter` and FastAPI `response_model` |
| `formatScore(1e10)` → "1000000000000.0%" | `frontend/app/components/verification/VerificationTable.vue:67-69`, evaluated in Node |
| CSV export writes the raw score, so a `1e10` sentinel would export as `10000000000` | `VerificationTable.vue:79` (`r.score !== null ? r.score : ''`) |
| Frontend already handles `null` scores | `VerificationTable.vue:67-69,79`; `useVerificationAnalytics.ts:298` |
| Frontend `PENDING` fallback; status-less and 5xx failures never halt | `frontend/app/components/verification/VerificationStatus.vue:45,120-131` |
| Poll cap 60 s, observed exactly 60.0 s; 2 GETs per poll (ofetch retry) | same, line 24; API access log for run 212, 2026-10-23 20:36–20:41 UTC |
| No request-ID mechanism exists | grep of `backend/biosim_server/**` for `request_id`/`X-Request-ID` |
| Spec regeneration command and freshness test | `backend/scripts/generate_openapi.py`; `backend/tests/api/test_openapi_endpoints.py` |
| Fixes 1–2 implemented and tested | PR #157, commit `2ca6e77`: `common/json_response.py`, `common/server_errors.py`, `tests/common/test_json_response.py`, `tests/common/test_server_errors.py` |
| §3.3 implemented and tested; spec unchanged | PR #157, commit `83e461a`: `api/main.py`, `tests/api/test_verification_completion.py`; `scripts.generate_openapi` → no diff |
| Deployed image `backend-0.10.2` | `kubectl get deploy api -n biosim-gke -o jsonpath`; `GET /version` |

**Not verified / out of scope:**
- **Which carrier failed for runs 253, 211, 92 and 83.** Their histories have aged out, and the worker's multi-line log entries split the score array from the line carrying the workflow ID. All four failed with the same `ValueError`.
- **Whether `HDF5Attribute` floats ever carry NaN in practice.**
- **The original browser sweep** (34×200 / 66×404 / 0×500 across 100 listed runs) reflected only live histories at that moment. It did not show the six earlier failures, because those had already aged out to 404.

---

## 10. TODO

Checked against the code on 2026-10-26: branch `fix/CORS-Error` at `83e461a`, plus uncommitted local changes. PR #157 is **open, not merged**. Production still runs `backend-0.10.2` (`GET /version` → `"0.10.2"`), so **none of these fixes is live yet**.

### Status of the four fixes

| Fix | Section | Status | Evidence |
|---|---|---|---|
| 1. Non-finite floats render as `null` | §3 | **Implemented** (only the optional §3.4 is not done) | `common/json_response.py` is the app's `default_response_class` (`api/main.py:220`). All 6 tolerance `Query` declarations have `allow_inf_nan=False` (§3.3). Tests pass: 8 in `tests/common/test_json_response.py`, 18 tolerance cases. The cycle-safe walk and its 4 tests are **uncommitted**. |
| 2. CORS headers on unhandled 500s | §4 | **Implemented** | `common/server_errors.py`, registered at `api/main.py:229`: inside `CORSMiddleware`, outside `MultipartBodyLimitMiddleware`. All 3 tests in `tests/common/test_server_errors.py` pass. |
| 3. Frontend: stop polling, show the error | §5 | **Not implemented** | `VerificationStatus.vue` is unchanged. 5xx and status-less failures still back off forever (lines 124-131). There is no failure counter and no `retry: 0`. The badge still falls back to `PENDING` (line 45). |
| 4. Request ID in 500 bodies (optional) | §6 | **Not implemented** | No `request_id` or `X-Request-ID` anywhere in `backend/biosim_server` or `frontend/app`. |

Re-run on 2026-10-26: `pytest tests/common/test_json_response.py tests/common/test_server_errors.py tests/api/test_verification_completion.py::test_non_finite_tolerance_rejects_before_any_work` → 29 passed.

### Ship Fixes 1–2 (PR #157)

- [ ] Commit and push the cycle-safe walk in `common/json_response.py` and its 4 tests in `tests/common/test_json_response.py` (§3.1).
- [ ] Merge PR #157. CI was green and `CLEAN` at `83e461a`; re-check after the commit above.
- [ ] Release PR: `bash backend/scripts/bump-backend.sh patch` (→ `0.10.3`), version files only. Merge it.
- [ ] Tag the merge commit on `main` as `backend-v0.10.3` and push the tag.
- [ ] Confirm the `release.yaml` run succeeded: `gh run list --workflow release.yaml --limit 1`. **Do not open the deploy PR until it has**, because `api` is `Recreate` at one replica.
- [ ] Check the rendered config: `kubectl kustomize kustomize/overlays/biosim-gke | grep -E 'AUTH0_DOMAIN|AUTH0_AUDIENCE|AUTH_REQUIRED'`.
- [ ] Deploy PR: set `newTag: backend-0.10.3` for api and worker in `kustomize/overlays/biosim-gke/kustomization.yaml`.
  - No worker drain is needed (§7 Deploy note).
  - Don't merge during an Auth0 outage.
- [ ] After deploy (§7 Verification gates):
  - `/version` returns `0.10.3`.
  - A fresh NaN run returns **200**, with ACAO and `null`s.
  - Cloud Logging shows no new `Out of range float values` lines.

### Remaining work

- [ ] **Fix 3, frontend (§5)**, as a separate frontend PR:
  - A consecutive-failure counter that counts 5xx **and** status-less errors; halt after N.
  - `retry: 0` on the poll `$fetch`.
  - A badge that does not read `PENDING` when the dialog has halted with no data.
  - Reset the counter in `manualRefresh` and in the `workflowId` watcher.
  - The component tests listed in §5.
- [ ] **§3.5 `calc_stats` test.** Not present: the only `calc_stats` tests (`tests/biosim_verify/test_hdf5_compare.py`) use finite data. Add a case to `tests/biosim_verify/test_generate_statistics.py`: an all-NaN row → that variable's score is `nan` and `is_close` is `False`.

### Optional

- [ ] §3.4: annotate `ComparisonStatistics.score` (`biosim_verify/models.py:23`) and `Hdf5DataValues.values` (`biosim_runs/models.py:55`) with `Optional[float]` elements. Then regenerate the spec (`uv run python -m scripts.generate_openapi`).
- [ ] §4.2 extra tests:
  - a disallowed origin → 500 **without** ACAO
  - a preflight `OPTIONS` → 200
- [ ] Fix 4 (§6), request-ID plumbing. First check whether the ingress already sets `X-Request-ID` (§8 Q4).

### Open questions to close (§8)

- [ ] Q3: choose the retry bound N for Fix 3. With ofetch's retry still on, each poll is 2 requests.
- [ ] Q5: COPASI 4.45.296 returns 2 time points plus an all-NaN variable. Check that against the SED-ML, and report it upstream if confirmed.
- [ ] Q6: confirm the Temporal namespace retention (≈ 1 day, inferred) is intended for verification results.
