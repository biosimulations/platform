# Verification report retention — operations

Reports are immutable in `BiosimVerifyReports`. The `verification-json-gzip-v2` codec preserves non-finite scores
and literal dictionary keys. The checksum covers canonical uncompressed report bytes. A report larger than
`VERIFICATION_REPORT_INLINE_MAX_BYTES` (12 MiB) is stored in object storage and referenced from its envelope;
decompression above `VERIFICATION_REPORT_MAX_DECOMPRESSED_BYTES` (64 MiB) is refused as corrupt. A published
report is never replaced: an identical retry is a no-op, and different content, owner or run ID for the same
workflow is an integrity conflict.

**What travels through Temporal.** Statistics of at most `VERIFICATION_ACTIVITY_INLINE_MAX_BYTES` (512 KiB of
uncompressed JSON) travel inline in the statistics activity's result and the persist activity's input, so they are
part of the workflow's history until the report is stored. Larger statistics are written to object storage first,
and only a reference crosses Temporal. Once the report is stored, the persist activity's result, the workflow
result and every progress query carry no statistics. (An earlier version of this runbook said statistics never
travel through Temporal payloads. That was not true.)

The API reads saved reports without Temporal or upstream services. A failed initial Temporal connection is
retried in the background every five seconds; workers wait for the connection. Readiness accepts healthy Mongo
and initialized report readers while reporting Temporal as degraded, so Kubernetes still routes saved-report reads.
Shutdown cancels that task.

## Persistence: retried without a cap, or BLOCKED

All three verification workflows (`OmexVerifyWorkflow`, `RunsVerifyWorkflow`, `ReconstructComparisonWorkflow`)
publish through one helper, `biosim_verify/workflow_persistence.py`. The persist activity stores the report before
it updates the ledger pointer or acknowledges completion.

- **Transient failures are retried with no attempt limit**: 1s initial interval, doubling, at most 5 minutes apart,
  2 minutes per attempt. A workflow that is retrying stays RUNNING, and Temporal retention only starts when a
  workflow closes, so the computed output stays in its history until storage accepts it. A storage outage delays a
  report; it never turns a computed result into a failure, and it is never reported as durable completion.
- **Externalized statistics wait for storage too.** Statistics above 512 KiB are written to object storage by the
  statistics activity, before the persist activity runs. A failed write is retried inside the attempt that already
  computed the statistics, with the same backoff as a failed report write (1s, doubling, at most 5 minutes). The
  computed bytes stay in memory, so those retries do not download the datasets again and do not use the 100
  attempts that computing gets. Each retry logs `verification_statistics_storage_unavailable`. The attempt holds
  its worker slot while it waits and heartbeats, so a dead worker is retried; that retry recomputes once. One day
  bounds the activity, storage waits included, so an outage shorter than a day does not fail the verification.
- **Reconstruction's comparison activity writes storage too** (the snapshot manifest and, when statistics are
  large, the statistics artifact). Transient failures retry with the persist backoff and no attempt cap. A
  checksum mismatch or a missing snapshot is non-retryable, and the derived report completes FAILED. A storage
  outage delays that derived report; it does not fail it after a few seconds.
- **Permanent failures complete the workflow as `BLOCKED`**: a `ValueError`, an integrity conflict, a corrupt
  artifact, or a missing artifact. The ledger row is marked `blocked`, and the workflow completes with the computed
  output marked `persistence_state=blocked`. A successful verification is never reported as FAILED. Unconfigured or
  unreachable storage stays retryable. A completed workflow's history is subject to retention, so a blocked result
  must be stored before it expires (see "Responding").
- **A failed ledger read is retried**, never treated as "no link", so a report is never stored without its
  re-verification link.
- **After the report is stored**, the activity deletes the workflow's transient objects (its statistics artifact and
  snapshot pointer). A failed delete logs `verification_transient_cleanup_failed` and is left to the lifecycle rule.
  A retried attempt that finds the report already stored for the same run and owner only acknowledges it.

A failed, terminated, cancelled or timed-out execution is recovered from its own history
(`biosim_verify/history.py`). If the workflow scheduled the persist activity, that activity's input is the computed
output, and it is what gets stored, after its workflow ID, run ID and owner are checked against the start event.
Only when the history proves that no output was computed is a FAILED report built, from the start input and the
close time alone, so `GET /verify/{id}` and the reconciler write byte-identical content. If two writers race anyway,
the loser serves the stored winner: never a 500.

**Draining while storage is broken.** An uncapped retry holds the workflow open, which blocks the worker drain.
Restore storage first. Terminating a workflow that is stuck in persistence is safe once storage works again: the
reconciler, or the next GET, recovers its computed output from history. A terminated workflow's history is then
subject to retention, so make sure the reconciler runs before the history expires.

## Data converter

The API and worker now connect to Temporal with `pydantic_data_converter`. `backend-0.10.3` used the SDK's default
`DataConverter`. Backfill and history recovery decode histories that 0.10.3 wrote, so this was tested rather than
assumed: `tests/biosim_verify/test_converter_compat.py` round-trips every payload type 0.10.3 could write (with NaN,
±Inf, enums, datetimes and `None`) from the default converter to the new one, and reads and replays a history
written by the default converter through `read_start_input`, `result()` and the Replayer. No decoding shim is
needed. A new workflow or activity payload type should get a sample in that test. The worker drain is still
required for workflow-code changes.

## Lookup during a Mongo outage

`GET /verify/{id}` reads the ledger row and the saved report first. When that lookup fails with a dependency error
(the ledger or the report store is unreachable), it answers what Temporal alone can, and writes nothing until Mongo
is back:

| Execution | Answer during the outage |
| --- | --- |
| Running | `200` progress, authorized from the workflow's own owner, `Cache-Control: private, no-store` |
| Completed, result never stored (`blocked` or `pending`) | `200` with the result, marked `pending` and `report_available=false` |
| Completed with a stored report, any failed execution, or an unknown ID | `503` with `Retry-After: 5`; never a `404` |
| Not a verification workflow | `404`, because Temporal says what it is |

A report that cannot be decoded (`ReportCorrupt`) is not an outage: it stays a plain `503`. **The trade-off: stored
reports depend on Mongo, and running progress does not.** The frontend's existing back-off handles the `503`.

## Local Temporal and developer migration

`compose.yaml` runs `temporal server start-dev --db-filename /data/temporal.db` on the named volume
`temporal_data`. The image runs as uid/gid 1000, but a fresh named volume is root-owned, so on its own the server
exits with `unable to open database file`. A one-shot `temporal-data-init` service (`busybox`, `chown -R 1000:1000
/data`) runs before every start, and `temporal` waits for it to complete. It repairs an existing root-owned volume
too, so a volume created by an earlier build of this branch does not need deleting. `mongo` and `temporal` have
health checks; `docker compose ps` shows both `healthy` once they are usable.

The next `docker compose up -d` recreates `platform-temporal` on the volume. Existing in-memory history is **not**
migrated. Never run `docker compose down -v`: it deletes `mongo_data` and `temporal_data`. Retention still applies
locally; the volume only makes history survive a restart or recreation. The smoke workflow runs the same compose
file in CI: it requires Temporal to be `SERVING`, proves that a workflow survives container recreation, and
requires `checks.temporal == true` from the API. The evidence that it fails on the old configuration is in
`docs/pr-163-phase-1-validation.md`.

## Backfill and local migration

First select the intended stack through `MONGODB_URI`, `MONGODB_DATABASE`, `TEMPORAL_SERVICE_URL`,
`TEMPORAL_NAMESPACE`, and storage settings. Use the existing environment/secret mechanisms rather than putting
credentials in shell history. Commands below run from `backend/`.

1. Stop accepting verification submissions and drain running work. Keep the existing in-memory Temporal process
   alive until backfill has finished. Record unresolved work instead of terminating it.
2. Inventory and inspect a dry-run checkpoint, using a persistent operator directory:

   ```bash
   uv run python -m scripts.backfill_verification_reports --inventory --limit 100 \
     --max-pages 100 --checkpoint /persistent/operator/verification-dry.json
   ```

3. Apply with a separate checkpoint:

   ```bash
   uv run python -m scripts.backfill_verification_reports --inventory --limit 100 --max-pages 100 \
     --checkpoint /persistent/operator/verification-apply.json --apply
   ```

   Each invocation processes bounded ledger and Temporal pages. Resume with the same command after a page-budget
   exit; `complete=true` means the scan finished, not that every original was recoverable. Resolve dependency,
   integrity, unknown-ownership, and still-running/pending entries before migration. Missing history remains explicitly unavailable.
   An explicitly public `owner_sub=null` can be recovered; an absent owner field cannot create a public orphan row.
   Dry-run/apply checkpoints, target fingerprints, inventory mode, and time windows cannot be mixed.
   Optional `--since`/`--until` require timezone-qualified ISO timestamps. The default upper watermark is fixed
   for a pass. Invoking a completed checkpoint starts a new pass, including earlier executions that closed late.
4. Repeat after the drain to include late closes. Export and verify a backup as described below.
5. Recreate only Temporal, with the Compose `temporal-data-init` service and `temporal_data` volume described above.
   Do not use `docker compose down -v`. Existing in-memory history is not automatically migrated to SQLite.
6. Reconnect workers, reopen submissions, and verify stored original reports. Retention still applies to Temporal.

A checkpoint contains identifiers and hashes, so it is written with mode 0600. Keep one writer per checkpoint.
Backfill never starts simulations or comparisons. Its nonzero exits prevent incomplete inventories from being
mistaken for a successful migration. Backfill reads 0.10.3 histories under the new data converter (see "Data
converter").

## Verified backups and restore

The application bundle includes ledger/request BSON, immutable report envelopes, referenced report objects, and
a checksum manifest. It does not back up the entire platform, archive collection, simulation cache, or Temporal.
A failed export has no completion manifest and cannot be restored. Store the directory on encrypted persistent
backup storage with access equivalent to the source reports; it includes private scientific data.

With submissions drained and after backfill, pass that cluster's `MONGODB_DATABASE`. GKE production is
`biosimulations-prod` (`kustomize/config/biosim-gke/shared.env`). RKE and local are `biosimulations`
(`kustomize/config/biosim-rke/shared.env`, `kustomize/config/biosim-local/shared.env`).

```bash
# GKE
uv run python -m scripts.backup_verification_reports export \
  --database biosimulations-prod --directory /persistent/backups/verification-2026-10-29
```

```bash
# RKE and local
uv run python -m scripts.backup_verification_reports export \
  --database biosimulations --directory /persistent/backups/verification-2026-10-29
```

Configure an isolated destination Mongo server/database and independent object storage before a restore drill.
Verify without changing the destination:

```bash
uv run python -m scripts.backup_verification_reports restore --database verification_restore_drill \
  --directory /persistent/backups/verification-2026-10-29
```

Then add `--apply` to restore. The tool validates all file checksums and report identities before writing,
uploads destination objects before inserting their references, and rejects conflicts with existing requests or
reports. Identical retries are safe. A restored object is published at the deterministic name the report would
have had (`verification-reports/{report_id}/{content_sha256}.json.gz`), so repeated restores create no new objects.
Backup and restore use only the report store's and the ledger's public methods. Read every restored report through
the report store/API and compare manifest content hashes; merely importing BSON is not restore acceptance.

The isolated integration test executes the real export/dry-run/restore commands' service functions, verifies
restored reports, deletes only test histories, and reads reports with Temporal stopped. It also backfills old
workflow results before restarting and replacing the isolated Temporal container. Production RPO, backup schedule,
provider versioning/lifecycle, retention, and access policies must be recorded by the deployment owner; this local
drill does not certify an existing production backup service. Before rollout, choose a backup interval and
retention that meet the accepted RPO, and perform the same drill against a production backup in an isolated target.

## Reconciliation, acknowledgement and alerts

The GitOps CronJob runs `scripts.reconcile_verification_reports --apply --limit 100` every five minutes, with
concurrency forbidden, a four-minute job deadline, a 150-second scan budget, and a 45-second per-row budget. It uses
`restartPolicy: Never` and `backoffLimit: 0`: the next scheduled run is the retry. A Mongo lease and row-by-row
cursor in `BiosimVerifyMaintenance` carry progress across jobs and fence concurrent writers. A job killed at its
deadline resumes after the last acknowledged row once its five-minute lease expires. A completed pass resets the
cursor so old pending work is revisited. Local operators can run the same module without `--apply` to inspect
candidates.

The CronJob is the opt-in kustomize component `kustomize/components/verification-reconcile`, not part of `base`.
No overlay enables it yet, because every deployed `platform-api` tag predates `scripts.reconcile_verification_reports`
and the job would fail on each run. Enable it in the deploy PR that moves an overlay to a backend release containing
the module, by adding `components: [../../components/verification-reconcile]` next to the `newTag` bump. For
`biosim-gke`, merging that PR deploys both. Its `/app/scratch` is a per-pod `emptyDir`: the worker already holds the
`ReadWriteOnce` `worker-cache-pvc`, so a job scheduled on another node could not attach it.

### Exit status is job health

| Run | Exit | Events |
| --- | --- | --- |
| A scan ran, complete or budget-limited, whatever its rows need | 0 | `verification_reconcile_summary` (INFO); `verification_persistence_attention` (ERROR) as well when a row needs an operator |
| Another job holds the lease | 0 | `verification_reconcile_summary` with `skipped="lease_busy"`; the lease expires by itself within five minutes |
| Initialization failure, an unreachable Temporal (`reason="temporal_unavailable"`), a lost lease, or an unexpected exception | 1 | `verification_reconcile_job_failed` (ERROR) with the reason and exception type, never the message. No row is touched and no summary is logged |

Rows are never a job failure. The summary carries counts only, never request payloads or owner identifiers:
`scanned`, `saved`, `already_present`, `pending`, `missing_history`, `integrity_conflict`, `permanent_failure`,
`dependency_error`, `blocked`, `acknowledged`, `persist_retrying`, `computing`, `stale_persist`, `stale_running`, `attention` and
`scan_partial`.

A row needs an operator (`attention`) when it has a dependency error, an integrity conflict or a permanent failure,
when it is `blocked`, or when its execution is stale. The reconciler classifies a failed save as the persist activity
does: an integrity conflict or a permanent failure (a `ValueError` such as the archival size limit, a corrupt
artifact, or a missing statistics artifact) marks the row `blocked`; anything else is a `dependency_error` and marks
it `pending`. A dependency error never replaces `blocked`: a timeout or an outage while retrying a blocked row leaves
the row, its error and its acknowledgement as they were.

- `persist_retrying`: the persist activity is pending at attempt 3 or later. Its age is the sum of the retry
  intervals up to that attempt, a lower bound, because Temporal does not report when the first attempt started. It
  is attention after `--stale-persist-after` (default 3600 seconds). The alert is never early, but it can be late
  when each attempt hangs until its timeout.
- `computing`: anything else still running. It is attention after `--stale-running-after` (default 86400 seconds).
  A statistics storage outage is not this signal.

Other events: the worker logs `verification_persistence_failure` the moment a persist attempt fails;
`verification_statistics_storage_unavailable` on each statistics storage retry, while the activity is still
waiting in the attempt that computed the comparison; `verification_object_integrity_conflict` means an object at
a content address did not hold its content (see "Object storage"); `verification_snapshot_capture_failed` and
`verification_snapshot_capture_skipped` are warnings that leave the original report intact and only make it
ineligible for reconstruction.

### Acknowledging a row

An acknowledgement silences attention for one ledger row without deleting or changing its report. It is valid only
while the row keeps the persistence state and error it acknowledged; a different failure alerts again. The command
is dry-run by default, prints only persistence bookkeeping, and refuses stored rows, rows not in `--expect-state`,
and rows that change between the read and the write.

```bash
uv run python -m scripts.ack_verification_persistence --workflow-id WF \
  --expect-state blocked --by alice --reason "upstream fix in 0.11.1"            # dry-run
uv run python -m scripts.ack_verification_persistence --workflow-id WF \
  --expect-state blocked --by alice --reason "upstream fix in 0.11.1" --apply
uv run python -m scripts.ack_verification_persistence --workflow-id WF \
  --expect-state blocked --by alice --clear --apply                               # remove it
```

The reconciler skips acknowledged `blocked` rows and counts them as `acknowledged`. After a fix release, run it
once with `--include-acknowledged` to retry them. Other acknowledged rows are still processed, so they are saved
when they can be, but they are not attention.

### Alert templates

Deployment templates in `ops/monitoring/` cover:

- A log alert on `verification_persistence_attention`, `verification_persistence_failure`,
  `verification_statistics_storage_unavailable` and `verification_object_integrity_conflict`, rate limited to one
  notification per 15 minutes. The statistics event fires on each storage retry, so an outage is visible while
  the activity is still waiting, rather than when the one-day computing staleness threshold arrives.
- A log-based run counter of `verification_reconcile_summary`, and an absence policy for 30 minutes without one.
  Every run that scanned, or found the lease busy, logs the summary, so the heartbeat counts healthy runs whatever
  their rows need.

The [Cloud Monitoring log alert format](https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts)
requires a notification channel. Replace `PROJECT_ID` and `CHANNEL_ID` with the deployment's approved channel,
create the log metric and both policies through the normal infrastructure review, and verify delivery during
rollout. These files are templates; rendering them does not install alert routing or send a notification.
The heartbeat policy must first observe a run. Confirm the CronJob is enabled and run one controlled staging
failure plus recovery; record the incident and successful subsequent run. Missing initialization logs or a
suspended CronJob are detected through the missing heartbeat, not the per-report alert.

### Responding

- `dependency_error` or `stale_persist`: restore the dependency and let the next run save the report, before the
  history expires.
- `permanent_failure`: retrying cannot store this report; the row is now `blocked` (see below).
- `integrity_conflict` or `verification_object_integrity_conflict`: investigate; never delete or replace an original
  report to clear it. A content-address conflict left a `.repair-<uuid>` copy beside the object.
- `blocked`: the computed result is in the completed workflow's result, and a completed workflow's history is subject
  to retention (24 hours in production). Fix the cause, release, and rerun with `--include-acknowledged` before the
  history expires; after that the result can no longer be stored. Acknowledge the row meanwhile to silence the alert.
- `stale_running`: a long computation, or a stuck one. Check the execution in Temporal.

Escalate before the oldest pending work approaches the measured namespace retention.

## Object storage: names, lifecycle and garbage collection

Every object name is derived from content or identity (`biosim_verify/object_names.py`). A retry or a second
verification of the same data reuses the object instead of creating another, so storage grows with distinct content,
not with retries. Objects are only ever created: GCS uses `ifGenerationMatch=0`, local storage links a temporary file
into place, and MinIO uses `If-None-Match: *`. An existing object whose content does not match its address is never
overwritten; the bytes go to a `.repair-<uuid>` copy and `verification_object_integrity_conflict` is logged.

| Object | Name | Lifetime |
| --- | --- | --- |
| Snapshot data | `verification-snapshots/data/{digest}.json.gz` | long-lived, shared |
| Snapshot manifest | `verification-snapshots/manifests/{digest}.json.gz` | long-lived |
| Externalized report | `verification-reports/{report_id}/{digest}.json.gz` | long-lived |
| Statistics artifact | `verification-activity/{workflow_id}/{digest}.json.gz` | transient |
| Snapshot index pointer | `verification-snapshots/index/{sha256(workflow_id)}.json` | transient |
| Restores from before deterministic names | `verification-restored/…` | collected when unreferenced |

Numerical-input snapshots above `VERIFICATION_SNAPSHOT_MAX_BYTES` (64 MiB uncompressed per verification) are not
captured; the report records that, and reconstruction is refused as `work_limit`.

**Transient objects** are deleted by the persist activity once the report is stored.
`ops/storage/verification-lifecycle.json` is the backstop: a GCS rule that deletes the two transient prefixes after
30 days. It is not applied automatically. Applying it replaces the bucket's whole lifecycle configuration, so merge
it with the existing rules through infra review (`ops/storage/README.md`). Check that the service account has
`storage.objects.delete`; without it, only this rule removes transient objects.

**Long-lived objects** are collected by reachability with `scripts.gc_verification_objects`. The reference set is
every object a stored report names (its payload, snapshots and manifest), plus everything a live snapshot pointer
reaches (a verification still persisting). A candidate is unreferenced and older than `--grace` days (default 7).

1. Dry-run and read the report. Every run writes a 0600 JSON report and prints per-prefix totals: objects, bytes,
   unreferenced objects and bytes, and candidates.

   ```bash
   uv run python -m scripts.gc_verification_objects --report /persistent/operator/verification-gc-dry.json
   ```

2. Delete only after reviewing the candidates, oldest first and at most `--max-deletes` (default 1000):

   ```bash
   uv run python -m scripts.gc_verification_objects --apply --max-deletes 1000 \
     --report /persistent/operator/verification-gc-apply.json
   ```

   `--apply` rebuilds the reference set immediately before deleting, so an object that became referenced during the
   listing is kept. It refuses to delete anything while a live pointer cannot be resolved. Nothing a report
   references is ever deleted.

Deletion cannot be undone. Run the dry-run regularly for a month and compare its reports before considering a
scheduled GC job. **Never run GC during a rollback**: an older build may still need objects a newer one stopped
referencing.

## Local evidence and live rollout boundary

`test_retention_crash.py` SIGKILLs a real worker process before/after object upload, before/after manifest commit,
before/after ledger update, and before activity acknowledgement. A replacement worker must publish exactly one
report and complete its ledger without GET. `test_retention_integration.py` covers old/new replay, migration
pagination, persistent Temporal restart/recreation on a named volume initialized like `compose.yaml`, reconnection
after startup outage, history deletion, and a verified independent restore. `test_compose_drift.py` and
`test_smoke_infrastructure.py` pin the compose fix and prove smoke fails on a broken Temporal.

The review remediation added: `test_persistence_failures.py` (uncapped retries, BLOCKED, recovery from history,
converging writers), `test_workflow_payloads.py` (no statistics after persistence), `test_converter_compat.py`,
`test_reconcile_job.py` and `test_persistence_ack.py` (exit status and acknowledgement), `test_object_storage.py`,
`test_transient_objects.py` and `test_object_gc.py` (bounded storage), `test_layering.py`, and
`tests/api/test_verification_outage.py` (the outage table above). Component tests mount the actual Vue
setup/lifecycle and exercise polling, backoff, access/expiry states, late responses, and unmount cancellation.
Existing authentication tests exercise the actual Auth0 request plugin.

These are local and CI acceptance results. Image publication, production backfill, shared-stack migration, live
backup policy, alert routing, the lifecycle rule, a production GC dry-run baseline, and a production restore drill
remain deployment acceptance steps. No release or production mutation is performed by the test suite.
