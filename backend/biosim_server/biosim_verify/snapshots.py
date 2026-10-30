"""Content-addressed numerical inputs for comparison-only reconstruction.

Snapshot bytes are written from data the comparison activity already holds.
A small per-workflow pointer lets report publication copy the manifest into
provenance without adding a workflow command.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence

from biosim_server.biosim_runs import Hdf5DataValues
from biosim_server.biosim_verify.comparison import simulator_label
from biosim_server.biosim_verify.models import (
    MAX_SNAPSHOT_CONCURRENCY,
    NumericalInputSnapshot,
    RunData,
    SimulationRunInfo,
)
from biosim_server.biosim_verify.report_codec import checksum, decode_report, encode_report
from biosim_server.dependencies import get_file_service


class SnapshotMissing(Exception):
    """A referenced snapshot object is not in storage."""


class SnapshotMismatch(Exception):
    """Stored bytes do not match the recorded checksum, shape, or identity."""


def _pointer_path(workflow_id: str) -> str:
    digest = hashlib.sha256(workflow_id.encode()).hexdigest()
    return f"verification-snapshots/index/{digest}.json"


def _snapshot_document(run_data: RunData, simulator_version: str) -> dict[str, object]:
    return {
        "run_id": run_data.run_id,
        "dataset_name": run_data.dataset_name,
        "var_names": list(run_data.var_names),
        "shape": list(run_data.data.shape),
        "values": list(run_data.data.values),
        "simulator_version": simulator_version,
    }


async def _bounded(items: Sequence[object], worker: Callable[[object], Awaitable[object]]) -> list[object]:
    semaphore = asyncio.Semaphore(MAX_SNAPSHOT_CONCURRENCY)

    async def one(item: object) -> object:
        async with semaphore:
            return await worker(item)

    return list(await asyncio.gather(*(one(item) for item in items)))


async def publish_snapshot_manifest(
    workflow_id: str, ordered_run_ids: list[str], snapshots: list[NumericalInputSnapshot],
) -> None:
    file_service = get_file_service()
    if file_service is None:
        return
    manifest = {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "ordered_run_ids": ordered_run_ids,
        "snapshots": [snapshot.model_dump() for snapshot in snapshots],
    }
    compressed, encoding, size, digest = encode_report(manifest)
    reference = await file_service.upload_bytes_immutable(
        compressed, f"verification-snapshots/manifests/{digest}.json.gz",
    )
    pointer = json.dumps(
        {"reference": reference, "sha256": digest, "encoding": encoding, "uncompressed_bytes": size},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    await file_service.upload_bytes(pointer, _pointer_path(workflow_id))


async def capture_input_snapshots(
    workflow_id: str, sim_run_info_list: list[SimulationRunInfo], run_data: list[RunData],
) -> list[NumericalInputSnapshot] | None:
    """Upload one immutable object per dataset and publish the workflow pointer.

    Returns None when storage is unavailable or there is nothing to store.
    A failure before the pointer is published leaves reconstruction ineligible.
    """
    file_service = get_file_service()
    if file_service is None or not workflow_id or not run_data:
        return None
    labels = {info.biosim_sim_run.id: simulator_label(info) for info in sim_run_info_list}
    order = {info.biosim_sim_run.id: index for index, info in enumerate(sim_run_info_list)}
    ordered = sorted(run_data, key=lambda item: (order.get(item.run_id, 10**9), item.dataset_name))

    async def upload(item: object) -> object:
        if not isinstance(item, RunData):
            raise TypeError("snapshot source must be run data")
        document = _snapshot_document(item, labels.get(item.run_id, ""))
        compressed, encoding, size, digest = encode_report(document)
        reference = await file_service.upload_bytes_immutable(
            compressed, f"verification-snapshots/data/{digest}.json.gz",
        )
        return NumericalInputSnapshot(
            run_id=item.run_id, dataset_name=item.dataset_name, var_names=list(item.var_names),
            shape=list(item.data.shape), simulator_version=labels.get(item.run_id, ""),
            content_sha256=digest, encoding=encoding, uncompressed_bytes=size, artifact_reference=reference,
        )

    uploaded = await _bounded(ordered, upload)
    snapshots = [item for item in uploaded if isinstance(item, NumericalInputSnapshot)]
    if len(snapshots) != len(ordered):
        raise SnapshotMismatch("snapshot capture did not retain every dataset")
    await publish_snapshot_manifest(
        workflow_id, [info.biosim_sim_run.id for info in sim_run_info_list], snapshots,
    )
    return snapshots


async def read_snapshot_manifest(workflow_id: str) -> tuple[list[str], list[NumericalInputSnapshot]] | None:
    """Return ``(ordered_run_ids, snapshots)`` for a workflow, or None when no pointer exists."""
    file_service = get_file_service()
    if file_service is None:
        return None
    raw = await file_service.get_file_contents(_pointer_path(workflow_id))
    if raw is None:
        return None
    pointer = json.loads(raw)
    if not isinstance(pointer, dict):
        raise SnapshotMismatch("snapshot pointer is not an object")
    reference = pointer.get("reference")
    encoding = pointer.get("encoding")
    digest = pointer.get("sha256")
    if not isinstance(reference, str) or not isinstance(encoding, str) or not isinstance(digest, str):
        raise SnapshotMismatch("snapshot pointer is incomplete")
    payload = await file_service.get_file_contents(reference)
    if payload is None:
        raise SnapshotMissing("snapshot manifest object is missing")
    if checksum(payload, encoding=encoding) != digest:
        raise SnapshotMismatch("snapshot manifest checksum mismatch")
    decoded = decode_report(payload, encoding=encoding)
    if not isinstance(decoded, dict) or decoded.get("workflow_id") != workflow_id:
        raise SnapshotMismatch("snapshot manifest identity mismatch")
    ordered = decoded.get("ordered_run_ids")
    raw_snapshots = decoded.get("snapshots")
    if not isinstance(ordered, list) or not isinstance(raw_snapshots, list):
        raise SnapshotMismatch("snapshot manifest is incomplete")
    snapshots = [NumericalInputSnapshot.model_validate(item) for item in raw_snapshots]
    return [str(run_id) for run_id in ordered], snapshots


async def load_verified_snapshot(snapshot: NumericalInputSnapshot) -> Hdf5DataValues:
    """Load one snapshot and refuse it unless the canonical checksum and identity match."""
    file_service = get_file_service()
    if file_service is None:
        raise SnapshotMissing("snapshot storage is unavailable")
    payload = await file_service.get_file_contents(snapshot.artifact_reference)
    if payload is None:
        raise SnapshotMissing("retained numerical input is missing")
    try:
        digest = checksum(payload, encoding=snapshot.encoding)
    except Exception as exc:
        raise SnapshotMismatch("numerical input checksum mismatch") from exc
    if digest != snapshot.content_sha256:
        raise SnapshotMismatch("numerical input checksum mismatch")
    decoded = decode_report(payload, encoding=snapshot.encoding)
    if not isinstance(decoded, dict):
        raise SnapshotMismatch("snapshot document is not an object")
    shape = decoded.get("shape")
    values = decoded.get("values")
    if (
        shape != snapshot.shape
        or decoded.get("run_id") != snapshot.run_id
        or decoded.get("dataset_name") != snapshot.dataset_name
    ):
        raise SnapshotMismatch("snapshot identity mismatch")
    if not isinstance(shape, list) or not isinstance(values, list):
        raise SnapshotMismatch("snapshot values are incomplete")
    try:
        parsed_shape = [int(item) for item in shape]
        parsed_values = [float(item) for item in values]
    except (TypeError, ValueError) as exc:
        raise SnapshotMismatch("snapshot values are not numeric") from exc
    return Hdf5DataValues(shape=parsed_shape, values=parsed_values)


async def load_verified_snapshots(
    snapshots: list[NumericalInputSnapshot],
) -> dict[str, dict[str, Hdf5DataValues]]:
    async def load(item: object) -> object:
        if not isinstance(item, NumericalInputSnapshot):
            raise TypeError("snapshot list contains a non-snapshot")
        return item, await load_verified_snapshot(item)

    loaded = await _bounded(snapshots, load)
    datasets: dict[str, dict[str, Hdf5DataValues]] = {}
    for item in loaded:
        if not isinstance(item, tuple) or len(item) != 2:
            raise SnapshotMismatch("snapshot load returned an unexpected result")
        snapshot, values = item
        if not isinstance(snapshot, NumericalInputSnapshot) or not isinstance(values, Hdf5DataValues):
            raise SnapshotMismatch("snapshot load returned an unexpected result")
        datasets.setdefault(snapshot.run_id, {})[snapshot.dataset_name] = values
    return datasets
