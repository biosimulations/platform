import asyncio
from datetime import timedelta
from importlib.metadata import version

from pydantic import BaseModel
from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from biosim_server.biosim_runs import Hdf5DataValues, BiosimServiceRest
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.comparison import compare_loaded_runs
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput, GenerateStatisticsActivityResult,
    DerivationKind, GenerateStatisticsArtifactReference, RunData, SimulationRunInfo, VerificationRecord,
    VerifyWorkflowOutput, VerificationProvenance, ReportPersistenceState,
)
from biosim_server.biosim_verify.object_names import (
    ACTIVITY_PREFIX,
    holds_content,
    snapshot_index_path,
    statistics_artifact_path,
)
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.report_storage import (
    ReportArtifactMissing, ReportCorrupt, ReportIntegrityConflict, VerificationReportStore,
)
from biosim_server.biosim_verify.snapshots import SnapshotMismatch, SnapshotMissing, capture_input_snapshots, read_snapshot_manifest
from biosim_server.dependencies import get_verification_database_service, get_verification_report_store, get_mongo_client
from biosim_server.dependencies import get_file_service
from biosim_server.common.storage.content_addressed import publish_content_addressed
from biosim_server.config import get_settings
from biosim_server.version import __version__


# The activity raises this for a failure that will not get better by retrying.
# It lives here, next to the raise, so the activity and the workflow policy
# cannot drift apart.
PERMANENT_ERROR_TYPE = "ReportBlocked"

# A failure that retrying cannot fix.  ReportArtifactMissing is a subclass of
# ReportUnavailable, so it must be listed explicitly: an unconfigured or
# unreachable store stays retryable, but a missing object never appears later.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    ValueError, ReportIntegrityConflict, ReportCorrupt, ReportArtifactMissing,
)

# The statistics activity has two kinds of failure, and they are retried
# differently.  Computing (fetching the runs' data from biosimulations.org and
# comparing it) keeps the budget it had before the activity wrote to object
# storage.  A failed write of an oversized result is retried in that same
# attempt, with the persist activity's backoff: the bytes stay in memory, so
# the retry does not download the datasets again and Temporal's attempt count
# does not move.  A storage outage therefore does not spend the compute budget
# and does not turn a computed comparison into a FAILED verification (I-1).
# The server no longer caps attempts; the activity caps computing itself, and
# the whole activity, crashes and timeouts included, is bounded by a day.
# The attempt holds its worker slot while it waits on storage, and heartbeats
# so a dead worker is retried.  That retry recomputes once.

#: Attempts the statistics activity gets when computing fails.
STATISTICS_COMPUTE_MAX_ATTEMPTS = 100
#: One compute attempt. A hung download ends here and counts as a compute failure.
STATISTICS_COMPUTE_ATTEMPT_TIMEOUT = timedelta(minutes=10)
#: The longest wait between writes while object storage refuses the statistics.
STATISTICS_STORAGE_MAX_RETRY_DELAY = timedelta(minutes=5)
STATISTICS_COMPUTE_FAILED_TYPE = "StatisticsComputationFailed"
#: Logged on each storage retry. The persistence alert matches this text.
STATISTICS_STORAGE_UNAVAILABLE_EVENT = "verification_statistics_storage_unavailable"
#: The workflow's policy for the statistics activity: fast retries for a
#: compute failure, as before, and no server-side attempt cap.
STATISTICS_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=10), maximum_attempts=0)
#: The bound on the statistics activity, all attempts and waits included.
STATISTICS_SCHEDULE_TO_CLOSE = timedelta(days=1)
#: Longer than one compute attempt. Computing heartbeats at its start and end;
#: a storage wait heartbeats throughout. A slow comparison is not failed for a
#: missed heartbeat, and a dead worker is still noticed.
STATISTICS_HEARTBEAT_TIMEOUT = timedelta(minutes=15)
STATISTICS_HEARTBEAT_INTERVAL = timedelta(seconds=30)


def statistics_storage_retry_delay(attempt: int) -> timedelta:
    """Wait before retrying a statistics write: 1s, 2s, 4s, ... up to five minutes."""
    return min(STATISTICS_STORAGE_MAX_RETRY_DELAY, timedelta(seconds=2 ** min(max(attempt - 1, 0), 16)))


class GenerateStatisticsActivityInput(BaseModel):
    sim_run_info_list: list[SimulationRunInfo]
    compare_settings: CompareSettings
    workflow_id: str = ""


class PersistVerificationReportActivityInput(BaseModel):
    output: VerifyWorkflowOutput
    provenance: VerificationProvenance


@activity.defn
async def generate_statistics_activity(gen_stats_input: GenerateStatisticsActivityInput) -> GenerateStatisticsActivityOutput:
    try:
        # Gather the data from each run for each dataset
        num_runs = len(gen_stats_input.sim_run_info_list)
        datasets: dict[str, dict[str, Hdf5DataValues]] = {}
        sims_run_data: list[RunData] = []

        biosim_service = BiosimServiceRest()
        if biosim_service is None:
            raise Exception("Biosim service is not initialized")

        for run_index_i in range(num_runs):
            sim_run_info_i = gen_stats_input.sim_run_info_list[run_index_i]
            run_id_i = sim_run_info_i.biosim_sim_run.id
            datasets[run_id_i] = {}
            dataset_names_i = [dataset.name for group in sim_run_info_i.hdf5_file.groups for dataset in group.datasets]
            for dataset_name in dataset_names_i:
                data: Hdf5DataValues = await biosim_service.get_hdf5_data(simulation_run_id=run_id_i,
                                                                          dataset_name=dataset_name)
                datasets[run_id_i][dataset_name] = data
                var_names = sim_run_info_i.hdf5_file.datasets[dataset_name].sedml_labels
                run_data: RunData = RunData(run_id=run_id_i, dataset_name=dataset_name, var_names=var_names, data=data)
                sims_run_data.append(run_data)

        gen_stats_output = compare_loaded_runs(
            gen_stats_input.sim_run_info_list, datasets, gen_stats_input.compare_settings,
        )
        if gen_stats_input.compare_settings.include_outputs:
            gen_stats_output.sim_run_data = sims_run_data
        try:
            workflow_id = activity.info().workflow_id or ""
            await capture_input_snapshots(workflow_id, gen_stats_input.sim_run_info_list, sims_run_data)
        except Exception:
            # The scientific comparison already succeeded. Missing snapshots make
            # reconstruction ineligible; they do not discard this report.
            activity.logger.error("verification_snapshot_capture_failed")
        return gen_stats_output
    except Exception as e:
        activity.logger.exception(f"Error in generate_statistics_activity: {e}", exc_info=e)
        raise e


@activity.defn
async def generate_statistics_activity_v2(gen_stats_input: GenerateStatisticsActivityInput) -> GenerateStatisticsActivityResult:
    # Small statistics stay inline: they then cost no object write, no read, and
    # no transient artifact, and history recovery can still read them when
    # object storage is unavailable.  Only a payload that would approach
    # Temporal's limit is externalized.
    attempt = activity.info().attempt
    # Starts the heartbeat timer. Computing then has until
    # STATISTICS_HEARTBEAT_TIMEOUT, which is longer than its own timeout.
    activity.heartbeat()
    try:
        async with asyncio.timeout(STATISTICS_COMPUTE_ATTEMPT_TIMEOUT.total_seconds()):
            output = await generate_statistics_activity(gen_stats_input)
    except Exception as exc:
        if attempt >= STATISTICS_COMPUTE_MAX_ATTEMPTS:
            raise ApplicationError(
                "Statistics computation failed", type=STATISTICS_COMPUTE_FAILED_TYPE, non_retryable=True,
            ) from exc
        raise
    return await _externalize_until_stored(output, activity.info().workflow_id or "")


async def prepare_report(
    payload: PersistVerificationReportActivityInput,
    *,
    store: VerificationReportStore,
    row: VerificationRecord | None,
) -> tuple[VerifyWorkflowOutput, VerificationProvenance]:
    """The report content and provenance stored for one persist input.

    The persist activity stores exactly this.  A writer that recovers the
    persist input from workflow history calls it too, so the recovered report
    is the one the workflow would have stored, byte for byte.  *row* is the
    verification's ledger row, which carries a re-verification's link.
    """
    output = await store.hydrate_activity_reference(payload.output)
    output = output.model_copy(update={"workflow_results_reference": None})
    provenance = payload.provenance.model_copy(update={
        "comparator_version": __version__,
        "dependency_versions": {name: version(name) for name in ("numpy", "pydantic")},
        "actual_runs": output.workflow_results.sims_run_info if output.workflow_results else [],
    })
    try:
        index = await read_snapshot_manifest(payload.output.workflow_id)
    except (SnapshotMissing, SnapshotMismatch, ValueError, TypeError):
        # Only a missing or corrupt manifest goes without snapshots.  An I/O
        # failure (a timeout or a dropped connection, both OSErrors) propagates
        # and is retried: the report and its provenance are stored once, so a
        # read that merely failed would make reconstruction impossible for good.
        activity.logger.error("verification_snapshot_manifest_unreadable")
        index = None
    if index is not None and index.skipped:
        provenance = provenance.model_copy(update={"input_snapshots_skipped": index.skipped})
    elif index is not None:
        provenance = provenance.model_copy(update={
            "ordered_run_ids": index.ordered_run_ids,
            "input_snapshots": index.snapshots,
            "snapshot_manifest_reference": index.manifest_reference,
        })
    return link_derived(output, provenance, row)


def link_derived(
    output: VerifyWorkflowOutput, provenance: VerificationProvenance, row: VerificationRecord | None,
) -> tuple[VerifyWorkflowOutput, VerificationProvenance]:
    """Carry a derived verification's link from its ledger row into the output and provenance."""
    if isinstance(row, VerificationRecord):
        linked: dict[str, object] = {}
        if row.source_verification_id and not provenance.source_verification_id:
            linked["source_verification_id"] = row.source_verification_id
        if row.derivation_kind and not provenance.derivation_kind:
            linked["derivation_kind"] = row.derivation_kind
        if linked:
            provenance = provenance.model_copy(update=linked)
            output = output.model_copy(update=linked)
            if row.derivation_kind == DerivationKind.RECONSTRUCTION:
                output = output.model_copy(update={"calculation": "new_calculation"})
    return output, provenance


async def remove_transient_objects(payload: PersistVerificationReportActivityInput) -> None:
    """Best-effort removal of the workflow's transient objects once its report is durable.

    Only the statistics artifact and the snapshot index pointer are removed;
    neither is referenced by a stored report.  A failed delete is logged and
    left to the storage lifecycle rule, never raised.
    """
    file_service = get_file_service()
    if file_service is None:
        return
    paths = [snapshot_index_path(payload.output.workflow_id)]
    statistics = payload.output.workflow_results_reference
    if statistics is not None and statistics.artifact_reference.startswith(ACTIVITY_PREFIX):
        paths.append(statistics.artifact_reference)
    for path in paths:
        try:
            await file_service.delete_file(path)
        except Exception:
            activity.logger.warning("verification_transient_cleanup_failed path=%s", path)


@activity.defn
async def persist_verification_report_activity(
    payload: PersistVerificationReportActivityInput,
) -> VerifyWorkflowOutput:
    store = get_verification_report_store()
    ledger = get_verification_database_service()
    mongo = get_mongo_client()
    if store is None or ledger is None or mongo is None:
        raise RuntimeError("verification report persistence is unavailable")
    try:
        # Re-verification records its link on the ledger, not on the omex workflow
        # input, so an in-flight unpatched history does not gain a new command.
        try:
            row = await ledger.get_verification(payload.output.workflow_id)
        except Exception as exc:
            # A ledger read failure is a dependency outage, not "no link":
            # swallowing it would store a report with its re-verification link
            # silently dropped.  Retry instead.
            raise RuntimeError("ledger read failed") from exc
        envelope = await store.find_envelope(payload.output.workflow_id)
        if envelope is not None and envelope.workflow_run_id == payload.output.workflow_run_id \
                and envelope.owner_sub == payload.output.owner_sub:
            # An earlier attempt stored this report and may have removed the
            # transient objects prepare_report reads.  The stored report is
            # authoritative, so this attempt only acknowledges it.
            output, _ = link_derived(payload.output, payload.provenance, row)
        else:
            output, provenance = await prepare_report(payload, store=store, row=row)
            envelope = await store.save(output, provenance)
        await ledger.record_report_persisted(payload.output.workflow_id, envelope.report_id, payload.output.workflow_status)
        await remove_transient_objects(payload)
        # The stored report is authoritative.  Once it is durable, statistics
        # leave the activity result, the workflow result and every query: the
        # payload stays bounded however large the comparison was.
        return output.model_copy(update={
            "persistence_state": ReportPersistenceState.STORED, "report_available": True,
            "report_id": envelope.report_id,
            "workflow_results": None, "workflow_results_reference": None,
        })
    except Exception as exc:
        blocked = isinstance(exc, PERMANENT_ERRORS)
        activity.logger.error("verification_persistence_failure blocked=%s", blocked)
        try:
            await ledger.mark_persistence_issue(payload.output.workflow_id, blocked)
        except Exception:
            activity.logger.error("verification_persistence_bookkeeping_failure")
        if blocked:
            raise ApplicationError(
                "Report integrity/encoding failure", type=PERMANENT_ERROR_TYPE, non_retryable=True,
            ) from exc
        raise


async def externalize_statistics(output: GenerateStatisticsActivityOutput,
                                 workflow_id: str) -> GenerateStatisticsActivityResult:
    """Return statistics inline when they are small, externalized when they are not.

    Inline keeps the common case free of object storage and recoverable from
    history alone.  Above ``verification_activity_inline_max_bytes`` the payload
    is written to object storage and only a reference crosses Temporal.  The
    object's name is its content's digest under the workflow, so a retried
    activity reuses it instead of writing another.
    """
    encoded, encoding, size, digest = encode_report(output.model_dump(mode="json"))
    if size <= get_settings().verification_activity_inline_max_bytes:
        return GenerateStatisticsActivityResult(output=output)
    if size > get_settings().verification_report_max_decompressed_bytes:
        raise ApplicationError("Statistics exceed archival size limit", non_retryable=True)
    file_service = get_file_service()
    if file_service is None:
        raise RuntimeError("statistics artifact storage is unavailable")
    reference = await publish_content_addressed(
        file_service, encoded, statistics_artifact_path(workflow_id, digest), holds_content(digest, encoding),
    )
    return GenerateStatisticsActivityResult(reference=GenerateStatisticsArtifactReference(
        artifact_reference=reference, content_sha256=digest, encoding=encoding, uncompressed_bytes=size,
    ))


async def _wait_for_storage(delay: timedelta) -> None:
    """Sleep *delay*, heartbeating so a dead worker is noticed during the wait."""
    remaining = delay.total_seconds()
    interval = STATISTICS_HEARTBEAT_INTERVAL.total_seconds()
    while remaining > 0:
        pause = min(interval, remaining)
        await asyncio.sleep(pause)
        activity.heartbeat()
        remaining -= pause


async def _externalize_until_stored(
    output: GenerateStatisticsActivityOutput, workflow_id: str,
) -> GenerateStatisticsActivityResult:
    """Write externalized statistics, retrying storage inside this attempt.

    The computed bytes stay in memory. A storage failure does not return to
    Temporal, so the next try does not download the datasets again and does
    not spend a compute attempt. The archival size limit still propagates.
    The activity's schedule-to-close timeout bounds the wait.
    """
    storage_attempt = 1
    while True:
        activity.heartbeat()
        try:
            return await externalize_statistics(output, workflow_id)
        except ApplicationError:
            raise
        except Exception:
            activity.logger.warning(STATISTICS_STORAGE_UNAVAILABLE_EVENT)
            await _wait_for_storage(statistics_storage_retry_delay(storage_attempt))
            storage_attempt += 1
