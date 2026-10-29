# Verification report retention — Phase A operations

Reports are immutable in `BiosimVerifyReports`. The `verification-json-gzip-v2` codec preserves non-finite scores
and literal dictionary keys. The checksum covers canonical uncompressed report bytes. Large statistics are
externalized before returning from the producing activity. Each upload attempt writes a unique object, verifies
its bytes, then publishes its reference through an insert-once manifest. Retries can leave unreferenced objects;
they cannot replace a published report. No garbage collector deletes these objects automatically.

The API reads saved reports without Temporal or upstream services. A failed initial Temporal connection is
retried in the background every five seconds; workers wait for the connection. Readiness accepts healthy Mongo
and initialized report readers while reporting Temporal as degraded, so Kubernetes still routes saved-report reads. Shutdown cancels that task.
Finalization stores the report before updating its ledger pointer or acknowledging completion. Permanent encoding
or integrity failures mark a row blocked and fail the activity; transient outages remain retryable. The scheduled
reconciler can repair a committed manifest's missing ledger pointer without a user lookup.

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
5. Recreate only Temporal using the Compose `temporal_data` volume and `--db-filename /data/temporal.db` configuration.
   Do not use `docker compose down -v`. Existing in-memory history is not automatically migrated to SQLite.
6. Reconnect workers, reopen submissions, and verify stored original reports. Retention still applies to Temporal.

A checkpoint contains identifiers and hashes, so it is written with mode 0600. Keep one writer per checkpoint.
Backfill never starts simulations or comparisons. Its nonzero exits prevent incomplete inventories from being
mistaken for a successful migration.

## Verified backups and restore

The application bundle includes ledger/request BSON, immutable report envelopes, referenced report objects, and
a checksum manifest. It does not back up the entire platform, archive collection, simulation cache, or Temporal.
A failed export has no completion manifest and cannot be restored. Store the directory on encrypted persistent
backup storage with access equivalent to the source reports; it includes private scientific data.

With submissions drained and after backfill:

```bash
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
reports. Identical retries are safe. Read every restored report through the report store/API and compare manifest
content hashes; merely importing BSON is not restore acceptance.

The isolated integration test executes the real export/dry-run/restore commands' service functions, verifies
restored reports, deletes only test histories, and reads reports with Temporal stopped. It also backfills old
workflow results before restarting and replacing the isolated Temporal container. Production RPO, backup schedule,
provider versioning/lifecycle, retention, and access policies must be recorded by the deployment owner; this local
drill does not certify an existing production backup service. Before rollout, choose a backup interval and
retention that meet the accepted RPO, and perform the same drill against a production backup in an isolated target.

## Reconciliation and persistence alerts

The GitOps CronJob runs `scripts.reconcile_verification_reports --apply` every five minutes, with concurrency
forbidden, a four-minute job deadline, a 150-second scan budget, and a 45-second per-row budget. A Mongo lease
and row-by-row cursor in `BiosimVerifyMaintenance` carry progress across jobs and fence concurrent writers.
A job killed at its deadline resumes after the last acknowledged row once its five-minute lease expires.
A completed pass resets the cursor so old pending work is revisited. Deploy only with a backend image containing these
modules. Local operators can run the same module without `--apply` to inspect candidates.

Each run emits a JSON summary event with counts only. A dependency error, integrity conflict, blocked row, or
pending row older than one hour emits `verification_persistence_attention`, logs at ERROR, and exits nonzero.
The summary contains neither request payloads nor owner identifiers. A healthy run emits
`verification_reconcile_success`. Immediate publication failures also emit `verification_persistence_failure`.

Deployment templates in `ops/monitoring/` cover:

- Matching persistence failures/attention events, rate limited to one notification per 15 minutes.
- A log-based run counter and an absence policy for 30 minutes without a reconciliation summary.

The [Cloud Monitoring log alert format](https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts)
requires a notification channel. Replace `PROJECT_ID` and `CHANNEL_ID` with the deployment's approved channel,
create the log metric and both policies through the normal infrastructure review, and verify delivery during
rollout. These files are templates; rendering them does not install alert routing or send a notification.
The heartbeat policy must first observe a run. Confirm the CronJob is enabled and run one controlled staging
failure plus recovery; record the incident and successful subsequent run. Missing initialization logs or a
suspended CronJob are detected through the missing heartbeat, not the per-report alert.

Respond by restoring dependency access and rerunning reconciliation before history expiry. Never delete or
replace an original report to clear an integrity alert. Escalate before the oldest pending work approaches the
measured namespace retention.

## Local evidence and live rollout boundary

`test_retention_crash.py` SIGKILLs a real worker process before/after object upload, before/after manifest commit,
before/after ledger update, and before activity acknowledgement. A replacement worker must publish exactly one
report and complete its ledger without GET. `test_retention_integration.py` covers old/new replay, migration
pagination, persistent Temporal restart/recreation, reconnection after startup outage, history deletion, and a
verified independent restore. Component tests mount the actual Vue setup/lifecycle and exercise polling,
backoff, access/expiry states, late responses, and unmount cancellation. Existing authentication tests exercise
the actual Auth0 request plugin.

These are local acceptance results. Image publication, CI, production backfill, shared-stack migration, live
backup policy, alert routing, and a production restore drill remain deployment acceptance steps. No release or
production mutation is performed by the local test suite.
