"""The reconcile CronJob and its monitoring agree with the job's exit-status contract (WP-6)."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_a_failed_job_is_not_retried_in_place() -> None:
    cronjob = yaml.safe_load(
        (ROOT / "kustomize/components/verification-reconcile/verification-reconcile-cronjob.yaml").read_text()
    )
    job = cronjob["spec"]["jobTemplate"]["spec"]
    # Row problems exit 0; a non-zero exit is a job failure that the next run retries.
    assert job["backoffLimit"] == 0
    assert job["template"]["spec"]["restartPolicy"] == "Never"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"


def test_the_heartbeat_counts_summaries_and_the_alert_matches_attention() -> None:
    metric = yaml.safe_load((ROOT / "ops/monitoring/verification-reconcile-metric.yaml").read_text())
    assert "verification_reconcile_summary" in metric["filter"]
    assert "verification_reconcile_success" not in metric["filter"]
    alert = json.loads((ROOT / "ops/monitoring/verification-persistence-alert.json").read_text())
    [condition] = alert["conditions"]
    log_filter = condition["conditionMatchedLog"]["filter"]
    assert "verification_persistence_attention" in log_filter
    assert "verification_persistence_failure" in log_filter
    # A content address holding the wrong bytes is attention too (WP-7a).
    assert "verification_object_integrity_conflict" in log_filter
    assert "verification_reconcile_summary" not in log_filter
