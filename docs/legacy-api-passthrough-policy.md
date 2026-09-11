# Legacy API passthrough policy

## Summary migration: #107 replaced by #108

PR #107 introduced temporary byte passthroughs for `GET /projects/{id}/summary`
and `GET /runs/{run_id}/summary`. Issue #108 replaces that stop-gap with
platform-owned Pydantic `ProjectSummary` and `RunSummary` response models.
`common/proxy.py` and `proxy_get` have been removed.

Both public routes now serialize validated models and expose named OpenAPI
schemas. Legacy response headers, arbitrary query parameters (including
`includeData`), and caller credentials are not forwarded. Upstream required-field
drift fails validation and produces a sanitized 502; timeouts produce 504,
upstream 404 remains 404, and other 4xx statuses retain their status with a
sanitized body. Redirects, unexpected success statuses, and invalid JSON are 502.

The frontend has **not** been migrated yet — that is the one remaining #108
checkbox. Both detail pages still fetch their summaries from `legacy_api_url`
(`frontend/app/pages/projects/[id].vue:84`, `frontend/app/pages/runs/[id].vue:127`),
so the platform routes have no consumers today. That is deliberate: it keeps the
switch additive rather than breaking, and it is why the models could be sized
from consumer needs instead of from the upstream payload. When the migration
lands, the project page should derive its run summary from `simulationRun`
rather than issuing a second summary request. Files, specifications and logs
are read as **inputs** to the owned page contracts and are never re-hosted: no
passthrough route exposes them, their upstream shape is not part of any
published schema, and the page payload is a closed model. Results, thumbnails,
downloads and non-summary run information stay on the legacy API.

## Own the consumer contract

Models in `backend/biosim_server/summaries/models.py` intentionally include only:

| Contract | Fields |
|---|---|
| ProjectSummary | `id`, `created`, `updated`, `simulationRun` |
| RunSummary | `id`, `name`, `run`, `metadata` |
| RunExecution | simulator `name` and `version`, optional `projectSize` and `resultsSize` |
| RunMetadataSummary | `abstract`, `description`, `thumbnails`, `creators`, `keywords`, `citations`, `encodes` |

Metadata remains a list containing zero or one record, selected from the first
upstream metadata entry. Identifiers contain nullable `uri` and `label` fields.
Unused legacy fields such as `tasks`, `outputs`, `submitted`, `owner`, run status,
and simulator digest are stripped. Private upstream models in
`summaries/mapping.py` validate required fields and ignore unknown additions;
project mapping delegates nested run conversion to the shared run mapper.

Fixture-based mapping tests pin required-field drift and consumed values.
Live integration tests compare mapped upstream values with the committed captures,
allowing unrelated legacy fields to evolve without widening our contract.

The project fixture was captured from the public
`/projects/Yeast-cell-cycle-Irons-J-Theor-Biol-2009/summary` endpoint on
2026-09-08. Its complete nested `simulationRun` object equals the retained
`run_summary_response.json` fixture for run `61fea483f499ccf25faafc4d`; there
are no wrapper-related run payload differences.

## Do not reintroduce passthrough

A passthrough moves a URL without transferring contract ownership. It gives
clients no owned schema, delays upstream drift detection until consumer runtime,
and makes unused legacy fields part of the apparent public contract.

Do not extend the former stop-gap to `/files/`, `/logs/`, `/ontologies/KISAO/`,
`/results/`, `/specifications/`, or non-summary `GET /runs/{id}`. Each migration
requires an explicit platform-owned contract based on consumer needs.

`common/upstream.py` retains safe independent path-segment encoding and dot-segment
rejection. Its `fetch_upstream_json` helper fetches JSON inputs for owned models;
it is not a passthrough and is not permission to re-host arbitrary legacy
endpoints. Any future migration must define its model, centralized mapping,
OpenAPI contract, drift tests, and consumer migration explicitly.

## Platform-owned page aggregation

`GET /projects/{project_id}/page` and `GET /runs/{run_id}/page` are
platform-owned aggregation endpoints, not passthroughs. Their typed contracts
live in `biosim_server/pages`; private upstream models keep page-only run fields
out of the existing public summary models. Both `/summary` endpoints remain
available with unchanged contracts and authentication behavior.

The project page makes three direct upstream requests: project summary, then
files and specifications concurrently using the embedded simulation run ID.
The run page makes four: run summary, then files, specifications, and logs
concurrently. Neither endpoint calls the platform's own summary routes or
requires database access or authentication.

Only satellite 404s produce empty files/specifications or null logs. A missing
satellite is a normal state for a run that has none. Satellite 5xx, timeout,
invalid JSON, and field drift fail the whole page (502, or 504 on timeout) so
an upstream outage is not presented as emptiness. Identity 404s fail the page;
other identity failures retain sanitized gateway errors. Metadata comes only
from the first record, and project model formats are derived from full
specifications before public projection.
Curve `style` may be an inline object or a SED-ML style id string, matching the
existing data-generator union. Caller query parameters, headers, and credentials
are not forwarded.

In-progress runs may omit or null `projectSize`/`resultsSize`; both page
payloads serialize those as null instead of failing. Citation and encodes
identifiers accept a null `uri`, matching the summary `LabeledIdentifier`
contract. Project-page identity parse does not require `submitted`, `updated`,
or run `status`, because those fields are absent from the project page output.
Run-page `info` still requires them.

The frontend continues using `legacy_api_url` until a later frontend-specific
PR. This change adds the page APIs and does not migrate frontend consumers.
