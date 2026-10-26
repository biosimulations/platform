# Verification Results Reported as CORS Errors (HTTP 500 on `GET /verify`): Investigation and Fixes

| | |
|---|---|
| **Incident window** | 2026-10-22 20:05 to 2026-10-23 20:40 UTC (first and last logged 500) |
| **Affected endpoint** | `GET /verify/{workflow_id}` on `biosim-gke`, image `backend-0.10.2` |
| **Symptom** | The verification dialog on `/utilities/verify-model` stayed on `PENDING`, and the browser console reported CORS errors |
| **Root cause** | A `NaN` in a stored verification report made response rendering raise. The unhandled 500 left the app without CORS headers, so the browser reported a CORS error instead. |
| **Fixed in** | [PR #157](https://github.com/biosimulations/platform/pull/157) (Fixes 1 and 2), to be released as `backend-v0.10.3` |
| **Status (2026-10-26)** | PR #157 open; production still on `backend-0.10.2` |
| **Follow-up work** | §9 |

**Evidence labels used in this document:**
- **CONFIRMED:** reproduced, or read from the code or from production logs and data.
- **INFERRED:** follows from inspected behavior, but not reproduced end to end.
- **UNVERIFIED:** needs runtime or external confirmation.

## Contents

1. [Summary](#1-summary)
2. [Impact and timeline](#2-impact-and-timeline)
3. [Root cause](#3-root-cause)
4. [Where the NaN and Inf values come from](#4-where-the-nan-and-inf-values-come-from)
5. [Fixes](#5-fixes)
6. [Changes and tests in PR #157](#6-changes-and-tests-in-pr-157)
7. [Deployment](#7-deployment)
8. [Known limitations and technical debt](#8-known-limitations-and-technical-debt)
9. [Follow-up work](#9-follow-up-work)
10. [Open questions](#10-open-questions)
11. [Runbook: the browser reports a CORS error from the API](#11-runbook-the-browser-reports-a-cors-error-from-the-api)
12. [Evidence and references](#12-evidence-and-references)

---

## 1. Summary

### 1.1 What happened

Opening certain completed verification runs on the verification page failed. The browser reported a CORS error, and the run's dialog showed `PENDING` and kept polling, even though the verification list showed the same run as `COMPLETED`.

The CORS error was a symptom. Behind each one was an HTTP 500 from `GET /verify/{workflow_id}`. The CORS allowlist was correct throughout.

### 1.2 Root cause in brief

1. **A simulator returned NaN.** In run `compose-api-prod-comparison-212-…`, COPASI 4.45.296 returned only the first output step of the time course. Seven of its 18 report variables contained NaN, and one was NaN at every time point (§4.1).
2. **The NaN entered the report silently.** The simulation-data service sends NaN as the JSON string `"nan"`. Pydantic quietly converts that string to a float NaN (§4.2). The comparison step, `calc_stats`, then scored the all-NaN variable as `nan` (§4.3). Temporal stored the report as is.
3. **Rendering failed, which caused the 500.** `GET /verify` read the report successfully. Starlette's `JSONResponse` then encodes with `json.dumps(allow_nan=False)`, which raised `ValueError: Out of range float values are not JSON compliant: nan`. Nothing handled the exception.
4. **The 500 had no CORS headers, which caused the CORS error.** Starlette turns unhandled exceptions into a 500 in `ServerErrorMiddleware`, which sits *outside* `CORSMiddleware`. The 500 therefore had no `Access-Control-Allow-Origin` header. The browser blocked it, and the frontend never saw the status.
5. **The list and the dialog disagreed.**
   - `GET /verify` records a run's final status in the verification ledger *before* it renders the response, so the list showed `COMPLETED`.
   - The dialog never stops polling on a 5xx or on a failure with no status, so it showed `PENDING` indefinitely.

Six verifications produced 127 such 500s. Each one stopped failing on its own about a day after it completed: once its Temporal history expired, the endpoint returned a normal 404 instead (§3.5).

### 1.3 What PR #157 changes

- **Fix 1:**
  - `NaN`, `+Infinity` and `-Infinity` anywhere in an API response now render as JSON `null` instead of failing the response.
  - Non-finite comparison tolerances (`nan`, `inf`, `-inf`) are rejected with 422 before any work starts.
- **Fix 2:** an unhandled exception now becomes a JSON 500 *inside* `CORSMiddleware`, so the browser sees the real status.
- **Origins:** the two production origins added earlier on this branch are removed again, because CORS configuration was not the cause.

Merging PR #157 does not deploy anything. The fixes reach production with the `backend-v0.10.3` release and a deploy PR (§7).

### 1.4 Decisions on the remaining work

| Item | Decision | Priority |
|---|---|---|
| Non-finite values in `calc_stats` (§4.4, §9 item 1) | **Fix.** Non-finite input can produce false matches, not only NaN scores. | High |
| **Fix 3:** frontend stops polling and shows the error (§5.4) | **Make it.** It is needed to fully resolve the symptom users saw. | High |
| **Fix 4:** request ID on 500 responses (§5.5) | **Optional.** It is not needed to resolve this incident. | Low |

- **Why Fix 3 matters:** Fixes 1 and 2 remove this particular 500 and make future 500s readable. They don't change the dialog, which still retries forever and shows `PENDING` on any 5xx, and on any failure that has no status. ingress-nginx's own 502, 503 and 504 error pages carry no CORS headers, so an outage of the ingress or the backend would recreate the "PENDING forever" symptom even with Fix 2 deployed.
- **Why Fix 4 is optional:**
  - **For:** it would have shortened this investigation, because a user could quote the 500's ID and find its traceback directly.
  - **Against:** with one `api` replica and current traffic, a run ID plus a time already finds the traceback.
  - **Revisit** it if `api` scales beyond one replica, or if 500 reports become frequent.

The PR #157 review raised the handling of non-finite values as **technical debt**: NaN and Inf should be treated as signs of a bad result, not passed along as values. The investigation supports that view (§4.4, §8).

---

## 2. Impact and timeline

### 2.1 Affected verifications

Cloud Logging over a 10-day lookback from 2026-10-23 held 127 API 500s, and all of them were this `ValueError`. No other route returned a 500 in that window.

| Verification (`compose-api-prod-comparison-…`) | 500s | Archive | What is known |
|---|---|---|---|
| `212-69b4e144-b3b3-4e94-94cc-3127e88ff460` | 85 | BIOMD0000000155 | COPASI self-comparison scored `nan` (worker log). Upstream data examined (§4.1). |
| `82-…` | 10 | BIOMD0000000024 | COPASI self-comparison scored `nan` (worker log). Upstream data examined (§4.1). |
| `253-…` | 8 | — | History expired before the investigation. The NaN-carrying field was not identified. |
| `211-…` | 8 | — | Same as 253. |
| `92-…` | 8 | — | Same as 253. |
| `83-…` | 8 | — | Same as 253. |

All six came from the compose-api comparison runs of 2026-10-22.

- **No data was lost.** The reports were stored intact; only rendering them failed.
- **No 503s on `/verify`.** The browser-extension investigation that first reported the problem also mentioned 503s, but none appear in the API or ingress-nginx logs for `/verify` in this window.

### 2.2 What users saw

- **Browser console:** a CORS error on `GET https://api.biosim.biosimulations.org/verify/{id}`. The browser hid the 500 status.
- **Verification dialog:** the badge read `PENDING`.
  - Polling backed off from 5 s to a 60 s cap and never stopped.
  - Each poll sent two requests, because of ofetch's default retry.
  - Production logs show run 212 polled at exactly 60.0 s intervals from 20:36:31 to 20:40:32 UTC. The period is 60 s plus request latency, because the countdown restarts when each answer arrives.
- **Verification list:** the same run showed `COMPLETED`.

### 2.3 Timeline (UTC)

| Time | Event |
|---|---|
| 2026-10-22 20:05 | First `ValueError … nan` 500 in Cloud Logging |
| 2026-10-23 20:34 | Problem reported on PR #157 for run 212 |
| 2026-10-23 20:36–20:40 | Run 212 polled every 60 s; every poll returned a 500 |
| 2026-10-23 20:40 | Last logged 500 |
| 2026-10-23 20:41:32 | Run 212 starts returning `404 … no longer retained`, about 24 h after completion. Its Temporal history had expired. |
| 2026-10-23 | Root cause identified from API pod logs, Cloud Logging and worker logs |
| 2026-10-26 | Fixes 1 and 2 complete in PR #157. Upstream data and comparison behavior analyzed (§4). |

---

## 3. Root cause

### 3.1 The failing request

```
GET /verify/{workflow_id}
  → get_verify_output()                       # api/main.py — succeeds
      → handle.query("get_output")            # returns VerifyWorkflowOutput (contains nan)
      → _authorize_verification_read(...)     # succeeds
      → _reconcile_terminal_status(...)       # succeeds
      → _record_terminal_status(...)          # ledger write: status = COMPLETED
      → return workflow_output
  → FastAPI serializes the response_model     # model_dump(mode="json") keeps nan as a float
  → JSONResponse.render()                     # json.dumps(..., allow_nan=False)
  → ValueError: Out of range float values are not JSON compliant: nan
  → unwinds past CORSMiddleware (no response started yet)
  → ServerErrorMiddleware sends a plain-text 500 without Access-Control-Allow-Origin
```

### 3.2 Why rendering failed

FastAPI 0.115.14 turns the `response_model` into plain data with `model_dump(mode="json")`, which keeps NaN as a Python float. It then hands that data to Starlette's `JSONResponse.render`, which calls `json.dumps(..., allow_nan=False)` and raises on NaN or ±Inf (**CONFIRMED** by local reproduction with the real `VerifyWorkflowOutput` model).

Two other layers accepted the NaN:
- **Temporal:** its JSON payload converter accepts NaN, so neither the worker nor Temporal rejected the report.
- **Pydantic:** its own `model_dump_json()` would have emitted `null`, because `ser_json_inf_nan` defaults to `'null'`. FastAPI's response path doesn't use it.

### 3.3 Why the browser reported a CORS error

Starlette builds the middleware stack with `ServerErrorMiddleware` outermost. User middleware sits inside it, with the last-added outermost, and `ExceptionMiddleware` is innermost. Before PR #157 the stack was:

```
ServerErrorMiddleware              (outermost; renders unhandled exceptions as a 500)
  → CORSMiddleware                 (adds Access-Control-* headers to http.response.start)
      → MultipartBodyLimitMiddleware
          → ExceptionMiddleware    (innermost; renders HTTPException)
              → router → handler
```

`CORSMiddleware` adds its headers only to the `http.response.start` message. Here the exception was raised while the response was still being built, so that message never passed through CORS. `ServerErrorMiddleware` then wrote its own 500, outside CORS and without the header. The browser blocked a cross-origin response that had no `Access-Control-Allow-Origin` and reported a CORS failure. To the frontend, the request therefore had **no status at all**.

Handled errors behave differently. An `HTTPException` is rendered by `ExceptionMiddleware`, inside CORS, so the response passes back out through CORS and gets the header. That is why 401, 404 and 413 responses carry `Access-Control-Allow-Origin`. Run 212's later 404 carried it as well, which also shows the allowlist was correct.

### 3.4 Why the list and the dialog disagreed

- **The list.** `get_verify_output` writes the terminal status to the `BiosimCompare` ledger, and it is the only writer of that status. The write happens before the response is rendered, so the ledger recorded `COMPLETED` even though every detail request then failed. `GET /verification_ids` reads the ledger.
- **The dialog.** `VerificationStatus.vue` shows `PENDING` whenever it has no data. Its polling stops only on a lasting 4xx that carries a status. Every other failure, including 5xx and status-less failures, is treated as temporary and retried forever (§5.4).

The ledger write is correct behavior. It just meant the failure couldn't be seen from the list.

### 3.5 Why the failures disappeared on their own

Run 212 went from 500 to the ledger's `404 "results are no longer retained"` at 2026-10-23 20:41:32 UTC, about 24 hours after it completed. **INFERRED:** the Temporal namespace keeps workflow histories for about one day.

Once a history expires, `GET /verify` returns the 404 before it ever reads a report. A later sweep of 100 listed runs therefore found no live 500s (34 returned 200, 66 returned 404), even though six runs had failed.

---

## 4. Where the NaN and Inf values come from

### 4.1 The simulator output

**CONFIRMED** on 2026-10-26 from the public simulation-data API, `simdata.api.biosimulations.org`.

For run 212 (BIOMD0000000155, dataset `BIOMD0000000155_url.sedml/autogen_report_for_task1`):

| | COPASI 4.45.296 (run `6ada6fdc041d31202095031d`) | PySCeS 1.2.2 (run `6ada6fdc60280023643783c3`) |
|---|---|---|
| Shape (variables × time points) | `[18, 2]` | `[18, 1001]` |
| First variable (simulation time) | `0.0, 0.01` | `0.0, 0.01, …, 10.0` |
| Non-finite entries | 8 of 36, all sent as the string `"nan"`, in variables 1, 2, 9, 14, 15, 16 and 17 | none |
| Variables NaN at every point | variable 16: `["nan", "nan"]` | none |

**COPASI returned only the first output step** of a time course that PySCeS ran to t = 10 in 1001 points. At that step, seven variables were NaN. Variable 16 was NaN even at t = 0, which suggests a quantity that couldn't be evaluated from the initial state (**INFERRED**). The cross-simulator comparisons failed on the shape mismatch and were reported with an `error_message`. Only the self-comparisons were computed, and COPASI's scored `nan` for variable 16:

```
Comparing copasi:4.45.296:run=6ada6fdc041d31202095031d and copasi:4.45.296:run=6ada6fdc041d31202095031d
  for dataset BIOMD0000000155_url.sedml/autogen_report_for_task1
  score: [ 0.  0. …  0. nan  0.] is_close: [ True … False  True]
```

Run 82 (BIOMD0000000024, COPASI run `6ada6ebc6028002364377a2a`) shows the same pattern:
- shape `[9, 2]`
- time stops at `0.01`
- 7 entries are `"nan"`
- variable 6 is `["nan", "nan"]`

**Conclusion:** the NaN comes from a failed or truncated simulation, not from rounding in the comparison. Why COPASI 4.45.296 stops after the first step on these models is an open question (§10). The cause lies upstream of this repository, so PR #157 doesn't address it.

### 4.2 How the string `"nan"` becomes a float NaN

JSON has no NaN literal, so the simulation-data service sends non-finite values as strings. `BiosimServiceRest.get_hdf5_data` (`biosim_runs/biosim_service.py`) builds `Hdf5DataValues(shape=..., values=...)`, and `values` is declared as `list[float]`. Pydantic converts the strings without any warning:

```python
Hdf5DataValues(shape=[1, 3], values=[0.0, "nan", "inf"]).values   # → [0.0, nan, inf]
```

This conversion is where an upstream serialization choice becomes a non-finite float inside the pipeline. It is also why `calc_stats` received a NaN row rather than failing on a parse error. **CONFIRMED** locally.

### 4.3 How the comparison turns non-finite input into non-finite scores

`calc_stats` (`biosim_verify/activities.py`) is the comparison that `generate_statistics_activity` runs. For each variable it computes:

```
atol     = max(abs_tol_min, nanmax(arr1) · abs_tol_scale, nanmax(arr2) · abs_tol_scale)   # np.max over the three
score    = nanmax( |arr1 − arr2| / (atol + rel_tol · |arr2|) )                              # over time points
is_close = score < 1
```

`np.nanmax` returns NaN for a row that is entirely NaN. NaN in a row's maximum then passes through `np.max` into `atol`, and from there into every ratio for that variable. The caller stores `score.tolist()` in `ComparisonStatistics.score`, an `Optional[list[float]]`, which accepts NaN and Inf.

The following outcomes come from running the repository's `calc_stats` with the default tolerances (`rel_tol=1e-4`, `abs_tol_min=1e-3`, `abs_tol_scale=1e-5`) unless noted, against the reference `[1, 2, 3]` (**CONFIRMED**, 2026-10-26):

| Input | `score` | `is_close` | Problem |
|---|---|---|---|
| Identical finite rows | `0.0` | `True` | — |
| **All-NaN row** (run 212) | `nan` | `False` | Non-finite score: **this caused the 500** |
| `-Inf` at one point | `inf` | `False` | Non-finite score |
| `abs_tol_min=0`, both rows all zero | `nan` | `False` | Non-finite score, and identical data is reported as a mismatch |
| `abs_tol_min=0`, row maxima ≤ 0, reference 0 | `inf` | `False` | Non-finite score |
| `rel_tol` negative enough to cancel `atol` exactly | `inf` | `False` | Non-finite score |
| NaN at one point, rest equal | `0.0` | `True` | **False match:** the NaN point is ignored (§4.4) |
| `+Inf` at one point, rest off by 100× (`[100, inf, -50]`) | `0.0` | `True` | **False match** (§4.4) |
| Negative tolerances, data off by 100× | `-49.5` | `True` | **False match** (§4.4) |
| `+Inf` at the same point on both sides | `0.0` | `True` | — |

### 4.4 Related defect: non-finite input and invalid tolerances produce false matches

The `calc_stats` docstring says it computes the same result as `np.allclose(arr1, arr2, ..., equal_nan=False)`. That function treats any NaN as "not close". The vectorized code doesn't do that:

- **A NaN at some time points is skipped.** `np.nanmax` ignores those points, so a variable can be reported as a match on the remaining points alone.
- **A single `+Inf` makes a variable pass.** It makes `nanmax(arr)` infinite, so `atol` becomes infinite. Every finite difference then divides to 0, and the `Inf` point itself gives `inf/inf = NaN`, which `nanmax` skips. The score is 0 whatever the other values are.
- **Negative tolerances make everything pass.** A negative denominator gives a negative score, and every negative score is `< 1`.

This is a correctness defect in the verification results themselves. It existed before PR #157, which neither causes nor fixes it. How often it has affected stored results is unknown: reports are kept only as long as their Temporal history (about one day), so past results can't be re-checked. It is follow-up item 1 in §9.

### 4.5 Client-supplied tolerances

`rel_tol`, `abs_tol_min` and `abs_tol_scale` are query parameters on `POST /verify/omex` and `POST /verify/runs`, and they are stored in the report's `compare_settings`.

- **Before PR #157:** `?rel_tol=nan` was accepted. The request started a workflow whose every score was NaN, and it made every `GET` for that verification (and the POST's own response) return a 500.
- **Fixed in PR #157:** `nan`, `inf` and `-inf` are rejected with 422.
- **Still accepted:** zero and negative values (`abs_tol_min=0` → 200, `abs_tol_min=-1` → 200). Zero and negative tolerances can still produce non-finite scores or false matches (§4.3, §4.4). Bounding them is a change to the API contract, so it is left for follow-up item 5 in §9.

### 4.6 Every field that can carry a non-finite value

| Field in `VerifyWorkflowOutput` | Source | Observed |
|---|---|---|
| `workflow_results.comparison_statistics[…].score` | `calc_stats` (§4.3) | **Yes**: runs 212 and 82 |
| `workflow_results.sim_run_data[].data.values` | Raw simulator output (§4.1, §4.2), included when `include_outputs=true` | Possible whenever a simulator emits NaN |
| `compare_settings.rel_tol` / `abs_tol_min` / `abs_tol_scale` | The client's query string (§4.5) | Possible before PR #157; now rejected |
| `workflow_results.sims_run_info[].hdf5_file…attributes[].value` | Upstream HDF5 metadata (`ATTRIBUTE_VALUE_TYPE` includes `float` and `list[float]`) | Not observed (**INFERRED** possible) |

Because NaN can appear in any of these fields, guarding them one at a time can never be complete, and each new float field would reopen the bug. That is why Fix 1 acts at the response boundary, which every response passes through.

### 4.7 `hdf5_compare.compare_arrays` is not on this path

`hdf5_compare.compare_arrays` handles NaN by returning `(False, 1e10)`. It is called by `compare_datasets` in the same module and by `tests/biosim_verify/test_hdf5_compare.py`. The verification workflow doesn't use either function: `generate_statistics_activity` calls `calc_stats`, which mentions `compare_arrays` only in its docstring. Stored reports therefore never carry the `1e10` value.

---

## 5. Fixes

### 5.1 Overview

| Fix | Change | Status (2026-10-26) | Where |
|---|---|---|---|
| **1** | Non-finite floats render as `null`; non-finite tolerances are rejected with 422 | **Implemented** in PR #157 | `common/json_response.py`, `api/main.py` |
| **2** | Unhandled exceptions become a JSON 500 inside CORS | **Implemented** in PR #157 | `common/server_errors.py`, `api/main.py` |
| **3** | Frontend stops polling after repeated failures and shows the error | **Planned**: recommended | `frontend/app/components/verification/VerificationStatus.vue` |
| **4** | Request ID on 500 responses | **Planned**: optional | `common/server_errors.py` |

### 5.2 Fix 1: non-finite floats never fail a response (implemented)

#### Response rendering

`NonFiniteSafeJSONResponse` (`backend/biosim_server/common/json_response.py`) subclasses Starlette's `JSONResponse` and is the app's `default_response_class`:

```python
def render(self, content: Any) -> bytes:
    try:
        return super().render(content)                     # unchanged fast path
    except ValueError:
        return super().render(_finite_or_none(content))    # nan / ±inf → None, then re-render
```

- **It covers every route and every field** in §4.6, including routes in included routers and POST responses.
- **It covers results already stored** in Temporal, with no data migration.
- **Ordinary responses cost nothing extra.** The content is walked only after the strict encoder has rejected it.
- **The output matches Pydantic's own:** `model_dump_json` also emits `null` for these values.
- **The frontend already handles `null`:**
  - `formatScore` in `VerificationTable.vue` shows `—` for `null` and NaN.
  - The CSV export writes an empty cell.
  - `useVerificationAnalytics.ts` uses `?? null`.
  - A `null` in chart data is drawn as a gap.
- **Other failures still raise.**
  - A non-JSON type still raises `TypeError`.
  - Circular content still raises `ValueError("Circular reference detected")`. The walk tracks the containers on the current path, so a cycle can't recurse until `RecursionError`.
  - A value shared by two siblings, which is not a cycle, is walked twice and kept, just as `json.dumps` does.

#### Rejecting non-finite tolerances

`allow_inf_nan=False` is set on all six tolerance `Query` declarations: `rel_tol`, `abs_tol_min` and `abs_tol_scale`, on both POST endpoints. With it:
- `nan`, `inf` and `-inf` return **422** naming the parameter, before any upload, ledger write or workflow start.
- Finite values are unaffected.
- The OpenAPI spec is unchanged, because `allow_inf_nan` has no JSON Schema equivalent.

Without this check, a non-finite tolerance would no longer cause a 500 once rendering is fixed, but it would still spend a rate-limit unit and one simulator job per simulator on a run that can't produce a result.

#### Trade-offs

`null` collapses NaN, `+Inf` and `-Inf` into one value, and the client can't tell them apart. Fix 1 is a safety net, so that no response can fail on a non-finite float. It is not a statement that NaN is an acceptable result (§8).

### 5.3 Fix 2: CORS headers on unhandled errors (implemented)

`ServerErrorCorsMiddleware` (`backend/biosim_server/common/server_errors.py`) is registered so that it sits inside `CORSMiddleware` and outside `MultipartBodyLimitMiddleware`:

```python
app.add_middleware(MultipartBodyLimitMiddleware)
app.add_middleware(ServerErrorCorsMiddleware)   # inside CORS, outside the multipart limit
app.add_middleware(CORSMiddleware, ...)          # outermost user middleware: adds headers to the 500
```

It renders an unhandled exception as `{"detail": "Internal Server Error"}` with status 500, then **re-raises it**, the way `ServerErrorMiddleware` does:
- **Logging is unchanged.** uvicorn still logs the full traceback once.
- **Tests still see the error.** Test clients that raise application exceptions still raise.
- **Nothing is sent twice.** The middleware tracks whether `http.response.start` was sent, and if headers are already out it re-raises without sending a second response.
- **No exception text reaches the client.**

**Why it sits outside `MultipartBodyLimitMiddleware`:** that middleware can itself re-raise an exception, and a catch-all placed inside it would miss that. `HTTPException` never reaches the new middleware, because `ExceptionMiddleware` handles it further in.

**Why this approach** (measured with `starlette 0.46.2` / `fastapi 0.115.14`):

| Approach | Status | `Access-Control-Allow-Origin` |
|---|---|---|
| No handler (before PR #157) | 500 | absent |
| `add_exception_handler(Exception, …)` | 500 | **absent**: Starlette installs this handler on `ServerErrorMiddleware`, outside CORS |
| `add_exception_handler(ValueError, …)` | 500 | present, but only for `ValueError` |
| **Middleware registered inside CORS** (chosen) | 500 | **present**, for every unhandled exception |

**Limitation:** errors generated by ingress-nginx itself (502, 503, 504) are produced outside the application and still carry no CORS headers. Fix 3 handles them on the client side.

### 5.4 Fix 3: frontend stops polling and shows the error (planned, recommended)

**Current behavior of `VerificationStatus.vue`** (**CONFIRMED**):
- The badge shows `status`, which falls back to `'PENDING'` when no data has loaded.
- The `fetchStatus` catch block stops polling only on a 4xx that has a status and is not in the retryable set (408, 409, 425, 429). Everything else backs off from 5 s, doubling to the 60 s cap, and **never stops**. That includes every 5xx and every failure with no status, where `err.response?.status ?? err.statusCode` is `undefined`.
- `$fetch` (ofetch) retries a GET once by default after a 5xx or a network error, so each poll sends two requests.

**Why counting only 5xx would not be enough:**
- A CORS-blocked response reaches `$fetch` as a network error with no status.
- ingress-nginx error pages also arrive with no status, even after Fix 2.

The fix must therefore count status-less failures too.

**Design decisions**

- **Move the decision logic into a pure module.** The frontend's tests run with `node --test` against plain `.ts` utilities (for example `tests/verification-listing.test.mjs` imports `app/utils/verification-listing.ts`), and `frontend-ci` runs them through `npm run test:verification`. There is no component-test setup. So the halt-or-back-off decision and the error text move to `app/utils/verification-polling.ts`, where these tests can reach them, and the component keeps only the wiring. Adding vitest and `@vue/test-utils` is a separate decision this fix doesn't need.
- **Stop after N = 5 consecutive failures.** With the existing doubling from 5 s, failures land at about t = 0, 10, 30, 70 and 130 s. A dialog therefore stops about 2 min 10 s after the API stops answering. **UNVERIFIED:** that should outlast an `api` restart under its `Recreate` strategy; time a real rollout before settling on N.
- **Pass `retry: 0` on the poll,** so that each poll sends one request and N counts requests.
- **Add a display-only `UNAVAILABLE` badge.**
  - `status` stays a `VerifyWorkflowStatus`, because `isDone` depends on it.
  - A new `displayStatus` returns `'UNAVAILABLE'` when polling has stopped and no data ever loaded.
  - The existing `default:` branches of `statusBadgeColor` and `statusIcon` already map an unknown value to `neutral` / `i-lucide-help-circle`.
  - This also fixes expired runs, whose 404 stops polling today while the badge still reads `PENDING`.

**Step 1: `frontend/app/utils/verification-polling.ts` (new)**

```ts
// Consecutive failed polls after which polling stops and the error is shown
export const MAX_CONSECUTIVE_POLL_FAILURES = 5
// The 4xx answers that can change on retry; ofetch treats the same ones as transient (moved from the component)
export const TRANSIENT_CLIENT_ERRORS = new Set([408, 409, 425, 429])

// status is undefined for a network or CORS failure; failures includes this one
export function pollFailureAction(status: number | undefined, failures: number): 'halt' | 'backoff' {
  if (status !== undefined && status >= 400 && status < 500 && !TRANSIENT_CLIENT_ERRORS.has(status)) return 'halt'
  return failures >= MAX_CONSECUTIVE_POLL_FAILURES ? 'halt' : 'backoff'
}

export function pollErrorMessage(err: any): string {
  const status: number | undefined = err?.response?.status ?? err?.statusCode
  // No status: no readable answer at all (network failure, CORS block, or an ingress error page)
  const text = status === undefined
    ? 'Could not reach the verification service.'
    : err?.data?.detail || err?.message || 'Failed to poll verification workflow'
  // Fix 4: the ID a user can quote; absent until the backend change is deployed
  const requestId = err?.data?.request_id
  return requestId ? `${text} (request ID: ${requestId})` : text
}
```

**Step 2: `frontend/app/components/verification/VerificationStatus.vue`**

- **Imports:** import `pollFailureAction` and `pollErrorMessage`, and delete the component's own `TRANSIENT_CLIENT_ERRORS`.
- **Counter:** add `let consecutiveFailures = 0` next to `inFlight`.
- **Fetch options:** pass `retry: 0` to the `$fetch` in `fetchStatus`.
- **On success:** after the aborted/`workflowId` guard, set `consecutiveFailures = 0`.
- **On failure:** replace the body of the catch block after its guard with:
  ```ts
  consecutiveFailures++
  errorMsg.value = pollErrorMessage(err)
  const status: number | undefined = err?.response?.status ?? err?.statusCode
  if (pollFailureAction(status, consecutiveFailures) === 'halt') {
    halted.value = true
    stopTimers()
  } else {
    // Transient, server or network failure: back off rather than hammer a struggling backend
    pollInterval.value = Math.min(pollInterval.value * 2, MAX_POLL_INTERVAL_SECONDS)
  }
  ```
- **Resets:** set `consecutiveFailures = 0` next to `halted.value = false` in `manualRefresh`, so `Retry` gets a fresh five attempts, and in the `workflowId` watcher.
- **Badge:** add the display status, then use `displayStatus` for `statusBadgeColor`, `statusIcon` and the badge text:
  ```ts
  // A dialog that gave up before any answer must not claim the run is PENDING
  const displayStatus = computed(() => halted.value && !workflowData.value ? 'UNAVAILABLE' : status.value)
  ```
- **Comment:** update the comment on `halted`, which is now also set after N consecutive failures.

**Step 3: tests in `frontend/tests/verification-polling.test.mjs` (new), added to the `test:verification` script**

- 401, 403 and 404 → `halt` on the first failure, unchanged.
- 408, 409, 425 and 429 → `backoff` below N, `halt` at N.
- 500, 502, 503 and 504 → `backoff` for failures 1–4, `halt` at 5.
- An `undefined` status behaves like a 5xx. This is the incident's case.
- `pollErrorMessage`:
  - shows a FastAPI `detail`
  - returns the fixed text for a status-less error, instead of ofetch's `[GET] "…": <no response> Failed to fetch`
  - appends a `request_id` when the body has one

Then run `npm run lint`, `npm run typecheck` and `npm run test:verification`.

**Step 4: manual checks on the local stack**

Start the infrastructure with `docker compose up -d`, then run the backend and `npm run dev`.

- **5xx path.**
  - Run `docker compose stop temporal`. `GET /verify/{id}` then returns a 503 with CORS headers (`Temporal service unavailable`).
  - Expect one request per poll, a stop after five polls, an `UNAVAILABLE` badge (or the last known status if data had loaded), the 503's detail in the alert, and a `Retry` button.
- **Status-less path.** Stop the API process. Expect the same, with "Could not reach the verification service."
- **Recovery.**
  - Restart the stopped service and click `Retry`. Data should load and polling should resume.
  - Stop the service again. It should take another five failures to stop, which shows the counter was reset.
- **Unchanged behavior.** An unknown ID still stops after one 404.

**Release.** Follow `frontend/CLAUDE.md` → Deploy:
1. Release PR with `frontend/scripts/bump-frontend.sh patch`.
2. Tag `frontend-v0.3.9` on the merge commit.
3. Confirm the `release.yaml` run succeeded.
4. Deploy PR bumping the frontend `newTag` in the `biosim-gke` overlay.

`frontend` runs as a RollingUpdate at two replicas, and Fix 3 doesn't depend on PR #157.

### 5.5 Fix 4: request ID on 500 responses (planned, optional)

The 500 body is now `{"detail": "Internal Server Error"}`. It is correctly free of internals, but it gives a user nothing to quote, and nothing like it exists in the backend today: there is no `X-Request-ID` handling and no request-ID middleware.

**Design decisions**

- **Only 500s get an ID,** created in `ServerErrorCorsMiddleware`. A 500 is the only response where a user needs something to quote. This needs no new middleware and no change to middleware order. Putting `X-Request-ID` on every response would need a separate outermost middleware, and can come later.
- **Reuse the ingress's ID when it sends one.**
  - By default (`generate-request-id: "true"`), ingress-nginx forwards `X-Request-ID` to the upstream: the client's own value if it sent one, otherwise a generated 32-hex ID. Its default `log-format-upstream` ends with the same `$req_id`.
  - Reusing that value makes the 500's ID match the ingress access-log line.
  - **UNVERIFIED for `biosim-gke`:** check the controller's ConfigMap (step 3). Without an inbound ID, the middleware generates one, and that still identifies the traceback.
- **Validate before reuse.** A client can choose that header's value, because the ingress passes it through. Accept only `[A-Za-z0-9._-]{1,128}` and otherwise generate `uuid4().hex`. That keeps newlines and markup out of logs and responses.
- **Attach the ID to the existing traceback with `exc.add_note`, rather than writing a second log line.**
  - uvicorn writes the traceback through its `uvicorn.error` logger. That logger doesn't propagate to the root `JsonFormatter` (`"uvicorn": {"propagate": false}`), and `JsonFormatter` drops `exc_info` anyway.
  - A note is printed at the end of a logged traceback. **CONFIRMED:** `logging.Formatter.formatException` prints `request_id=…` after the exception line.
  - `add_note` needs Python 3.11 or later, and the project requires `>=3.13`.
- **Still no exception text in the body.**

**Step 1: `backend/biosim_server/common/server_errors.py`**

```python
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _request_id(scope: Scope) -> str:
    """The ingress's X-Request-ID when well-formed, so the 500 matches its access log; else a new one."""
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    return uuid.uuid4().hex


# in ServerErrorCorsMiddleware.__call__
        except Exception as exc:
            if not response_started:
                request_id = _request_id(scope)
                # Printed at the end of the server's traceback, which is how a quoted ID finds it
                exc.add_note(f"request_id={request_id}")
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Internal Server Error", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )
                await response(scope, receive, send)
            raise
```

Mention the ID in the module docstring and in the CORS paragraph of `backend/CLAUDE.md`.

**Step 2: tests in `backend/tests/common/test_server_errors.py`** (these reuse the `failing_get_verify` fixture)

- The 500 body is exactly `{"detail": "Internal Server Error", "request_id": <id>}`. The `x-request-id` header equals that ID, and `Access-Control-Allow-Origin` is present.
- A well-formed inbound `X-Request-ID` is echoed back.
- A malformed inbound ID (containing a space or newline, or 129 characters long) is replaced with a 32-character hex ID.
- The re-raised exception carries the note: `f"request_id={id}" in exc.__notes__`.
- The existing test that no exception text leaks still passes.

Then regenerate the spec with `uv run python -m scripts.generate_openapi`; no diff is expected. Run `ruff`, `mypy` and `pytest`.

**Step 3: check the ingress configuration** (read-only; needs cluster access)

```bash
kubectl get cm -n ingress-nginx ingress-nginx-controller -o jsonpath='{.data}'
```

The IDs match the ingress access log when `generate-request-id` is absent or `"true"`, and `log-format-upstream` is absent or contains `$req_id`.

**Step 4: after deploy.** There is no safe way to cause an unhandled 500 in production on purpose, so the tests are the gate. The next real 500's traceback should be findable with the Cloud Logging query `resource.labels.container_name="api" textPayload:"request_id="`.

### 5.6 Alternatives considered and rejected

- **A finite stand-in value in `calc_stats`**, such as `np.where(np.isfinite(score), score, 1e10)`:
  - **It replaces one flag with another.** The PR #157 review objects to exactly that.
  - **It would stop `null` from ever being used.** It runs before the report is stored, so consumers would see `1e10` instead of `null`.
  - **It would show up in the UI.** The Score column would show "1000000000000.0%" (`formatScore(1e10)`), and the CSV export would contain `10000000000`.
  - **It adds nothing.** `is_close` is already `False` for NaN.
  - **The precedent it relied on is off the verification path:** `compare_arrays` (§4.7).
- **Pydantic validators on `score` and `values`:**
  - **They only cover the fields they're attached to.** That misses the other fields in §4.6.
  - **They don't always run.** They do run when the API decodes a Temporal query result (`pydantic_data_converter` re-validates), but not on attribute assignment (`activities.py` assigns `score` after construction, and `validate_assignment` is off), and not on a model that a handler builds and returns directly.
  - **They change shared behavior.** `Hdf5DataValues` is also used by `biosim_service`.
- **`@app.exception_handler(Exception)`:** Starlette installs it on `ServerErrorMiddleware`, outside CORS, so its 500 still has no CORS headers (§5.3).
- **`@app.exception_handler(ValueError)`:** it does get CORS headers, but it covers only one exception type.
- **Hard-coding the production origins in `APP_ORIGINS`** (this branch's first commit): CORS configuration was not the cause. `biosim-gke` already allows the frontend through `CORS_EXTRA_ORIGINS` in `kustomize/config/biosim-gke/api.env`, and requests from the API host to itself are same-origin. Reverted in PR #157.

---

## 6. Changes and tests in PR #157

### 6.1 Files

| Path | Change |
|---|---|
| `backend/biosim_server/common/json_response.py` | New: `NonFiniteSafeJSONResponse` (Fix 1) |
| `backend/biosim_server/common/server_errors.py` | New: `ServerErrorCorsMiddleware` (Fix 2) |
| `backend/biosim_server/api/main.py` | Sets `default_response_class`, registers the middleware inside CORS, and adds `allow_inf_nan=False` to the six tolerance parameters. `APP_ORIGINS` is unchanged from `main`. |
| `backend/tests/common/test_json_response.py` | New: 8 tests |
| `backend/tests/common/test_server_errors.py` | New: 3 tests |
| `backend/tests/api/test_verification_completion.py` | `test_non_finite_tolerance_rejects_before_any_work` (18 cases) |
| `backend/CLAUDE.md` | CORS section: why error responses must stay inside `CORSMiddleware`, and that NaN/±Inf render as `null` |
| `backend/docs/verify-500-cors-fix-implementation-plan.md` | This document |
| `.gitignore` | Ignores the machine-local `.perch/` directory |

The OpenAPI artifact, the frontend, kustomize and `version.py` are unchanged, so merging deploys nothing.

### 6.2 Commits

| Commit | Content |
|---|---|
| `5e81f4d` | Ignores `.perch/`, and adds two production origins (removed again in `2ca6e77`) |
| `2ca6e77` | Fixes 1 and 2, their tests, the `backend/CLAUDE.md` note, and the origin revert |
| `83e461a` | Rejects non-finite tolerances with 422, plus tests |
| `76f670f` | Cycle-safe walk in `json_response.py`, plus 4 tests |
| later commits | This document |

### 6.3 Tests

| Test | Shows |
|---|---|
| `test_get_verify_with_nan_in_report_is_200` | A report shaped like run 212's, with NaN and Inf in `score` and NaN in `sim_run_data` values, returns **200** from `GET /verify/{id}`, with `null`s and `Access-Control-Allow-Origin` |
| `test_finite_content_renders_exactly_like_starlette` | Ordinary content renders byte-for-byte as before |
| `test_non_finite_floats_render_as_null_at_any_depth` | NaN and ±Inf become `null` in nested dicts, lists and tuples |
| `test_other_render_errors_still_raise` | Non-JSON types still raise `TypeError` |
| `test_circular_content_raises_value_error_not_recursion_error`, `test_cycle_through_a_list_raises_value_error`, `test_cycle_with_non_finite_values_still_raises_cleanly` | Cycles raise `ValueError("Circular reference detected")` |
| `test_shared_references_are_not_mistaken_for_cycles` | A list shared by two keys is kept |
| `test_unhandled_500_carries_cors_headers` | An unhandled error in `GET /verify/{id}` returns a 500 with `Access-Control-Allow-Origin` and a generic body |
| `test_unhandled_exception_is_still_raised_to_the_server` | The exception still propagates to the server |
| `test_no_second_response_once_headers_are_sent` | No second response once headers are out |
| `test_non_finite_tolerance_rejects_before_any_work` (18 cases) | `nan`, `inf` and `-inf` in each tolerance, on both POST endpoints: 422 naming the parameter, with no upload, no ledger call and no workflow start |

**Verified to fail without the fix:**
- The NaN-report test fails with the same `ValueError … nan` as production.
- The 500 test fails on the missing CORS header.
- All 18 tolerance cases fail.

**Suite results:**
- `backend-ci` on `0a05995`: 1547 passed, 17 skipped.
- Locally on `73c68e2`, with `pytest -m "not integration"`: 1541 passed, 15 skipped, 8 deselected.
- `frontend-ci`, `smoke`, GitGuardian and Snyk also passed.

---

## 7. Deployment

### 7.1 Release and deploy

Follow the two-PR flow in `backend/CLAUDE.md` → Deploy:

1. **Merge PR #157.**
2. **Release PR:** `bash backend/scripts/bump-backend.sh patch` (`0.10.2` → `0.10.3`), version files only. Merge it.
3. **Tag:** tag the merge commit on `main` as `backend-v0.10.3` and push the tag.
4. **Confirm the build:** check that the `release.yaml` run succeeded (`gh run list --workflow release.yaml --limit 1`).
5. **Check the configuration:** `kubectl kustomize kustomize/overlays/biosim-gke | grep -E 'AUTH0_DOMAIN|AUTH0_AUDIENCE|AUTH_REQUIRED'`.
6. **Deploy PR:** set `newTag: backend-0.10.3` for `api` and `worker` in `kustomize/overlays/biosim-gke/kustomization.yaml`. Flux applies it on merge.

Notes:
- **Wait for step 4 before step 6.** `api` runs with `strategy: Recreate` at one replica, so pointing it at an unpublished image tag takes the API down.
- **No worker drain is needed.** `0.10.3` also carries the verify-workflow change from #126 (`17e213a`), which is on `main` but not in `backend-v0.10.2`. It doesn't change the Temporal command sequence (reviewed 2026-10-23).
- **Don't merge the deploy PR during an Auth0 outage.** A restarted pod starts with an empty JWKS cache.

### 7.2 Post-deploy verification

- `curl -s https://api.biosim.biosimulations.org/version` returns `"0.10.3"`.
- A verification with a NaN score returns **200**, with `Access-Control-Allow-Origin` and `null` scores:
  ```bash
  curl -si -H "Origin: https://biosim.biosimulations.org" https://api.biosim.biosimulations.org/verify/<id> | head -20
  ```
  Run 212 can no longer be used, because its history has expired. Use a fresh COPASI comparison, or reproduce locally.
- Cloud Logging shows no new `Out of range float values are not JSON compliant` lines from the `api` container.

---

## 8. Known limitations and technical debt

The PR #157 review accepted the fix as an immediate remedy and recorded the handling of non-finite values as **technical debt**. The review's position was that NaN and Inf should have been treated as the sign of a bad result, not passed along as a value or a flag. Otherwise such problems surface much later, or are silently ignored. It also suggested custom serialization to carry NaN, +Inf and −Inf faithfully.

The investigation bears that out:

- **NaN is used as a flag.**
  - A NaN score currently stands for "not comparable", and NaN in raw data stands for "simulator could not compute this". Neither is an explicit error.
  - The comparison already reports missing datasets, label mismatches and shape mismatches through `ComparisonStatistics.error_message`. Non-finite input has no equivalent.
- **Non-finite input is silently ignored in some cases** and produces false matches (§4.4).
- **Bad input is silently converted.** The string `"nan"` from upstream becomes a float NaN with no warning (§4.2).
- **`null` loses information.** Fix 1 renders NaN, +Inf and −Inf all as `null`. For scores this stops mattering once non-finite input is reported as an error. For raw simulator data, where a non-finite value can be meaningful, it may.
- **The safety net is silent.** Nothing logs when `NonFiniteSafeJSONResponse` replaces a value, so a new source of non-finite values would go unnoticed.
- **The OpenAPI schema is inaccurate.** `ComparisonStatistics.score` and `Hdf5DataValues.values` are still documented as arrays of `number`, while responses can contain `null`.
- **Some errors are still invisible in the browser.** ingress-nginx 502/503/504 responses carry no CORS headers (§5.3), so until Fix 3 the dialog can't tell them apart from a slow API.
- **Results are short-lived.** Temporal keeps histories for about one day (**INFERRED**). That makes failures hide themselves, and it means past results can't be audited for the §4.4 defect.

---

## 9. Follow-up work

Status as of 2026-10-26; nothing in this list is part of PR #157.

| # | Item | Priority | Notes |
|---|---|---|---|
| 1 | **Report non-finite input in `calc_stats` as an error, not a score** | High | Any non-finite value in either output for a variable makes that variable not a match, matching the documented `np.allclose(..., equal_nan=False)` behavior, and records the reason (for example "copasi:4.45.296 output contains NaN at 2 of 2 time points"). **Agree on how to represent this with the reviewers before building it.** An optional new model field can still decode stored results. The change lives in an activity, not workflow code, so no worker drain is needed. Add tests for every row of the §4.3 table. |
| 2 | **Fix 3**: frontend stops polling (§5.4) | High | Separate frontend PR → `frontend-v0.3.9` |
| 3 | **Log when the `null` fallback fires** | Medium | One warning with the number of replaced values, and no payload or IDs (consistent with `JsonFormatter`'s allowlist). After item 1, any such warning points to a new source to trace. |
| 4 | **Investigate COPASI 4.45.296's truncated output** (§4.1) | Medium | Compare run `6ada6fdc041d31202095031d`'s output and logs with the SED-ML for BIOMD0000000155. If it's a simulator problem, report it to the BioSimulators COPASI maintainers. |
| 5 | **Bound the tolerance parameters** (§4.5) | Medium | For example `abs_tol_min > 0`, `rel_tol ≥ 0`, `abs_tol_scale ≥ 0`. This changes the API contract (requests that were accepted would now get a 422), so check existing callers first. |
| 6 | **Validate upstream values on ingestion** (§4.2) | Medium | Make the `"nan"` → NaN conversion explicit, by detecting and recording non-finite values when `Hdf5DataValues` is built, rather than relying on Pydantic's silent coercion. Pair this with item 1. |
| 7 | **Decide how non-finite raw data is serialized** (§8) | Deferred | `null` versus a lossless encoding (for example the strings `"NaN"`, `"Infinity"`, `"-Infinity"`, or a separate mask). Decide with the API's consumers once someone needs to tell the three apart. |
| 8 | **Document `null` in the schema** | Low | Annotate `score: Optional[list[Optional[float]]]` and `values: list[Optional[float]]` (no validators), then run `uv run python -m scripts.generate_openapi`. The frontend doesn't generate types from the spec. |
| 9 | **Fix 4**: request ID on 500s (§5.5) | Low | Optional |
| 10 | **More tests** | Low | A `calc_stats` all-NaN case in `tests/biosim_verify/test_generate_statistics.py` (the existing `calc_stats` tests use only finite data). A 500 for a disallowed origin returns no CORS header. A preflight `OPTIONS` still returns 200. |
| 11 | **Confirm Temporal retention** | Low | Confirm the namespace retention period, and whether about one day is intended for results that users come back to |

---

## 10. Open questions

1. **Why does COPASI 4.45.296 stop after the first output step** on BIOMD0000000155 and BIOMD0000000024? And is the string `"nan"` the intended way for the simulation-data service to encode non-finite values?
2. **How should "not comparable" be represented** in `ComparisonStatistics`: a per-variable reason, a status enum, or a nullable score plus a reason? (§9 item 1)
3. **What should the Fix 3 retry limit be?** N = 5 with `retry: 0` stops polling after about 2 min 10 s; check that against a timed `api` rollout.
4. **Does `biosim-gke`'s ingress forward `X-Request-ID`**, and does it log `$req_id`? (§5.5 step 3; only affects Fix 4)
5. **Is about one day of Temporal retention intended** for verification results?
6. **How often has the §4.4 false-match defect affected results?** Stored results expire, so this can only be measured from now on, for example by logging non-finite inputs once item 1 or item 6 is in place.

---

## 11. Runbook: the browser reports a CORS error from the API

A CORS error on a request that normally works usually hides a server error, not a CORS misconfiguration. To find the real cause:

1. **Get the real status.** Send the request outside the browser, with an `Origin` header:
   ```bash
   curl -si -H "Origin: https://biosim.biosimulations.org" "https://api.biosim.biosimulations.org/verify/<id>" | head -20
   ```
   Read the result as follows:
   - **5xx without `access-control-allow-origin`:** an unhandled error from before Fix 2, or an error page from the ingress.
   - **5xx with the header:** an application error.
   - **A normal response with the header:** the problem is on the client side.
2. **Find the traceback in the API logs** (read-only; needs cluster access):
   ```bash
   kubectl logs -n biosim-gke deploy/api --since=2h | grep -A40 "Exception in ASGI application"
   ```
   For history beyond the pod's lifetime, use Cloud Logging:
   ```
   resource.labels.namespace_name="biosim-gke"
   resource.labels.container_name="api"
   textPayload:"Exception in ASGI application"
   ```
   If Fix 4 is deployed, search for `request_id=<id>` instead.
3. **Check the ingress** if the API logs show nothing: `kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --since=2h | grep "/verify/<id>"`.
4. **For verifications, check the comparison** in the worker logs: Cloud Logging, `container_name="worker"`, `textPayload:"Comparing"`. Each pair's `score:` and `is_close:` arrays are logged there.
5. **Check whether the result has expired.** `404 "results are no longer retained"` means the Temporal history has expired. The verification list can still show the ledger's final status.
6. **To inspect a simulator's raw output,** use the public data API: `https://simdata.api.biosimulations.org/datasets/<simulation run id>/data?dataset_name=<sedml>/<report>`. Non-finite values appear as strings.

---

## 12. Evidence and references

### 12.1 Evidence

| Claim | Source |
|---|---|
| 75 `ValueError … nan` errors in the API pod's logs, all for run 212; no 503s on `/verify` | `kubectl logs -n biosim-gke deploy/api` (pod started 2026-10-23 20:10 UTC); `ingress-nginx` controller logs |
| 127 API 500s across six verifications, all this `ValueError` | Cloud Logging, `container_name="api"`, `textPayload:"Out of range float values"`, 10-day lookback |
| COPASI 4.45.296's self-comparison scored `nan` (runs 212 and 82); shape mismatch with PySCeS | Cloud Logging, `container_name="worker"`, `generate_statistics_activity` "Comparing … score:" and "Shapes of … do not match" lines |
| Run 212: 500 → 404 at 2026-10-23 20:41:32 UTC; polling every 60.0 s; 2 GETs per poll | API access log for run 212 |
| COPASI output for run 212: `[18, 2]`, time `0.0, 0.01`, 8 string `"nan"` entries, variable 16 all NaN; PySCeS `[18, 1001]`, time to `10.0`, all floats | `simdata.api.biosimulations.org/datasets/6ada6fdc041d31202095031d/data` and `/datasets/6ada6fdc60280023643783c3/data`, dataset `BIOMD0000000155_url.sedml/autogen_report_for_task1`, fetched 2026-10-26 |
| COPASI output for run 82: `[9, 2]`, time `0.0, 0.01`, 7 string entries, variable 6 all NaN | `simdata.api.biosimulations.org/datasets/6ada6ebc6028002364377a2a/data`, dataset `BIOMD0000000024_url.sedml/autogen_report_for_task1`, fetched 2026-10-26 |
| Pydantic converts `"nan"` and `"inf"` to floats through `list[float]` | Local reproduction with `Hdf5DataValues` |
| `calc_stats` outcomes in §4.3, including false matches | Local reproduction against `biosim_verify/activities.py:calc_stats`, 2026-10-26 |
| Zero and negative tolerances accepted; non-finite ones rejected | Local reproduction with `fastapi 0.115.14` |
| `JSONResponse.render` uses `allow_nan=False` | `starlette/responses.py` (`JSONResponse.render`), 0.46.2 |
| Middleware order; `Exception` handler installed on `ServerErrorMiddleware` | `starlette/applications.py` (`Starlette.build_middleware_stack`) |
| `ServerErrorMiddleware` sends once, then re-raises | `starlette/middleware/errors.py` (`ServerErrorMiddleware.__call__`) |
| CORS adds headers only to `http.response.start` | `starlette/middleware/cors.py` (`CORSMiddleware.send`) |
| Four-way handler comparison (§5.3) | Local reproduction, `starlette 0.46.2`, `fastapi 0.115.14` |
| `model_dump(mode="json")` keeps NaN; `model_dump_json` emits `null` | Local reproduction with the real models |
| The ledger write happens before rendering; `GET /verify` is its only writer | `api/main.py` (`get_verify_output`, `_record_terminal_status`) |
| `compare_arrays` is not on the verification path | `biosim_verify/hdf5_compare.py`; callers are `compare_datasets` and `tests/biosim_verify/test_hdf5_compare.py` |
| The frontend handles `null` scores | `VerificationTable.vue` (`formatScore`, CSV export); `useVerificationAnalytics.ts` |
| The frontend falls back to `PENDING` and never stops on 5xx or status-less failures | `VerificationStatus.vue` (`status`, `fetchStatus`) |
| A traceback note is printed by the logging formatter | Local reproduction, `logging.Formatter.formatException` |
| uvicorn's error logger doesn't propagate to the root logger | `uvicorn.config.LOGGING_CONFIG`, `uvicorn 0.34.3` |
| Deployed image `backend-0.10.2` | `kustomize/overlays/biosim-gke/kustomization.yaml`; `GET /version` |

### 12.2 Not verified

- **Which field carried the NaN for runs 253, 211, 92 and 83.** Their histories had expired, and the worker's multi-line log entries separate the score array from the workflow ID.
- **Whether `HDF5Attribute` floats ever carry NaN in practice.**
- **Whether string `"nan"` values appear in other runs' data.** Only runs 212 and 82 were fetched.
- **Whether `biosim-gke`'s ingress forwards and logs `X-Request-ID`** (§5.5).
- **The exact Temporal retention setting** (§3.5).

### 12.3 Versions

- **Backend:** `fastapi` 0.115.14, `starlette` 0.46.2, `pydantic` 2.13.4, `uvicorn` 0.34.3. Python 3.13 in the production image.
- **Frontend:** `ofetch` 1.5.1.

### 12.4 Document history

| Date | Change |
|---|---|
| 2026-10-23 | Investigation: root cause identified from production logs |
| 2026-10-26 | Fixes 1 and 2 and the tolerance validation implemented in PR #157. Upstream data and `calc_stats` behavior analyzed. Plans for Fixes 3 and 4 written. Review feedback recorded as technical debt. |
