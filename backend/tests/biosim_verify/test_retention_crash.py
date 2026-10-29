"""Hard-kill actual worker processes around object/manifest/ledger/ack writes."""
import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from temporalio.client import Client
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]

from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import VerificationRecord, VerificationType, VerificationProvenance
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.common.temporal import pydantic_data_converter
from tests.biosim_verify.test_report_storage import _output
from tests.fixtures.retention_history.crash_worker import CrashPublicationWorkflow


@pytest_asyncio.fixture
async def crash_server() -> Any:
    with DockerContainer('temporalio/temporal:1.7.0').with_exposed_ports(7233).with_command(
        'server start-dev --ip 0.0.0.0') as server:
        address = f'localhost:{server.get_exposed_port(7233)}'
        for _ in range(100):
            try:
                client = await Client.connect(address, data_converter=pydantic_data_converter)
                break
            except RuntimeError:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError('test Temporal failed to start')
        yield address, client


@pytest.mark.integration_local
@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_upload', 'after_upload', 'before_manifest', 'after_manifest',
                                   'before_ledger', 'after_ledger', 'before_ack'])
async def test_sigkill_recovers_every_publication_boundary(
    stage: str, crash_server: Any, mongo_test_client: Any, mongodb_container: Any, tmp_path: Path,
) -> None:
    address, client = crash_server
    store = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / 'objects'))
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await store.ensure_indexes()
    await ledger.ensure_indexes()
    output = _output(owner=None).model_copy(update={'workflow_id': 'crash-' + uuid4().hex, 'workflow_run_id': None})
    await ledger.insert_verification(VerificationRecord(workflow_id=output.workflow_id, verify_type=VerificationType.RUNS,
                                                       created=datetime.now(UTC)))
    payload = PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
        verification_id=output.workflow_id, owner_sub=None, computed_at=datetime.now(UTC)))
    queue, marker = 'crash-' + uuid4().hex, tmp_path / 'boundary'
    command = [sys.executable, '-m', 'tests.fixtures.retention_history.crash_worker', '--server', address,
               '--mongo', mongodb_container.get_connection_url(), '--database', store._settings.mongodb_database,
               '--objects', str(tmp_path / 'objects'), '--queue', queue, '--marker', str(marker)]
    log = (tmp_path / 'worker.log').open('w')
    process = subprocess.Popen(command + ['--stage', stage], stdout=log, stderr=log)
    replacement = None
    try:
        handle = await client.start_workflow(CrashPublicationWorkflow.run, payload, id=output.workflow_id, task_queue=queue)
        for _ in range(300):
            if marker.exists():
                break
            assert process.poll() is None, (tmp_path / 'worker.log').read_text()
            await asyncio.sleep(.05)
        else:
            raise AssertionError('worker never reached crash boundary')
        process.kill()  # SIGKILL: no Python cleanup or graceful Temporal cancellation.
        process.wait(timeout=5)
        replacement = subprocess.Popen(command + ['--stage', ''], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recovered = await asyncio.wait_for(handle.result(), timeout=30)
        assert recovered.persistence_state == 'stored'
        saved = await store.get(output.workflow_id)
        assert saved is not None and saved.workflow_run_id == recovered.workflow_run_id
        assert await store._collection.count_documents({'workflow_id': output.workflow_id}) == 1
        row = await ledger.get_verification(output.workflow_id)
        assert row is not None and row.persistence_state == 'stored'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if replacement is not None:
            replacement.kill()
            replacement.wait(timeout=5)
        log.close()
        await store.delete_all()
        await ledger.delete_all_verifications()
