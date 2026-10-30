"""A computed verification is never degraded by a persistence failure (WP-3).

Real workflows run on the local Temporal dev server against real Mongo. Faults
are injected only into report writes made by the persist activity, so the
recovery writers (GET and the reconciler) see healthy storage.
"""
import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from biosim_server import dependencies as deps
from biosim_server.api.main import app
from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs.activities import (
    GetExistingBiosimSimulationRunActivityInput,
    GetExistingBiosimSimulationRunActivityOutput,
)
from biosim_server.biosim_runs.models import (
    BiosimSimulationRun,
    BiosimSimulationRunStatus,
    BiosimulatorVersion,
    BiosimulatorWorkflowRun,
    Hdf5DataValues,
)
from biosim_server.biosim_verify import workflow_persistence
from biosim_server.biosim_verify.activities import PERMANENT_ERROR_TYPE
from biosim_server.biosim_verify.activities import (
    PersistVerificationReportActivityInput,
    generate_statistics_activity,
    generate_statistics_activity_v2,
    persist_verification_report_activity,
)
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.history import terminal_failure_output
from biosim_server.biosim_verify.models import (
    CompareSettings,
    GenerateStatisticsArtifactReference,
    ReportPersistenceState,
    VerificationProvenance,
    VerificationRecord,
    VerificationType,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.report_recovery import process_record
from biosim_server.biosim_verify.report_storage import (
    ReportArtifactMissing,
    ReportCorrupt,
    ReportIntegrityConflict,
    ReportUnavailable,
    VerificationReportStore,
)
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow, RunsVerifyWorkflowInput
from biosim_server.common.auth import get_optional_user
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.common.temporal import pydantic_data_converter
from biosim_server.config import get_settings
from tests.biosim_verify.test_compatibility import _file

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

SETTINGS = CompareSettings(user_description="persistence failures", include_outputs=False,
                           rel_tol=1e-4, abs_tol_min=1e-3, abs_tol_scale=1e-5)
SIMULATOR = BiosimulatorVersion(id="copasi", name="copasi", version="4.45", image_url="image",
                                image_digest="digest", created="2026-01-01", updated="2026-01-01")
# Fields that describe where a report lives, not what it says. The store excludes them from the hash.
PERSISTENCE_FIELDS = {"persistence_state", "report_id", "report_available"}


@activity.defn(name="get_existing_biosim_simulation_run_activity")
async def succeeded_run(arg: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
    run = BiosimSimulationRun(id=arg.biosim_run_id, name="test", simulator_version=SIMULATOR,
                              status=BiosimSimulationRunStatus.SUCCEEDED)
    archive = OmexFile(file_hash_md5="hash", uploaded_filename="m.omex", bucket_name="test",
                       omex_gcs_path="m.omex", file_size=1)
    return GetExistingBiosimSimulationRunActivityOutput(status=run.status, biosim_workflow_run=BiosimulatorWorkflowRun(
        workflow_id=arg.workflow_id, file_hash_md5="hash", image_digest="digest", cache_buster="0",
        omex_file=archive, simulator_version=SIMULATOR, biosim_run=run,
        hdf5_file=_file(arg.biosim_run_id, {"shared": ["t", "x"]}),
    ))


class SaveFaults:
    """Fails report writes made by the persist activity; writes outside an activity pass through."""

    def __init__(self, store: VerificationReportStore) -> None:
        self._save = store.save
        self.error: Exception | None = None
        self.remaining: int | None = None
        # (output, provenance) of every activity write attempt: what the activity would have stored.
        self.attempts: list[tuple[VerifyWorkflowOutput, VerificationProvenance]] = []
        self.conflicts = 0

    def fail(self, error: Exception, times: int | None = None) -> None:
        """Fail the next *times* activity writes, or every one until :meth:`clear` when None."""
        self.error, self.remaining = error, times

    def clear(self) -> None:
        self.error = None

    async def save(self, output: VerifyWorkflowOutput, provenance: VerificationProvenance) -> Any:
        if activity.in_activity():
            self.attempts.append((output, provenance))
            if self.error is not None and (self.remaining is None or self.remaining > 0):
                if self.remaining is not None:
                    self.remaining -= 1
                raise self.error
        try:
            return await self._save(output, provenance)
        except ReportIntegrityConflict:
            self.conflicts += 1
            raise

    async def wait_for_attempts(self, count: int, timeout: float = 30) -> None:
        async with asyncio.timeout(timeout):
            while len(self.attempts) < count:
                await asyncio.sleep(.02)


@dataclasses.dataclass
class World:
    client: Client
    queue: str
    store: VerificationReportStore
    ledger: VerificationDatabaseServiceMongo
    objects: FileServiceLocal
    faults: SaveFaults

    async def start(self, *, queue: str | None = None, link: bool = False) -> WorkflowHandle[Any, VerifyWorkflowOutput]:
        """Insert the ledger row the API would insert, then start a runs verification."""
        workflow_id = "persistence-" + uuid4().hex
        verify_input = RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], compare_settings=SETTINGS)
        await self.ledger.insert_verification(VerificationRecord(
            workflow_id=workflow_id, verify_type=VerificationType.RUNS, created=datetime.now(UTC),
            workflow_input=verify_input.model_dump(mode="json"),
            source_verification_id="source-verification" if link else None,
            derivation_kind="reverification" if link else None,
        ))
        return await self.client.start_workflow(RunsVerifyWorkflow.run, verify_input, id=workflow_id,
                                                task_queue=queue or self.queue)

    async def reconcile(self, workflow_id: str) -> str:
        record = await self.ledger.get_verification(workflow_id)
        assert record is not None
        return await process_record(record=record, ledger=self.ledger, store=self.store,
                                    temporal=self.client, apply=True)

    async def digest(self, workflow_id: str) -> str:
        doc = await self.store._collection.find_one({"workflow_id": workflow_id})
        assert doc is not None, "no report stored"
        return str(doc["content_sha256"])

    async def forget_report(self, workflow_id: str) -> None:
        await self.store._collection.delete_many({"workflow_id": workflow_id})


async def get_verify(workflow_id: str) -> httpx.Response:
    app.dependency_overrides[get_optional_user] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/verify/{workflow_id}")
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


def content(output: VerifyWorkflowOutput) -> dict[str, Any]:
    return output.model_dump(mode="json", exclude=PERSISTENCE_FIELDS)


def as_stored(moment: datetime) -> datetime:
    """A datetime as Mongo returns it: UTC, millisecond precision."""
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=moment.microsecond // 1000 * 1000)


@pytest_asyncio.fixture
async def world(
    temporal_client: Client, mongo_test_client: Any, verification_database_service_mongo: VerificationDatabaseServiceMongo,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[World]:
    objects = FileServiceLocal(tmp_path / "objects")
    store = VerificationReportStore(mongo_test_client, objects)
    await store.ensure_indexes()
    faults = SaveFaults(store)
    monkeypatch.setattr(store, "save", faults.save)
    monkeypatch.setattr(deps, "global_verification_report_store", store)
    monkeypatch.setattr(deps, "global_file_service", objects)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_mongo_client", lambda: mongo_test_client)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.BiosimServiceRest.get_hdf5_data",
                        AsyncMock(return_value=Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])))
    # Shorten only the intervals: the attempt limit under test stays the production one.
    monkeypatch.setattr(workflow_persistence, "PERSIST_RETRY", dataclasses.replace(
        workflow_persistence.PERSIST_RETRY,
        initial_interval=timedelta(milliseconds=10), maximum_interval=timedelta(milliseconds=50)))
    queue = "persistence-" + uuid4().hex
    async with Worker(temporal_client, task_queue=queue, workflows=[RunsVerifyWorkflow],
                      activities=[succeeded_run, generate_statistics_activity, generate_statistics_activity_v2,
                                  persist_verification_report_activity],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        yield World(temporal_client, queue, store, verification_database_service_mongo, objects, faults)
    await store.delete_all()


# ---------------------------------------------------------------------------
# The activity decides what is retryable (WP-3b, S-3)
# ---------------------------------------------------------------------------

def _payload() -> PersistVerificationReportActivityInput:
    output = VerifyWorkflowOutput(workflow_id="wf", workflow_run_id="run", compare_settings=SETTINGS,
                                  workflow_status=VerifyWorkflowStatus.COMPLETED, timestamp="2026-10-30T00:00:00+00:00")
    return PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
        verification_id="wf", workflow_run_id="run", owner_sub=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("error, permanent", [
    (ValueError("report exceeds archival size limit"), True),
    (ReportIntegrityConflict("conflicting report"), True),
    (ReportCorrupt("statistics artifact checksum mismatch"), True),
    (ReportArtifactMissing("statistics artifact is unavailable"), True),
    (ReportUnavailable("statistics artifact storage is unavailable"), False),
    (RuntimeError("mongo unavailable"), False),
    (ConnectionError("storage reset"), False),
], ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value))
async def test_persist_activity_blocks_only_permanent_failures(error: Exception, permanent: bool) -> None:
    store, ledger = AsyncMock(), AsyncMock()
    ledger.get_verification.return_value = None
    store.hydrate_activity_reference.side_effect = error
    with patch("biosim_server.biosim_verify.activities.get_verification_report_store", return_value=store), \
         patch("biosim_server.biosim_verify.activities.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.biosim_verify.activities.get_mongo_client", return_value=MagicMock()), \
         pytest.raises(Exception) as raised:
        await ActivityEnvironment().run(persist_verification_report_activity, _payload())
    if permanent:
        assert isinstance(raised.value, ApplicationError)
        assert raised.value.type == PERMANENT_ERROR_TYPE and raised.value.non_retryable
    else:
        assert raised.value is error  # re-raised as-is: Temporal retries it without a cap
    ledger.mark_persistence_issue.assert_awaited_once_with("wf", permanent)


@pytest.mark.asyncio
async def test_ledger_read_failure_is_retried_not_swallowed() -> None:
    store, ledger = AsyncMock(), AsyncMock()
    ledger.get_verification.side_effect = RuntimeError("ledger unavailable")
    with patch("biosim_server.biosim_verify.activities.get_verification_report_store", return_value=store), \
         patch("biosim_server.biosim_verify.activities.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.biosim_verify.activities.get_mongo_client", return_value=MagicMock()), \
         pytest.raises(RuntimeError, match="ledger read failed"):
        await ActivityEnvironment().run(persist_verification_report_activity, _payload())
    store.save.assert_not_awaited()
    ledger.mark_persistence_issue.assert_awaited_once_with("wf", False)


@pytest.mark.asyncio
async def test_only_a_missing_object_is_a_permanent_artifact_failure(mongo_test_client: Any, tmp_path: Path) -> None:
    reference = GenerateStatisticsArtifactReference(artifact_reference="verification-activity/missing.json.gz",
                                                    content_sha256="0" * 64, encoding="x", uncompressed_bytes=1)
    output = _payload().output.model_copy(update={"workflow_results_reference": reference})
    with pytest.raises(ReportUnavailable) as unconfigured:
        await VerificationReportStore(mongo_test_client).hydrate_activity_reference(output)
    assert not isinstance(unconfigured.value, ReportArtifactMissing)
    with pytest.raises(ReportArtifactMissing):
        await VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path)).hydrate_activity_reference(output)


# ---------------------------------------------------------------------------
# The workflow retries transient failures without a cap (WP-3a)
# ---------------------------------------------------------------------------

@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_transient_failures_are_retried_until_the_report_is_stored(world: World) -> None:
    world.faults.fail(RuntimeError("mongo unavailable"))
    handle = await world.start()
    # More attempts than the old 20-attempt policy allowed.
    await world.faults.wait_for_attempts(25)
    progress = await handle.query("get_output", result_type=VerifyWorkflowOutput)
    assert progress.workflow_status == VerifyWorkflowStatus.IN_PROGRESS
    assert progress.persistence_state == ReportPersistenceState.PENDING  # the UI shows "Saving report"
    assert progress.workflow_results is None
    world.faults.clear()

    result = await asyncio.wait_for(handle.result(), 30)
    assert result.persistence_state == ReportPersistenceState.STORED
    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    saved = await world.store.get(handle.id)
    assert saved is not None and saved.workflow_status == VerifyWorkflowStatus.COMPLETED
    attempted, _ = world.faults.attempts[0]
    assert saved.workflow_results is not None and saved.workflow_results.comparison_statistics
    assert content(saved) == content(attempted)
    row = await world.ledger.get_verification(handle.id)
    assert row is not None and row.persistence_state == ReportPersistenceState.STORED


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_a_permanent_failure_completes_blocked_with_the_computed_output(world: World) -> None:
    world.faults.fail(ValueError("verification report exceeds archival size limit"))
    handle = await world.start()
    result = await asyncio.wait_for(handle.result(), 30)

    assert (await handle.describe()).status == WorkflowExecutionStatus.COMPLETED
    assert result.persistence_state == ReportPersistenceState.BLOCKED
    assert result.report_available is False
    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED  # never FAILED
    assert result.workflow_results is not None and result.workflow_results.comparison_statistics
    assert len(world.faults.attempts) == 1  # not retried
    assert await world.store.get(handle.id) is None
    row = await world.ledger.get_verification(handle.id)
    assert row is not None and row.persistence_state == ReportPersistenceState.BLOCKED


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_a_failed_ledger_read_is_retried_and_keeps_the_link(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    read = world.ledger.get_verification
    reads = 0

    async def flaky_read(workflow_id: str) -> VerificationRecord | None:
        nonlocal reads
        if activity.in_activity():
            reads += 1
            if reads == 1:
                raise RuntimeError("ledger unavailable")
        return await read(workflow_id)

    issues = AsyncMock(wraps=world.ledger.mark_persistence_issue)
    monkeypatch.setattr(world.ledger, "get_verification", flaky_read)
    monkeypatch.setattr(world.ledger, "mark_persistence_issue", issues)
    handle = await world.start(link=True)
    result = await asyncio.wait_for(handle.result(), 30)

    assert reads == 2
    assert result.persistence_state == ReportPersistenceState.STORED
    saved = await world.store.get(handle.id)
    assert saved is not None
    assert saved.source_verification_id == "source-verification"
    assert saved.derivation_kind == "reverification"
    assert [call.args[1] for call in issues.await_args_list] == [False]  # never blocked


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_histories_recorded_with_the_capped_policy_replay(world: World) -> None:
    """In-flight and finished workflows from the 20-attempt build replay under the current code."""
    from tests.fixtures.retention_history.capped_retry_runs import RunsVerifyWorkflow as CappedRetryRuns
    replayer = Replayer(workflows=[RunsVerifyWorkflow], data_converter=pydantic_data_converter,
                        workflow_runner=UnsandboxedWorkflowRunner())
    queue = "capped-" + uuid4().hex
    world.faults.fail(RuntimeError("mongo unavailable"))
    async with Worker(world.client, task_queue=queue, workflows=[CappedRetryRuns],
                      activities=[succeeded_run, generate_statistics_activity, generate_statistics_activity_v2,
                                  persist_verification_report_activity],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        handle = await world.start(queue=queue)
        await world.faults.wait_for_attempts(2)
        # A worker restart mid-retry replays this open history under the new build.
        await replayer.replay_workflow(await handle.fetch_history())
        world.faults.clear()
        result = await asyncio.wait_for(handle.result(), 30)
    assert result.persistence_state == ReportPersistenceState.STORED
    await replayer.replay_workflow(await handle.fetch_history())


# ---------------------------------------------------------------------------
# Recovery stores the computed output, and writers converge (WP-3c, WP-3d, WP-3e, WP-3f)
# ---------------------------------------------------------------------------

@pytest.mark.integration_local
@pytest.mark.asyncio
@pytest.mark.parametrize("statistics", ["inline", "externalized"])
async def test_terminated_while_persisting_both_writers_store_the_computed_output(
    world: World, monkeypatch: pytest.MonkeyPatch, statistics: str,
) -> None:
    if statistics == "externalized":
        monkeypatch.setattr(get_settings(), "verification_activity_inline_max_bytes", 1)
    world.faults.fail(RuntimeError("storage unavailable"))
    handle = await world.start(link=True)
    await world.faults.wait_for_attempts(2)
    await handle.terminate("operator stopped a stuck verification")
    attempted, attempted_provenance = world.faults.attempts[0]
    workflow_id = handle.id

    response = await get_verify(workflow_id)
    assert response.status_code == 200, response.text
    assert response.json()["workflow_status"] == "COMPLETED"
    by_get = await world.store.get(workflow_id)
    assert by_get is not None and by_get.workflow_results is not None
    assert content(by_get) == content(attempted)  # exactly what the activity would have stored
    provenance = await world.store.get_provenance(workflow_id)
    assert provenance is not None and provenance.origin == "recovered_from_history"
    for field in ("input_snapshots", "ordered_run_ids", "actual_runs", "source_verification_id",
                  "derivation_kind", "workflow_input"):
        assert getattr(provenance, field) == getattr(attempted_provenance, field), field
    assert provenance.input_snapshots  # the snapshot manifest was carried over, not dropped
    assert attempted_provenance.computed_at is not None and provenance.computed_at is not None
    assert as_stored(provenance.computed_at) == as_stored(attempted_provenance.computed_at)
    if statistics == "externalized":
        assert attempted.workflow_results is not None  # hydrated from the transient object
    get_digest = await world.digest(workflow_id)

    await world.forget_report(workflow_id)
    assert await world.reconcile(workflow_id) == "saved"
    by_reconciler = await world.store.get(workflow_id)
    assert by_reconciler is not None and content(by_reconciler) == content(attempted)
    assert await world.digest(workflow_id) == get_digest
    assert world.faults.conflicts == 0


async def _terminated_before_computing(world: World) -> WorkflowHandle[Any, VerifyWorkflowOutput]:
    handle = await world.start(queue="idle-" + uuid4().hex)  # no worker: nothing is ever computed
    await handle.terminate("cancelled before any work")
    return handle


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_terminated_while_computing_writers_store_one_identical_failure(world: World) -> None:
    handle = await _terminated_before_computing(world)
    workflow_id = handle.id

    assert (await get_verify(workflow_id)).status_code == 200
    get_digest = await world.digest(workflow_id)
    await world.forget_report(workflow_id)
    assert await world.reconcile(workflow_id) == "saved"
    assert await world.digest(workflow_id) == get_digest

    # Both writers at once: the loser finds identical content, so neither conflicts.
    await world.forget_report(workflow_id)
    response, outcome = await asyncio.gather(get_verify(workflow_id), world.reconcile(workflow_id))
    assert response.status_code == 200, response.text
    assert outcome in {"saved", "already_present"}
    assert world.faults.conflicts == 0
    assert await world.store._collection.count_documents({"workflow_id": workflow_id}) == 1
    assert await world.digest(workflow_id) == get_digest
    saved = await world.store.get(workflow_id)
    assert saved is not None and saved.workflow_status == VerifyWorkflowStatus.FAILED
    assert saved.workflow_error == "Verification workflow ended with status TERMINATED"
    assert saved.workflow_results is None


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_a_losing_writer_serves_the_stored_report(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    handle = await _terminated_before_computing(world)
    workflow_id = handle.id
    desc = await handle.describe()
    # A history-only execution: no ledger row, so GET reaches the recovery writer.
    await world.ledger.delete_verification(workflow_id)
    start = RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], compare_settings=SETTINGS)
    assert desc.status is not None and desc.close_time is not None
    other = terminal_failure_output(start, workflow_id, desc.run_id, desc.status, desc.close_time).model_copy(
        update={"workflow_error": "Stored first by another writer"})
    await world.store.save(other, VerificationProvenance(verification_id=workflow_id, workflow_run_id=desc.run_id,
                                                         owner_sub=None))

    response = await get_verify(workflow_id)
    assert response.status_code == 200, response.text
    assert response.json()["workflow_error"] == "Stored first by another writer"
    assert world.faults.conflicts == 1

    monkeypatch.setattr(world.store, "get", AsyncMock(side_effect=RuntimeError("private read failure")))
    with caplog.at_level(logging.ERROR):
        response = await get_verify(workflow_id)
    assert response.status_code == 503
    assert "private" not in response.text
    assert "verification_persistence_attention reason=integrity_conflict" in caplog.text


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_a_stored_result_without_its_report_is_an_incident(
    world: World, caplog: pytest.LogCaptureFixture,
) -> None:
    handle = await world.start()
    result = await asyncio.wait_for(handle.result(), 30)
    assert result.persistence_state == ReportPersistenceState.STORED
    await world.forget_report(handle.id)  # e.g. a wiped or partially restored store

    with caplog.at_level(logging.ERROR):
        response = await get_verify(handle.id)
    assert response.status_code == 503
    assert "verification_persistence_attention reason=stored_report_missing" in caplog.text
    assert await world.store.get(handle.id) is None  # the slim result was not stored as the report


# ---------------------------------------------------------------------------
# A completed execution is never answered with a synthesized failure (I-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.CONTINUED_AS_NEW,
                                    WorkflowExecutionStatus.RUNNING])
async def test_an_unreadable_result_is_never_replaced_by_a_failure(status: WorkflowExecutionStatus) -> None:
    from tests.api.test_main import _temporal_with_describe
    temporal = _temporal_with_describe(workflow_type="RunsVerifyWorkflow", exec_status=status,
                                       query_side_effect=ValueError("result could not be decoded"))
    read_start: Callable[..., Awaitable[Any]] = AsyncMock()
    scan: Callable[..., Awaitable[Any]] = AsyncMock()
    publish = AsyncMock()
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.read_start_input", new=read_start), \
         patch("biosim_server.api.main.computed_output_from_history", new=scan), \
         patch("biosim_server.api.main._publish_recovered_output", new=publish):
        response = await get_verify("wf")
    assert response.status_code == 503
    read_start.assert_not_awaited()  # type: ignore[attr-defined]
    scan.assert_not_awaited()  # type: ignore[attr-defined]
    publish.assert_not_awaited()
