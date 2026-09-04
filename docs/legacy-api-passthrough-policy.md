# Passthrough proxies over the legacy API: when they're acceptable

`platform` is the migration target for biosimulations.org. During that migration
there is a standing temptation to expose a legacy endpoint by forwarding it
verbatim — a *passthrough proxy* — rather than by modelling the data we intend
to own. This note says when that is a reasonable stop-gap and when it is not.

Prompted by PR #107, which adds `GET /projects/{id}/summary` and
`GET /runs/{run_id}/summary` as byte-faithful passthroughs.

---

## The position

**A passthrough is a migration tool, not an architecture.** It moves a URL; it
does not move ownership. Used sparingly and deliberately it buys time. Used as
the default it reproduces the legacy API inside the new one, at which point the
migration has achieved a change of hostname.

The mechanism in PR #107 is well built. That is not the question. The question is
what a passthrough *is*, and the answer is: a forwarded byte stream whose shape
we neither define nor validate nor know.

## What a passthrough actually costs

`response_model=None` is the tell. It is required — FastAPI would otherwise
interpose a schema and re-serialize — but it is also an admission:

```python
@run_summary_router.get(
    "/{run_id}/summary",
    response_model=None,          # <- we are declaring that we have no contract
    ...
)
```

Concretely, that costs:

- **No schema in OpenAPI.** Generated clients get `any`. Every consumer
  re-derives the shape by reading legacy responses, so the coupling we are trying
  to remove is duplicated into each of them instead.
- **No validation, so no early warning.** If upstream renames a field, nothing
  here fails. The break surfaces in a consumer, at runtime, at a distance from
  the cause.
- **No ability to simplify.** We forward whatever upstream sends, including
  fields nobody wants and legacy shapes we would not choose. The response is
  ~5 KB where the consumed surface is a fraction of that (see below).
- **No place to put our own semantics.** Ownership, auth scoping, deprecation,
  field additions — all of these need a model to attach to. A passthrough has
  nowhere to put them, so the first real requirement forces the rewrite anyway.
- **It hardens on contact.** Once a consumer depends on the passthrough's
  behaviour, replacing it with a typed model becomes a breaking change. The
  cheapest moment to model the data is *before* anything consumes it.

## When a passthrough is acceptable

All of these, not some:

1. **A consumer needs the endpoint before we can model it**, and the delay is
   measured in weeks, not quarters.
2. **It is recorded as temporary** — a tracking issue exists, referenced from the
   code, that names what replaces it.
3. **It stays rare.** One or two, not a pattern. See the slope below.
4. **Nothing depends on its exact upstream shape yet**, so replacing it later is
   additive rather than breaking.
5. **The data is public**, so forwarding cannot leak anything scoped to a user.

## When it is not

- As the standard way to expose legacy functionality.
- Where we already know the consumer and the fields it reads — then model it.
- Where the endpoint would carry ownership or auth semantics. Those need a model.
- Where the passthrough is being reached for because the upstream shape is
  awkward. That awkwardness is the reason to own it, not to forward it.

## The replacement pattern: own a minimal model

Define the datamodel from **what consumers actually read**, not from what
upstream happens to return. Starting smaller than the legacy payload is the
point, not a compromise:

- The model is ours, so it can be simpler than legacy from day one.
- Fields can be added later without breaking anyone. Removing them cannot.
- Upstream renames become a mapping change in one place, caught by tests.

### What the frontend reads today

The whole of `GET /projects/{id}/summary` currently reaches the frontend, which
uses this much of it (`frontend/app/pages/projects/[id].vue`,
`frontend/app/components/FilesOutputsTable.vue`):

| From | Fields consumed |
|---|---|
| project summary | `created`, `updated`, `simulationRun` |
| `simulationRun` | `id`, `name` |
| `simulationRun.run` | `simulator.name`, `simulator.version`, `projectSize`, `resultsSize` |
| `simulationRun.metadata[0]` | `abstract`, `description`, `thumbnails[0]`, `creators[].label`, `keywords[].label`, `citations`, `encodes` |

That is roughly a dozen leaf fields out of a ~5 KB response. `tasks` and
`outputs` are declared in the frontend's `SimulationRunSummary` type but are read
from `/specifications/` and `/files/`, not from the summary. `submitted` is
unused.

A model of that size is a morning's work and is strictly better than a
passthrough: typed, documented in OpenAPI, and simpler than what legacy returns.

### It is cheap right now

The frontend calls `legacy_api_url` **directly** for these summaries — it does
not go through the platform API. So the endpoints in PR #107 have **no consumers
today**. Introducing a typed model breaks nothing, and never will be cheaper.

## The slope

The frontend currently reaches past `platform` to seven legacy endpoint families:

```
/files/    /logs/    /ontologies/KISAO/    /projects/
/results/  /runs/    /specifications/
```

If passthrough is the answer for `/projects/{id}/summary`, it is equally the
answer for all seven — and the result is the legacy API re-hosted, with none of
it owned. That is the outcome this policy exists to prevent. Each of these is a
place to *decide*, and the default answer should be "model it".

## Decision for PR #107

**Merge as an explicit stop-gap**, with a tracking issue to replace both
endpoints with owned datamodels sized to the table above. It satisfies the
acceptability criteria: the data is public, nothing consumes it yet, and it
unblocks work now.

What makes it a stop-gap rather than a precedent:

- The tracking issue ([#108](https://github.com/biosimulations/platform/issues/108))
  is filed and linked from `common/proxy.py`.
- `proxy_get` is not extended to further endpoints without revisiting this note.
- The replacement is sized from consumer needs, not from the upstream payload.
