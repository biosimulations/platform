"""Real isolated Mongo/Temporal retention and replay, with no upstream simulations."""
import asyncio
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import docker  # type: ignore[import-untyped]
import yaml
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker, Replayer, UnsandboxedWorkflowRunner
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest
from temporalio.api.common.v1 import WorkflowExecution
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]

from biosim_server import dependencies as deps
from biosim_server.biosim_verify.activities import (
    generate_statistics_activity, generate_statistics_activity_v2, persist_verification_report_activity,
)
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import CompareSettings, VerificationRecord, VerificationType, VerifyWorkflowOutput
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow, RunsVerifyWorkflowInput
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflow, OmexVerifyWorkflowInput
from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityOutput
from biosim_server.biosim_runs import GetExistingBiosimSimulationRunActivityInput, BiosimSimulationRunStatus
from biosim_server.biosim_omex import OmexFile
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.common.temporal import pydantic_data_converter
from tests.fixtures.retention_history.legacy_runs import RunsVerifyWorkflow as LegacyRuns
from tests.fixtures.retention_history.legacy_omex import OmexVerifyWorkflow as LegacyOmex

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@activity.defn(name="get_existing_biosim_simulation_run_activity")
async def missing_run(arg: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
    return GetExistingBiosimSimulationRunActivityOutput(status=BiosimSimulationRunStatus.RUN_ID_NOT_FOUND)


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_retention_restart_replay_and_restore(
    mongo_test_client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    objects = FileServiceLocal(tmp_path / "objects")
    store = VerificationReportStore(mongo_test_client, objects)
    store._settings = store._settings.model_copy(update={"verification_report_inline_max_bytes": 1})
    await ledger.ensure_indexes()
    await store.ensure_indexes()
    monkeypatch.setattr(deps, 'global_verification_report_store', store)
    monkeypatch.setattr(deps, 'global_verification_database_service', ledger)
    monkeypatch.setattr(deps, 'get_mongo_client', lambda: mongo_test_client)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_mongo_client', lambda: mongo_test_client)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_file_service', lambda: objects)
    compose = yaml.safe_load((Path(__file__).resolve().parents[3] / 'compose.yaml').read_text())
    temporal_config = compose['services']['temporal']
    init_config = compose['services']['temporal-data-init']
    docker_client = docker.from_env()
    volume = docker_client.volumes.create(name='retention-' + uuid4().hex)
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1', 0))
        host_port = port_socket.getsockname()[1]
    server = DockerContainer(temporal_config['image']).with_bind_ports(7233, host_port).with_volume_mapping(
        volume.name, '/data', mode='rw').with_command(temporal_config['command'])

    async def connect() -> Client:
        for _ in range(100):
            try:
                return await Client.connect(f'localhost:{server.get_exposed_port(7233)}', data_converter=pydantic_data_converter)
            except RuntimeError:
                await asyncio.sleep(.1)
        raise AssertionError('isolated Temporal failed to start')

    try:
        docker_client.containers.run(init_config['image'], command=init_config['command'],
            volumes={volume.name: {'bind': '/data', 'mode': 'rw'}}, remove=True)
        server.start()
        client = await connect()
        settings = CompareSettings(user_description='retention test', include_outputs=True,
                                   rel_tol=.1, abs_tol_min=.01, abs_tol_scale=.01)
        inputs = [
            RunsVerifyWorkflowInput(biosimulations_run_ids=['missing'], compare_settings=settings),
            OmexVerifyWorkflowInput(omex_file=OmexFile(file_hash_md5='test', uploaded_filename='test.omex',
                bucket_name='test', omex_gcs_path='test.omex', file_size=0), requested_simulators=[],
                cache_buster='0', compare_settings=settings),
        ]
        histories = []
        saved_ids = []
        for legacy in [True, False]:
            workflows: list[Any] = [LegacyRuns, LegacyOmex] if legacy else [RunsVerifyWorkflow, OmexVerifyWorkflow]
            async with Worker(client, task_queue='retention-test', workflows=workflows,
                              activities=[missing_run, generate_statistics_activity, generate_statistics_activity_v2,
                                          persist_verification_report_activity], workflow_runner=UnsandboxedWorkflowRunner()):
                for cls, arg in zip(workflows, inputs):
                    wid = f'retention-{uuid4()}'
                    from datetime import datetime, UTC
                    await ledger.insert_verification(VerificationRecord(workflow_id=wid,
                        verify_type=VerificationType.RUNS if isinstance(arg, RunsVerifyWorkflowInput) else VerificationType.OMEX,
                        created=datetime.now(UTC), workflow_input=arg.model_dump(mode='json')))
                    handle = await client.start_workflow(cls.run, arg, id=wid, task_queue='retention-test')
                    output = await asyncio.wait_for(handle.result(), 30)
                    assert isinstance(output, VerifyWorkflowOutput)
                    histories.append(await handle.fetch_history())
                    if not legacy:
                        assert output.persistence_state == 'stored'
                        assert await store.get(wid) is not None  # No GET was required to save.
                        saved_ids.append(wid)
        for history in histories:
            await Replayer(workflows=[RunsVerifyWorkflow, OmexVerifyWorkflow],
                           data_converter=pydantic_data_converter, workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)
        # Wait for visibility indexing, then remove one ledger row to require true orphan recovery.
        for _ in range(50):
            visible = [item async for item in client.list_workflows(
                query='WorkflowType IN ("RunsVerifyWorkflow", "OmexVerifyWorkflow")', limit=10)]
            if len(visible) == 4:
                break
            await asyncio.sleep(.1)
        assert len(visible) == 4
        orphan_id = histories[0].workflow_id
        await ledger._collection.delete_one({"workflow_id": orphan_id})
        # Migrate pre-change results without any GET, across one-entry pages.
        from scripts.backfill_verification_reports import run as backfill
        monkeypatch.setattr(deps, 'global_temporal_client', client)
        checkpoint = tmp_path / 'migration.json'
        for page_index in range(12):
            state = await backfill(checkpoint=checkpoint, apply=True, inventory=True, limit=1)
            if state['complete']:
                break
        assert state['complete'] and not state['outcomes'].get('dependency_error')
        assert page_index >= 3  # four actual server visibility pages at page_size=1
        assert await ledger.get_verification(orphan_id) is not None
        assert await store._collection.count_documents({}) == 4
        # Persisted SQLite survives both a restart and a replacement container.
        server.get_wrapped_container().restart()
        client = await connect()
        await client.get_workflow_handle(saved_ids[0]).describe()
        server.stop()
        server.start()
        client = await connect()
        await client.get_workflow_handle(saved_ids[0]).describe()
        # Delete only this isolated test history, then read saved reports without Temporal or upstream.
        for wid in saved_ids:
            await client.workflow_service.delete_workflow_execution(DeleteWorkflowExecutionRequest(
                namespace='default', workflow_execution=WorkflowExecution(workflow_id=wid)))
        address = f'localhost:{server.get_exposed_port(7233)}'
        server.get_wrapped_container().stop()
        from biosim_server.api.main import get_verify_output
        monkeypatch.setattr('biosim_server.api.main.get_temporal_client', lambda: None)
        for wid in saved_ids:
            assert (await get_verify_output(wid, user=None)).persistence_state == 'stored'
        # Start the connection manager while the service is down, then recover in-place.
        # The already-created Docker container retains its host port across start/stop.
        from biosim_server.config import get_settings
        monkeypatch.setattr(get_settings(), 'temporal_service_url', address)
        monkeypatch.setattr(get_settings(), 'temporal_namespace', 'default')
        await deps.start_temporal_connection(retry_seconds=.05)
        assert deps.get_temporal_client() is None
        server.get_wrapped_container().start()
        for _ in range(150):
            if deps.get_temporal_client() is not None:
                break
            await asyncio.sleep(.1)
        reconnected = deps.get_temporal_client()
        assert reconnected is not None
        await reconnected.get_workflow_handle(histories[0].workflow_id).describe()
        await deps.stop_temporal_connection()
        server.get_wrapped_container().stop()
        # Exercise the operational bundle export, dry-run validation, and restore.
        from biosim_server.biosim_verify.report_backup import export_bundle, restore_bundle
        bundle = tmp_path / 'backup'
        await export_bundle(bundle, store, ledger)
        restored = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / 'restored-objects'))
        restore_db = mongo_test_client.get_database(store._settings.mongodb_database)
        restored._collection = restore_db.get_collection('restored_' + uuid4().hex)
        restored_ledger = VerificationDatabaseServiceMongo(mongo_test_client)
        restored_ledger._collection = restore_db.get_collection('restored_ledger_' + uuid4().hex)
        counts = await restore_bundle(bundle, restored, restored_ledger)
        assert counts['reports'] == 4 and await restored._collection.count_documents({}) == 0
        await restore_bundle(bundle, restored, restored_ledger, apply=True)
        await restore_bundle(bundle, restored, restored_ledger, apply=True)  # retry is safe
        for wid in saved_ids:
            original, recovered = await store.get(wid), await restored.get(wid)
            assert original is not None and recovered is not None
            assert original.model_dump() == recovered.model_dump()
        await restore_db.drop_collection(restored._collection.name)
        await restore_db.drop_collection(restored_ledger._collection.name)
    finally:
        try:
            server.stop()
        finally:
            volume.remove()
            docker_client.close()
        await store.delete_all()
        await ledger.delete_all_verifications()
