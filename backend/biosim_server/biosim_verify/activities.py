from importlib.metadata import version

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from biosim_server.biosim_runs import Hdf5DataValues, BiosimServiceRest
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.comparison import compare_loaded_runs
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput, GenerateStatisticsActivityResult,
    GenerateStatisticsArtifactReference, RunData, SimulationRunInfo, VerificationRecord,
    VerifyWorkflowOutput, VerificationProvenance, ReportPersistenceState,
)
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.report_storage import ReportCorrupt, ReportIntegrityConflict
from biosim_server.biosim_verify.snapshots import SnapshotMismatch, SnapshotMissing, capture_input_snapshots, read_snapshot_manifest
from biosim_server.dependencies import get_verification_database_service, get_verification_report_store, get_mongo_client
from biosim_server.dependencies import get_file_service
from biosim_server.config import get_settings
from biosim_server.version import __version__


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
    # Externalize before returning across Temporal's uncompressed payload boundary.
    output = await generate_statistics_activity(gen_stats_input)
    return await externalize_statistics(output)


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
        output = await store.hydrate_activity_reference(payload.output)
        output = output.model_copy(update={"workflow_results_reference": None})
        provenance = payload.provenance.model_copy(update={
            "comparator_version": __version__,
            "dependency_versions": {name: version(name) for name in ("numpy", "pydantic")},
            "actual_runs": output.workflow_results.sims_run_info if output.workflow_results else [],
        })
        try:
            manifest = await read_snapshot_manifest(payload.output.workflow_id)
        except (SnapshotMissing, SnapshotMismatch, OSError, ValueError, TypeError):
            activity.logger.error("verification_snapshot_manifest_unreadable")
            manifest = None
        if manifest is not None:
            ordered_run_ids, snapshots = manifest
            provenance = provenance.model_copy(update={
                "ordered_run_ids": ordered_run_ids,
                "input_snapshots": snapshots,
            })
        # Re-verification records its link on the ledger, not on the omex workflow
        # input, so an in-flight unpatched history does not gain a new command.
        try:
            row = await ledger.get_verification(payload.output.workflow_id)
        except Exception:
            row = None
        if isinstance(row, VerificationRecord):
            linked: dict[str, object] = {}
            if row.source_verification_id and not provenance.source_verification_id:
                linked["source_verification_id"] = row.source_verification_id
            if row.derivation_kind and not provenance.derivation_kind:
                linked["derivation_kind"] = row.derivation_kind
            if linked:
                provenance = provenance.model_copy(update=linked)
                output = output.model_copy(update=linked)
                if row.derivation_kind == "reconstruction":
                    output = output.model_copy(update={"calculation": "new_calculation"})
        envelope = await store.save(output, provenance)
        await ledger.record_report_persisted(payload.output.workflow_id, envelope.report_id, payload.output.workflow_status)
        return output.model_copy(update={
            "persistence_state": ReportPersistenceState.STORED, "report_available": True, "report_id": envelope.report_id,
        })
    except Exception as exc:
        blocked = isinstance(exc, (ValueError, ReportIntegrityConflict, ReportCorrupt))
        activity.logger.error("verification_persistence_failure blocked=%s", blocked)
        try:
            await ledger.mark_persistence_issue(payload.output.workflow_id, blocked)
        except Exception:
            activity.logger.error("verification_persistence_bookkeeping_failure")
        if blocked:
            raise ApplicationError("Report integrity/encoding failure", non_retryable=True) from exc
        raise


async def externalize_statistics(output: GenerateStatisticsActivityOutput) -> GenerateStatisticsActivityResult:
    """Store a statistics payload before it crosses Temporal's result limit."""
    encoded, encoding, size, digest = encode_report(output.model_dump(mode="json"))
    if size > get_settings().verification_report_max_decompressed_bytes:
        raise ApplicationError("Statistics exceed archival size limit", non_retryable=True)
    file_service = get_file_service()
    if file_service is None:
        raise RuntimeError("statistics artifact storage is unavailable")
    reference = await file_service.upload_bytes_immutable(encoded, f"verification-activity/{digest}.json.gz")
    return GenerateStatisticsActivityResult(reference=GenerateStatisticsArtifactReference(
        artifact_reference=reference, content_sha256=digest, encoding=encoding, uncompressed_bytes=size,
    ))
