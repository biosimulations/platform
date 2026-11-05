# Verification storage templates

`verification-lifecycle.json` is a GCS lifecycle rule for the bucket that holds
verification objects. It is **not applied automatically**. Like the alert
templates in `ops/monitoring/`, it goes through infra review first.

It deletes objects older than 30 days under the two **transient** prefixes only:

- `verification-activity/`: statistics artifacts too large to cross Temporal inline.
- `verification-snapshots/index/`: per-workflow pointers to a snapshot manifest.

The persist activity deletes both, best-effort, as soon as a report is stored.
This rule is the backstop for deletes that fail, for example when the service
account lacks `storage.objects.delete` on the bucket. Check that permission before
rollout: without it, only this rule cleans up.

Nothing a stored report references lives under these prefixes. Snapshot data,
manifests and externalized reports are long-lived. `scripts/gc_verification_objects.py`
collects those once nothing references them.

30 days is far beyond the 24-hour window in which history recovery can still need
a statistics artifact, and beyond the 1-hour `persist_retrying` alert.

**Applying it replaces the bucket's whole lifecycle configuration.** Read the
current rules first and merge them:

```bash
gcloud storage buckets describe gs://BUCKET --format="default(lifecycle_config)"
gcloud storage buckets update gs://BUCKET --lifecycle-file=merged-lifecycle.json
```
