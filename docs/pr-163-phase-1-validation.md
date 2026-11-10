# PR #163 Phase 1 validation — 2026-10-30

Scope: steps 1.1–1.11 of the local PR #163 remediation plan, starting at `def76af` on
`fix/archive-verification-results`. Later remediation phases remain outside this change.

## Compose and smoke regression evidence

Host: macOS, Docker Desktop client/server 29.8.2, Compose v5.5.1. Both configurations were exercised through the
same `scripts/smoke-infrastructure.sh infra` entry point used by GitHub smoke. Separate Compose projects changed
only container names and published ports, preserving the actual images, named volumes, init command, dependencies,
and server command. This avoided replacing the developer's running in-memory stack.

| Configuration | Result |
| --- | --- |
| Fixed Compose, fresh named volume | Exit 0; Mongo and Temporal both healthy; image reports uid/gid 1000 |
| Old Compose from `git show def76af:compose.yaml`, fresh named volume | Exit 1; Temporal never becomes SERVING; SQLite cannot open the root-owned volume |
| Fixed Compose with init dependency removed in a temporary test tree | Compose drift test exits 1 at the missing `depends_on` assertion |

Fixed configuration output:

```text
mongo ready
temporal SERVING
temporal SERVING
temporal persistence probe survived container recreation
uid=1000(temporal) gid=1000(temporal) groups=1000(temporal)
mongo       Up (healthy)
temporal    Up (healthy)
```

The persistence probe starts a worker-less workflow, recreates the Temporal container, describes the same ID,
and terminates it. The separate retention integration test also restarts and recreates Temporal on a uniquely
named Docker volume initialized with the same BusyBox command from Compose, then removes the volume in `finally`.

Expected old-configuration failure output (not a deliberately broken pushed commit):

```text
mongo ready
temporal did not become SERVING
... temporal ... Restarting (1) ...
Error: failed starting server: failed setting up schema: unable to create SQLite admin DB:
unable to open database file: out of memory (14)
```

The error text reports SQLite's inability to open the file; it is reproduced by the volume ownership mismatch.
The helper then exits 1 and prints Compose state and the last 200 service log lines. Both disposable projects and
their volumes were removed. Full local logs were recorded under `/tmp/pr163-phase1-compose-{fixed,old}.log`.

## Automated regression checks

- `backend/tests/test_compose_drift.py` pins the image whose uid/gid was checked and asserts persistent storage,
  init ownership/dependency, and health-check configuration without Docker.
- `backend/tests/test_smoke_infrastructure.py` runs the real shell entry point with command stubs. Missing Mongo,
  NOT_SERVING Temporal, a missing workflow after recreation, and a degraded-ready API each exit nonzero with
  diagnostic output. The latter proves `/version` or an HTTP 200 `/ready` alone cannot make smoke pass.
- Persistence/recovery modules treat UserWarning as an error. Production and fixture model updates use
  `ReportPersistenceState` members; Mongo bookkeeping uses their values.
- Codec and store tests corrupt a valid gzip's DEFLATE block type, assert the underlying `zlib.error`, and require
  public classification as `ValueError` and then `ReportCorrupt` rather than an unclassified decompression error.

## Validation and CI

Local validation passed the exact backend CI command, `cd backend && uv run python -m pytest`: **1,617 passed,
17 skipped**, with no `PydanticSerializationUnexpectedValue`. `uv run ruff check .` and
`uv run mypy biosim_server tests` also passed (219 source files). Remote Linux backend-ci, smoke, frontend-ci,
GitGuardian and Snyk must all pass at the pushed Phase 1 SHA. The local remediation plan records final counts,
commit IDs, check URLs, and the check for absent `PydanticSerializationUnexpectedValue` in the backend CI log.

This evidence establishes Phase 1's CI and infrastructure repairs. It does not close the later phases'
immutability, payload size, operational policy, or recovery-interface findings.
