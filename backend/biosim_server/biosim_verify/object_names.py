"""Deterministic object names for verification storage.

Every name is derived from content or identity, never from a random suffix, so
a retry or a second verification of the same data reuses the object instead of
creating a new one (I-6: storage is O(distinct content)).

=====================  ===========================================  ====================
Object                 Name                                         Lifetime
=====================  ===========================================  ====================
Snapshot data          verification-snapshots/data/{digest}         shared, long-lived
Snapshot manifest      verification-snapshots/manifests/{digest}    long-lived
Externalized report    verification-reports/{report_id}/{digest}    long-lived
Statistics artifact    verification-activity/{workflow_id}/{digest} transient
Snapshot index pointer verification-snapshots/index/{sha256(id)}    transient (mutable)
=====================  ===========================================  ====================

Statistics artifacts are scoped per workflow, so age-based expiry of that prefix
can never delete an object that a newer workflow is reusing.
"""
import hashlib
from collections.abc import Callable
from urllib.parse import quote

from biosim_server.biosim_verify.report_codec import checksum

ACTIVITY_PREFIX = "verification-activity/"
SNAPSHOT_DATA_PREFIX = "verification-snapshots/data/"
SNAPSHOT_MANIFEST_PREFIX = "verification-snapshots/manifests/"
SNAPSHOT_INDEX_PREFIX = "verification-snapshots/index/"
REPORT_PREFIX = "verification-reports/"
# Written by restores before names were deterministic; only garbage collection reads it.
LEGACY_RESTORE_PREFIX = "verification-restored/"

#: Removed after persistence, with a lifecycle rule as the backstop.
TRANSIENT_PREFIXES = (ACTIVITY_PREFIX, SNAPSHOT_INDEX_PREFIX)
#: Kept while a report or a live pointer references them; garbage collection removes the rest.
COLLECTED_PREFIXES = (SNAPSHOT_DATA_PREFIX, SNAPSHOT_MANIFEST_PREFIX, REPORT_PREFIX, LEGACY_RESTORE_PREFIX)


def _segment(identity: str) -> str:
    """One path segment for a workflow or report id: never a separator or a dot segment."""
    segment = quote(identity, safe="-_.~")
    if segment in {"", ".", ".."}:
        return hashlib.sha256(identity.encode()).hexdigest()
    return segment


def statistics_artifact_path(workflow_id: str, digest: str) -> str:
    return f"{ACTIVITY_PREFIX}{_segment(workflow_id)}/{digest}.json.gz"


def snapshot_data_path(digest: str) -> str:
    return f"{SNAPSHOT_DATA_PREFIX}{digest}.json.gz"


def snapshot_manifest_path(digest: str) -> str:
    return f"{SNAPSHOT_MANIFEST_PREFIX}{digest}.json.gz"


def snapshot_index_path(workflow_id: str) -> str:
    return f"{SNAPSHOT_INDEX_PREFIX}{hashlib.sha256(workflow_id.encode()).hexdigest()}.json"


def report_artifact_path(report_id: str, digest: str) -> str:
    return f"{REPORT_PREFIX}{_segment(report_id)}/{digest}.json.gz"


def holds_content(digest: str, encoding: str) -> Callable[[bytes], bool]:
    """Whether stored bytes decode to the content whose canonical SHA-256 is *digest*."""
    def matches(payload: bytes) -> bool:
        return checksum(payload, encoding=encoding) == digest
    return matches

