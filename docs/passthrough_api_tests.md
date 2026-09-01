# Passthrough API — test guide

Test coverage for the biosimulations.org passthrough proxy: the typed mirror
models in `backend/biosim_server/common/biosim_api/`, the client methods on
`BiosimServiceRest`, the routes in `backend/biosim_server/legacy_proxy/`, and the
`ProjectDetail` aggregate. Companion to `docs/api_plan.md`, which explains *why*
the models are shaped the way they are.

**Status:** 119 cases implemented and passing — 108 offline, 11 live. Section 6
lists gaps that are recommended but **not yet written**.

---

## 1. How to run

All commands from `backend/`.

| Goal | Command | Needs |
|---|---|---|
| The whole passthrough surface, offline | `uv run pytest tests/legacy_proxy tests/projects -q` | nothing |
| One endpoint | `uv run pytest tests/legacy_proxy/test_specifications.py -v` | nothing |
| Live contract check against upstream | `uv run pytest tests/legacy_proxy/test_live_upstream.py -m integration -v` | network |
| What CI runs | `uv run pytest -q -m "not integration"` | Docker (for *other* suites) |
| Types + lint | `uv run mypy biosim_server tests && uv run ruff check .` | nothing |

### Why the offline tests need no infrastructure

`TestClient(app)` is used **without** its context manager, so the FastAPI
lifespan never runs and `init_standalone()` — which connects Mongo and Temporal —
never fires. Routes are exercised with `get_biosim_service` patched to an
`AsyncMock`. Client-level tests patch `aiohttp.ClientSession` instead, via the
shared helper in `tests/legacy_proxy/upstream_stub.py`:

```python
patcher, session = stub_session(_SPEC_BODY)     # canned upstream JSON
with patcher:
    specs = await BiosimServiceRest().get_run_specifications(run_id)
session.get.assert_called_once_with(...)         # assert the URL actually requested
```

The same trick makes the live tests cheap: they register a **real**
`BiosimServiceRest` and still skip Mongo/Temporal, so each call goes
route → client → `api.biosimulations.org` with no local stack.

> **61 errors from `testcontainers`/Docker are expected** when Docker is not
> running. They come from `tests/api`, `tests/biosim_omex`, `tests/common`,
> `tests/rbac_demo`, `tests/projects/test_project_search*.py` and
> `tests/simulations/test_runs_query.py` — none of them touch the passthrough.

---

## 2. What the layers cover

Three layers, tested separately because they fail differently.

```
route          FastAPI  →  status mapping, camelCase body, anonymity, 503, shadowing
  ↓                         (patch get_biosim_service)
client         BiosimServiceRest  →  upstream URL, id quoting, query params, array handling
  ↓                         (patch aiohttp.ClientSession)
model          Pydantic mirrors  →  optionality, unions, aliases, permissiveness
                            (model_validate on raw dicts — no I/O at all)
```

A model test that passes proves nothing about the URL; a route test that passes
proves nothing about parsing. Most bugs in this feature live in the model layer,
which is why it has the most cases.

---

## 3. Implemented — offline (108 cases)

### `tests/projects/test_project_summary.py` — 15 cases

The pre-existing suite, extended for the widened envelope.

| Test | Guards |
|---|---|
| `test_project_summary_returns_typed_nested_envelope` | camelCase body incl. `citations`/`encodes`/nested simulator |
| `test_project_summary_roundtrip_preserves_upstream_wire_keys` | **every input key survives serialization under its original name** — the regression guard for fields that used to ride through as `extra` |
| `test_project_summary_without_run_object_parses` | a summary with no `run` block returns 200, not a 500 |
| `test_project_summary_tolerates_a_sparse_metadata_block` | missing metadata → `[]`; empty `run` → no synthesized `simulator` |
| `test_embedded_simulation_run_is_the_shared_run_summary_type` | **`simulationRun` validates as `SimulationRunSummary`** — this is what licenses skipping the redundant `/runs/{id}/summary` call |
| `test_project_summary_{needs_no_token,forwards_no_caller_credentials}` | anonymous; no `Authorization` reaches upstream |
| `test_project_summary_upstream_{404_is_404,4xx_is_forwarded_not_502,error_is_502,unreachable_is_502}` | status mapping; transport errors do not echo the upstream host |
| `test_project_summary_without_biosim_service_is_503` | dependency unavailable |
| `test_project_summary_route_does_not_shadow_stats` | `/projects/stats` still resolves |
| `test_rest_client_{requests_the_upstream_summary_url,quotes_the_project_id}` | URL + hostile-id quoting |

### `tests/legacy_proxy/test_run_summary.py` — 13 cases

| Test | Guards |
|---|---|
| `test_run_response_captures_simulator_id_and_version_string` | the upstream slug and version string land in `simulator_id` / `simulator_version_string` **without disturbing the resolved `simulator_version` object** |
| `test_run_response_without_simulator_fields_is_none` | in-flight runs omit them |
| `test_run_summary_route_maps_upstream_status` | parametrized 404/400/403/500/503 → 404/400/403/502/502 |
| `test_run_summary_route_transport_failure_hides_upstream_address` | no host/port in the response body |
| `test_run_summary_route_is_anonymous_and_forwards_no_credentials` | anonymity |
| `test_rest_client_{requests_the_upstream_run_summary_url,quotes_the_run_id}` | URL + quoting |

### `tests/legacy_proxy/test_files.py` — 7 cases

Bare-array parsing, empty listing, null `format`/`size`/`url`, a `./`-prefixed
location, a non-array body degrading to `[]` rather than raising, id quoting,
404 and 503 mapping.

### `tests/legacy_proxy/test_specifications.py` — 22 cases

The largest set: this model has polymorphism, recursion, and three
serialized-or-expanded unions.

| Test | Guards |
|---|---|
| `test_outputs_dispatch_to_report_and_plot` | `_type` selects the right class |
| `test_unknown_output_type_falls_back_instead_of_raising` | **a future `_type` passes through** instead of 500ing — the reason the union is `left_to_right` with a permissive member rather than a Pydantic `discriminator` |
| `test_output_without_a_type_tag_falls_back` | missing tag |
| `test_plot3d_is_recognised_with_untyped_surfaces` | `SedPlot3D` parses; `surfaces` stays untyped |
| `test_task_model_may_be_a_serialized_id` | **`tasks[].model` is a bare id in live data**, with the language on the sibling `models[]` array |
| `test_task_model_may_be_expanded` | the inline form still works |
| `test_model_language_fallback_chain` | `acronym` → `name` → `sedmlUrn`, each independently optional |
| `test_data_generators_accept_id_string_or_expanded_object` | both forms |
| `test_curve_style_{absent_stays_none,may_be_an_id_string}` | absence ≠ empty object; id form accepted |
| `test_partial_style_leaves_the_missing_half_none` | `marker is None` is load-bearing for the renderer |
| `test_style_base_is_recursive` | `SedStyle.base` self-reference resolves |
| `test_unknown_style_type_vocabulary_is_accepted` | open vocabulary, not an enum |
| `test_empty_outputs_and_datasets_and_curves` | empty arrays |
| `test_rest_client_keeps_every_document_in_the_array` | **all documents returned**, not just `[0]` |
| `test_rest_client_wraps_a_lone_object_body` | a single object is still accepted |

### `tests/legacy_proxy/test_logs.py` — 11 cases

| Test | Guards |
|---|---|
| `test_log_entry_fields_are_shared_at_every_level` | the same five `LogEntry` fields are reachable at run/document/task/output |
| `test_skip_reason_and_exception_are_none_when_absent` | `None` means "not skipped"/"did not raise"; a sibling's failure does not leak |
| `test_output_datasets_presence_separates_reports_from_plots` | the report/plot discriminator |
| `test_running_simulation_has_no_documents_yet` | a log that exists before any document ran |
| `test_empty_task_and_output_arrays_behave_like_absent_ones` | `[]` ≡ absent |
| `test_unknown_log_status_is_accepted` | execution statuses stay `str` |
| `test_raw_get_sim_run_logs_still_returns_a_dict` | **compatibility**: `GET /simulations/{id}/logs` still gets a raw dict; the typed `get_run_log` is additive |

### `tests/legacy_proxy/test_results.py` — 11 cases

| Test | Guards |
|---|---|
| `test_results_accept_nested_values_from_repeated_tasks` | **`values` is `list[Any]`** — a repeated task nests its results, and `list[float]` would reject real payloads |
| `test_rest_client_encodes_the_slash_in_the_output_id` | the `/` in a composite output id is percent-encoded as data |
| `test_results_route_accepts_a_{slash_containing,percent_encoded}_output_id` | both spellings reach the client identically |
| `test_rest_client_forwards_include_data` | `?includeData=true` is sent |
| `test_results_route_maps_upstream_404_while_unavailable` | no results yet is a 404, not a 502 |
| `test_results_tolerate_empty_and_missing_data` | empty `data`, missing `values` |

### `tests/legacy_proxy/test_ontology.py` — 13 cases

| Test | Guards |
|---|---|
| `test_both_id_spellings_normalize_consistently` | `KISAO_0000019` ↔ `KISAO:0000019` in one helper |
| `test_both_spellings_hit_the_same_upstream_url` | one cache key, one request |
| `test_repeat_lookup_is_served_from_cache` | **repeat lookups do not re-hit upstream** — the same algorithm id repeats at every level of a log |
| `test_falls_back_to_the_local_table_when_upstream_is_down` | `name` + OLS URL from the vendored `KISAO_TERMS` |
| `test_local_term_supplies_name_and_url_but_not_description` | the table has no definitions; `description` stays `None` rather than being fabricated |
| `test_unknown_upstream_and_locally_reraises` | unresolvable → 404 |
| `test_a_degraded_fallback_is_not_cached` | a fallback must not pin itself for the TTL |

### `tests/projects/test_project_detail.py` — 16 cases

These pin the aggregation **policy**, not just the happy path.

| Test | Guards |
|---|---|
| `test_detail_never_refetches_the_embedded_run_summary` | `get_run_summary` **not awaited** — the object is already in hand |
| `test_detail_never_fetches_results_or_kisao` | neither is ever called from the aggregate |
| `test_detail_does_not_fetch_the_log_by_default` | logs are conditional |
| `test_detail_fetches_the_log_only_when_requested` | `?include=log` |
| `test_detail_tolerates_a_{files,specification,log}_failure` | each secondary degrades its own field, 200 preserved |
| `test_detail_tolerates_every_secondary_failing_at_once` | all three at once still returns the summary |
| `test_detail_fails_when_the_summary_fails` | parametrized: the mandatory call's failure is the request's failure |
| `test_detail_without_a_run_id_skips_every_dependent_call` | **no run id ⇒ no dependent request** rather than a malformed upstream URL |
| `test_detail_route_does_not_shadow_stats_or_summary` | existing project routes still resolve; `/summary` still returns the envelope, not the aggregate |

---

## 4. Implemented — live upstream (11 cases)

`tests/legacy_proxy/test_live_upstream.py`, marked `integration` so CI's
`-m "not integration"` skips it. Run these whenever you touch a mirror model.

| Test | Guards |
|---|---|
| `test_project_summary_parses_live` | the real envelope validates |
| `test_run_timestamps_are_not_iso_and_must_stay_strings` | upstream sends JS `Date.toString()` (`'Sat Feb 05 2022 16:23:31 GMT+0000 (Coordinated Universal Time)'`) — **typing these as `datetime` would reject the real payload** |
| `test_run_summary_matches_the_embedded_one` | the dedup holds against live data |
| `test_files_listing_is_an_array` | shape |
| `test_specifications_is_an_array_with_serialized_model_refs` | array body; `tasks[].model` is a string id; `models[].language` is a bare URN |
| `test_log_parses_at_every_level` | real log tree |
| `test_results_accept_a_composite_output_id` | slash-containing id, both encodings |
| `test_kisao_resolves_for_both_spellings` | parametrized |
| `test_detail_aggregates_without_the_log_by_default` | aggregate against real data |
| `test_unknown_project_is_404` | error mapping end to end |

### Why these earned their place

Running them the first time broke the proxy immediately and found bugs the
offline fixtures could not, because the fixtures encoded the shape the *docs*
describe rather than the shape the API *sends*:

| Live behavior | Offline assumption | Consequence |
|---|---|---|
| `tasks[].model` is a string id | inline object | `ValidationError` → **500 on every real project** |
| `/specifications` returns an array | single object | all but the first document silently dropped |
| `submitted`/`updated` are JS date strings | (untested) | would have broken had they been typed as `datetime` |

**Takeaway:** offline tests protect the logic; only the live tests protect the
*contract*. Run section 4 before merging any change to `biosim_api/`.

---

## 5. Conventions to follow when adding tests

1. **Never assert on JSON key order.** Pydantic emits declared fields before
   `extra` ones; ordering is not part of the contract.
2. **Assert the URL, not just the result.** `session.get.assert_called_once_with(...)`
   is what catches a quoting or path-construction regression.
3. **Add a hostile-id case for every new client method** — ids are caller-supplied
   and must stay one path segment.
4. **Test absence separately from emptiness.** For styles and log fields, `None`
   and `{}` mean different things to consumers.
5. **When you add an upstream field, add a round-trip assertion.** A declared
   field must serialize under exactly the key it arrived as.
6. **Reach for `_assert_subset`** (in `test_project_summary.py`) rather than
   hand-listing keys when checking that a payload survives intact.

---

## 6. Recommended — not yet implemented

Ordered by value. None of these exist today.

### 6.1 High value

**A second live project with 2D plots and multiple SED documents.**
The current live fixture (`Yeast-cell-cycle-Irons-J-Theor-Biol-2009`) has three
`SedReport`s, one document, and no styled curves — so `SedPlot2D`, `SedCurve`,
`SedStyle`, `SedLineStyle` and `SedMarkerStyle` have **never been validated
against real data**, only against hand-written fixtures. Given that hand-written
fixtures already got `tasks[].model` wrong, this is the single biggest remaining
risk. Parametrize `test_live_upstream.py` over two or three project ids.

**A captured-payload regression corpus.**
Save real responses to `tests/fixtures/local_data/biosim_api/*.json` and validate
each model against them offline. This gives live-data fidelity at offline speed
and makes upstream drift visible as a diff rather than a surprise 500.

```python
@pytest.mark.parametrize("path", sorted(FIXTURE_DIR.glob("specifications_*.json")))
def test_captured_specifications_still_validate(path: Path) -> None:
    TypeAdapter(list[SedDocumentSpec]).validate_python(json.loads(path.read_text()))
```

**`ProjectDetail` concurrency.**
`test_detail_*` proves files and specifications are both fetched, but not that
they run *concurrently*. A test with two delayed `AsyncMock`s asserting total
elapsed time is closer to one delay than two would pin the `asyncio.gather`.

**A `_type`-round-trip test for every SED output class.**
`test_specifications_route_serializes_upstream_keys` covers `SedReport` and
`SedPlot2D`. Extend to `SedPlot3D` and `SedUnknownOutput` so the `_type` alias
cannot silently regress on the less-travelled branches.

### 6.2 Medium value

**A whole-package alias audit.** One test walking every model in `biosim_api`
and asserting each field either has no alias or has a camelCase one, so a new
model cannot ship with a snake_case wire key.

```python
def test_every_alias_is_camelcase() -> None:
    for model in iter_upstream_models():          # walk the package
        for name, field in model.model_fields.items():
            assert field.alias is None or "_" not in field.alias.lstrip("_")
```

**A config audit.** Same walk, asserting every model inherits `UpstreamModel`
and therefore carries `extra="allow"` — the `plan`'s own self-review flagged
"accidental `extra="forbid"`" as a failure mode.

**Contract tests for the shared error helper.** `upstream_errors` is currently
tested only indirectly, once per route. A direct table-driven test over
`(404, 400, 403, 418, 500, 503, ClientConnectionError)` would let the per-route
tests shrink to one smoke case each.

**Large-payload behavior for `/results`.** A synthetic multi-megabyte `values`
array, asserting the model does not blow up and recording the parse time. Plan
open question Q8 (whether results need streaming rather than buffering) cannot
be answered without a number here.

### 6.3 Lower value / opportunistic

**A frontend-parity test.** Assert the proxy's response for a project satisfies
what `frontend/app/composables/useVisualizations.ts` reads (Vega file filter,
`SedPlot2D` branch, `uriSedDataSetMap` construction). This would have caught the
`tasks[].model` bug from the consumer side.

**`SedPlot3D` in the wild.** Plan question Q4 is still open — no project is known
to use 3D plots. A search across captured specifications would settle whether
`surfaces` deserves a real model.

**Cache-expiry timing for KISAO.** The TTL is untested (only the hit path is).
Low value unless the TTL becomes configurable.

**Concurrent-request safety on the KISAO cache.** Two simultaneous lookups of a
cold key currently both hit upstream. Harmless, but worth a test if the aggregate
ever resolves algorithms in bulk.

---

## 7. Known gaps that are *not* test gaps

Recorded so nobody writes a test expecting behavior that does not exist.

- **Streaming/binary endpoints are not proxied.** `/runs/{id}/download`,
  `/results/{id}/download`, `/files/{id}/{path}/download` and the whole-run
  `/results/{id}` are out of scope. Note that
  `/results/{run_id}/{output_id:path}` **would match** `/results/{id}/download`
  and treat `"download"` as an output id — adding the streaming proxies means
  registering them *before* that route.
- **No platform ACL on the passthrough.** Every route is anonymous, matching
  `GET /projects/{id}/summary`. Private-run authorization is separate work.
- **The frontend still calls `legacy_api_url` directly.** Cutover is a separate
  PR; until then these routes have no production traffic.
- **A log output's `dataSets` is typed `Any`.** One live sample shows
  `[{status, id}, …]`, which is not enough evidence to commit to a schema.
