"""Remaining verification acceptance and failure-path coverage."""
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientResponseError
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowQueryFailedError, WorkflowQueryRejectedError
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.biosim_runs.models import BiosimulatorVersion
from biosim_server.api.main import app, _load_hdf5_metadata_for_preflight
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.models import VerificationRecord, VerificationType, VerifyWorkflowStatus
from biosim_server.common.auth import AuthenticatedUser, get_current_user, get_optional_user
from tests.api.test_main import _make_temporal_for_runs, _make_verify_output, _temporal_with_describe, _verify_omex_mocks
from tests.biosim_verify.test_compatibility import _file


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("error, expected", [
    (TimeoutError("private failure"), 503),
    (RuntimeError("private failure"), 503),
    (RPCError("private failure", RPCStatusCode.UNAVAILABLE, b""), 503),
    (WorkflowQueryFailedError("private failure"), 404),
    (WorkflowQueryRejectedError(WorkflowExecutionStatus.FAILED), 404),
])
async def test_query_error_mapping(client: AsyncClient, error: Exception, expected: int) -> None:
    temporal = _temporal_with_describe(query_side_effect=error)
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        response = await client.get("/verify/wf")
    assert response.status_code == expected
    assert "private failure" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(WorkflowExecutionStatus))
async def test_get_reconciles_only_failed_terminal_states(client: AsyncClient, status: WorkflowExecutionStatus) -> None:
    output = _make_verify_output(status=VerifyWorkflowStatus.IN_PROGRESS)
    temporal = _temporal_with_describe(exec_status=status, query_result=output)
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        response = await client.get("/verify/wf")
    assert response.status_code == 200
    body = response.json()
    failed = status in {WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.TERMINATED,
                        WorkflowExecutionStatus.TIMED_OUT, WorkflowExecutionStatus.CANCELED}
    assert body["workflow_status"] == ("FAILED" if failed else "IN_PROGRESS")
    assert bool(body["workflow_error"]) == failed
    assert body["workflow_results"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger_available", [False, True])
async def test_missing_workflow_degrades_when_ledger_unavailable(client: AsyncClient, ledger_available: bool) -> None:
    temporal = _temporal_with_describe()
    temporal.get_workflow_handle.return_value.describe.side_effect = RPCError("private", RPCStatusCode.NOT_FOUND, b"")
    ledger = AsyncMock()
    ledger.get_verification.side_effect = RuntimeError("private database")
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger if ledger_available else None):
        response = await client.get("/verify/wf")
    assert response.status_code == 404
    assert response.json() == {"detail": "Verification not found: wf"}


@pytest.mark.asyncio
async def test_expired_workflow_admin_can_see_retention_detail(client: AsyncClient) -> None:
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: AuthenticatedUser(sub="admin", roles=["admin"])
    temporal = _temporal_with_describe()
    temporal.get_workflow_handle.return_value.describe.side_effect = RPCError("private", RPCStatusCode.NOT_FOUND, b"")
    ledger = AsyncMock()
    ledger.get_verification.return_value = VerificationRecord(workflow_id="wf", verify_type=VerificationType.RUNS,
                                                             owner_sub="someone-else", created=datetime.now(UTC))
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await client.get("/verify/wf")
    assert response.status_code == 404
    assert "no longer retained" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("row_owner, expected_detail", [
    ("auth0|owner", "Verification wf exists but its results are no longer retained."),
    ("someone-else", "Verification not found: wf"),
    (None, "Verification wf exists but its results are no longer retained."),
])
async def test_history_purged_between_describe_and_query_is_404(
        client: AsyncClient, row_owner: str | None, expected_detail: str) -> None:
    """describe succeeds, then query hits NOT_FOUND: same ledger fallback as a describe miss, not 503."""
    temporal = _temporal_with_describe(query_side_effect=RPCError("private failure", RPCStatusCode.NOT_FOUND, b""))
    ledger = AsyncMock()
    ledger.get_verification.return_value = VerificationRecord(workflow_id="wf", verify_type=VerificationType.OMEX,
                                                             owner_sub=row_owner, created=datetime.now(UTC))
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await client.get("/verify/wf")
    assert response.status_code == 404
    assert response.json() == {"detail": expected_detail}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runs", "omex"])
async def test_missing_ledger_rejects_before_upload_or_upstream_calls(client: AsyncClient, kind: str) -> None:
    """An unavailable ledger is a 503 before any storage write, simulator lookup or metadata fetch."""
    files, omex, biosim, temporal = _verify_omex_mocks()
    db = AsyncMock()
    with ExitStack() as stack:
        for name, service in [("get_file_service", files), ("get_omex_database_service", omex),
                              ("get_biosim_service", biosim), ("get_database_service", db),
                              ("get_temporal_client", temporal), ("get_verification_database_service", None)]:
            stack.enter_context(patch(f"biosim_server.api.main.{name}", return_value=service))
        if kind == "runs":
            response = await client.post("/verify/runs", params={"biosimulations_run_ids": ["r1", "r2"]})
        else:
            response = await client.post("/verify/omex", params={"simulators": "copasi"},
                                         files={"uploaded_file": ("m.omex", b"fake", "application/zip")})
    assert response.status_code == 503
    assert response.json() == {"detail": "Verification database service not available"}
    files.upload_bytes.assert_not_awaited()
    omex.get_omex_file_by_hash_and_owner.assert_not_awaited()
    omex.insert_omex_file.assert_not_awaited()
    biosim.get_simulator_versions.assert_not_awaited()
    db.get_biosimulator_workflow_runs_by_biosim_runid.assert_not_awaited()
    biosim.get_hdf5_metadata.assert_not_called()
    temporal.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_encoded_slash_not_routable(client: AsyncClient) -> None:
    response = await client.get("/verify/bad%2Fid")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runs", "omex"])
@pytest.mark.parametrize("failure", ["insert", "start", "cleanup", "no_ledger", "no_temporal", "bad_prefix"])
async def test_submission_failure_paths(client: AsyncClient, kind: str, failure: str) -> None:
    files, omex, biosim, temporal = _verify_omex_mocks()
    ledger = AsyncMock()
    if failure == "insert":
        ledger.insert_verification.side_effect = RuntimeError("private database")
    if failure in {"start", "cleanup"}:
        temporal.start_workflow.side_effect = RPCError("private temporal", RPCStatusCode.INVALID_ARGUMENT, b"")
    if failure == "cleanup":
        ledger.delete_verification.side_effect = RuntimeError("private cleanup")
    with ExitStack() as stack:
        for name, service in [("get_file_service", files), ("get_omex_database_service", omex),
                              ("get_biosim_service", biosim),
                              ("get_temporal_client", None if failure == "no_temporal" else temporal),
                              ("get_verification_database_service", None if failure == "no_ledger" else ledger)]:
            stack.enter_context(patch(f"biosim_server.api.main.{name}", return_value=service))
        params = {"workflow_id_prefix": "bad/prefix" if failure == "bad_prefix" else "custom-"}
        if kind == "runs":
            params["biosimulations_run_ids"] = "r1"
            response = await client.post("/verify/runs", params=params)
        else:
            params["simulators"] = "copasi"
            response = await client.post("/verify/omex", params=params,
                                         files={"uploaded_file": ("m.omex", b"fake", "application/zip")})
    assert response.status_code == (422 if failure == "bad_prefix" else 503)
    assert "private" not in response.text
    if failure in {"start", "cleanup"}:
        record = ledger.insert_verification.call_args.args[0]
        ledger.delete_verification.assert_awaited_once_with(record.workflow_id)
    else:
        temporal.start_workflow.assert_not_awaited()
    if failure in {"no_temporal", "bad_prefix"}:
        ledger.insert_verification.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("kind", ["runs", "omex"])
async def test_post_then_list_uses_real_ledger(client: AsyncClient, kind: str, anonymous: bool,
        verification_database_service_mongo: VerificationDatabaseServiceMongo) -> None:
    """POST -> list -> GET round trip; anonymous (legacy API) callers get ownerless, readable records."""
    if anonymous:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)
    owner = None if anonymous else "auth0|owner"
    files, omex, biosim, temporal = _verify_omex_mocks()
    db = verification_database_service_mongo
    events: list[str] = []

    async def start(*args: object, id: str = "", **kwargs: object) -> MagicMock:
        record = await db.get_verification(id)
        assert record is not None
        assert record.owner_sub == owner
        assert record.verify_type == VerificationType(kind)
        events.append("persisted-before-start")
        return MagicMock(id=id, run_id="execution")

    temporal.start_workflow.side_effect = start
    with ExitStack() as stack:
        for name, service in [("get_file_service", files), ("get_omex_database_service", omex),
                              ("get_biosim_service", biosim), ("get_temporal_client", temporal)]:
            stack.enter_context(patch(f"biosim_server.api.main.{name}", return_value=service))
        if kind == "runs":
            response = await client.post("/verify/runs", params={"biosimulations_run_ids": "r1"})
        else:
            response = await client.post("/verify/omex", params={"simulators": "copasi"},
                                         files={"uploaded_file": ("m.omex", b"fake", "application/zip")})
        assert response.status_code == 200
        workflow_id = response.json()["workflow_id"]
        assert response.json()["owner_sub"] == owner
        assert events == ["persisted-before-start"]
        listing = await client.get("/verification_ids")
        assert listing.status_code == 200
        assert listing.json() == {"verification_ids": [workflow_id], "next_cursor": None}
        handle = temporal.get_workflow_handle.return_value
        handle.describe = AsyncMock(return_value=MagicMock(workflow_type="RunsVerifyWorkflow", status=WorkflowExecutionStatus.COMPLETED))
        handle.query = AsyncMock(return_value=_make_verify_output(workflow_id=workflow_id, owner_sub=owner))
        retrieved = await client.get(f"/verify/{workflow_id}")
        assert retrieved.status_code == 200
        assert retrieved.json()["workflow_id"] == workflow_id


@pytest.mark.asyncio
@pytest.mark.parametrize("observables, expected", [(["absent"], 400), (["x"], 200), (["x", "absent"], 200)])
async def test_preflight_requested_observables(client: AsyncClient, observables: list[str], expected: int) -> None:
    metadata = {rid: _file(rid, {"shared": ["t", "x"]}) for rid in ["r1", "r2"]}
    ledger = AsyncMock()
    temporal = _make_temporal_for_runs()
    with patch("biosim_server.api.main._load_hdf5_metadata_for_preflight", new=AsyncMock(return_value=metadata)), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await client.post("/verify/runs", params={"biosimulations_run_ids": ["r1", "r2"], "observables": observables})
    assert response.status_code == expected
    if expected == 400:
        ledger.insert_verification.assert_not_awaited()
        temporal.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_prefers_cache() -> None:
    metadata = _file("r1", {"shared": ["t", "x"]})
    db, upstream = AsyncMock(), AsyncMock()
    db.get_biosimulator_workflow_runs_by_biosim_runid.return_value = [MagicMock(hdf5_file=metadata)]
    with patch("biosim_server.api.main.get_database_service", return_value=db), \
         patch("biosim_server.api.main.get_biosim_service", return_value=upstream):
        result = await _load_hdf5_metadata_for_preflight(["r1", "r1"])
    assert result == {"r1": metadata}
    db.get_biosimulator_workflow_runs_by_biosim_runid.assert_awaited_once_with("r1")
    upstream.get_hdf5_metadata.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("run_count", [2, 3])
async def test_preflight_skips_unavailable_metadata(client: AsyncClient, run_count: int) -> None:
    db, upstream, ledger = AsyncMock(), AsyncMock(), AsyncMock()
    db.get_biosimulator_workflow_runs_by_biosim_runid.return_value = []
    upstream.get_hdf5_metadata.side_effect = [ClientResponseError(MagicMock(), (), status=404, message="upstream not found")] + [
        _file(f"r{i}", {"shared": ["t", "x"]}) for i in range(1, run_count)]
    temporal = _make_temporal_for_runs()
    with patch("biosim_server.api.main.get_database_service", return_value=db), \
         patch("biosim_server.api.main.get_biosim_service", return_value=upstream), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        response = await client.post("/verify/runs", params={"biosimulations_run_ids": [f"r{i}" for i in range(run_count)]})
    assert response.status_code == 200
    assert upstream.get_hdf5_metadata.await_count == run_count
    ledger.insert_verification.assert_awaited_once()


@pytest.mark.integration_local
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "missing"])
async def test_real_temporal_and_mongo_roundtrip(
    client: AsyncClient,
    temporal_client: "Client",
    verification_database_service_mongo: VerificationDatabaseServiceMongo,
    simulator_version_copasi: "BiosimulatorVersion",
    outcome: str,
) -> None:
    """Real workflow, query/describe RPCs and ledger; external run download is stubbed."""
    from biosim_server.biosim_omex.models import OmexFile
    from biosim_server.biosim_runs.models import BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorWorkflowRun, Hdf5DataValues
    from biosim_server.biosim_runs.activities import GetExistingBiosimSimulationRunActivityInput, GetExistingBiosimSimulationRunActivityOutput
    from biosim_server.biosim_verify.activities import generate_statistics_activity
    from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError
    from temporalio.worker import Worker, UnsandboxedWorkflowRunner

    @activity.defn(name="get_existing_biosim_simulation_run_activity")
    async def fetch_run(arg: GetExistingBiosimSimulationRunActivityInput) -> GetExistingBiosimSimulationRunActivityOutput:
        if outcome == "failed":
            raise ApplicationError("private worker error", non_retryable=True)
        if outcome == "missing":
            return GetExistingBiosimSimulationRunActivityOutput(status=BiosimSimulationRunStatus.RUN_ID_NOT_FOUND)
        run = BiosimSimulationRun(id=arg.biosim_run_id, name="test", simulator_version=simulator_version_copasi,
                                  status=BiosimSimulationRunStatus.SUCCEEDED)
        archive = OmexFile(file_hash_md5="hash", uploaded_filename="m.omex", bucket_name="test",
                           omex_gcs_path="m.omex", file_size=1)
        return GetExistingBiosimSimulationRunActivityOutput(status=run.status, biosim_workflow_run=BiosimulatorWorkflowRun(
            workflow_id=arg.workflow_id, file_hash_md5="hash", image_digest="digest", cache_buster="0",
            omex_file=archive, simulator_version=simulator_version_copasi, biosim_run=run,
            hdf5_file=_file(arg.biosim_run_id, {"shared": ["t", "x"]}),
        ))

    async with Worker(temporal_client, task_queue="verification_tasks", workflows=[RunsVerifyWorkflow],
                      activities=[fetch_run, generate_statistics_activity], workflow_runner=UnsandboxedWorkflowRunner()):
        with patch("biosim_server.biosim_verify.activities.BiosimServiceRest.get_hdf5_data",
                   new=AsyncMock(return_value=Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5]))):
            response = await client.post("/verify/runs", params={"biosimulations_run_ids": "run1"})
            assert response.status_code == 200
            workflow_id = response.json()["workflow_id"]
            assert (await client.get("/verification_ids")).json() == {"verification_ids": [workflow_id], "next_cursor": None}
            handle = temporal_client.get_workflow_handle(workflow_id)
            if outcome == "failed":
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
            else:
                await handle.result()
            response = await client.get(f"/verify/{workflow_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["workflow_status"] == {"completed": "COMPLETED", "failed": "FAILED", "missing": "RUN_ID_NOT_FOUND"}[outcome]
            assert "private worker error" not in response.text
            if outcome == "completed":
                assert body["workflow_results"]["comparison_statistics"]["shared"][0][0]["score"] == [0.0, 0.0]


@pytest.mark.integration_local
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runs", "omex"])
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_start_reply_loss_and_termination_before_first_task(
    client: AsyncClient, temporal_client: Client,
    verification_database_service_mongo: VerificationDatabaseServiceMongo,
    kind: str, lost_reply: bool,
) -> None:
    """No worker: history still authorizes/reconstructs output; accepted starts stay listed."""
    files, omex, biosim, _ = _verify_omex_mocks()
    real_start = temporal_client.start_workflow

    async def start_then_lose_reply(*args: Any, **kwargs: Any) -> object:
        result = await real_start(*args, **kwargs)
        if lost_reply:
            raise RPCError("private lost reply", RPCStatusCode.UNAVAILABLE, b"")
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(temporal_client, "start_workflow", new=AsyncMock(side_effect=start_then_lose_reply)))
        for name, svc in [("get_file_service", files), ("get_omex_database_service", omex), ("get_biosim_service", biosim)]:
            stack.enter_context(patch(f"biosim_server.api.main.{name}", return_value=svc))
        if kind == "runs":
            response = await client.post("/verify/runs", params={"biosimulations_run_ids": "run1", "rel_tol": 0.02})
        else:
            response = await client.post("/verify/omex", params={"simulators": "copasi", "rel_tol": 0.02},
                                         files={"uploaded_file": ("m.omex", b"fake", "application/zip")})
    assert response.status_code == 200, response.text
    wid = response.json()["workflow_id"]
    handle = temporal_client.get_workflow_handle(wid)
    desc = await handle.describe()
    assert desc.status == WorkflowExecutionStatus.RUNNING
    if lost_reply:
        assert response.json()["workflow_run_id"] == desc.run_id
    assert (await client.get("/verification_ids")).json() == {"verification_ids": [wid], "next_cursor": None}
    await handle.terminate("private termination reason")
    output = await client.get(f"/verify/{wid}")
    assert output.status_code == 200, output.text
    assert output.json()["workflow_status"] == "FAILED"
    assert output.json()["compare_settings"]["rel_tol"] == 0.02
    assert output.json()["owner_sub"] == "auth0|owner"
    assert output.json()["workflow_run_id"] == desc.run_id
    assert output.json()["workflow_error"] == "Verification workflow ended with status TERMINATED"
    assert "private" not in output.text
    # Retained history must also cover old workflows with no ledger row.
    await verification_database_service_mongo.delete_verification(wid)
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: AuthenticatedUser(sub="stranger")
    assert (await client.get(f"/verify/{wid}")).status_code == 403
    app.dependency_overrides[get_current_user] = app.dependency_overrides[get_optional_user] = lambda: AuthenticatedUser(sub="admin", roles=["admin"])
    assert (await client.get(f"/verify/{wid}")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [RPCStatusCode.UNAVAILABLE, RPCStatusCode.NOT_FOUND])
async def test_ambiguous_start_retains_ledger_even_after_describe_miss(client: AsyncClient, followup: RPCStatusCode) -> None:
    temporal, ledger = _make_temporal_for_runs(), AsyncMock()
    temporal.start_workflow.side_effect = TimeoutError("private timeout")
    temporal.get_workflow_handle.return_value.describe = AsyncMock(side_effect=RPCError("private followup", followup, b""))
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await client.post("/verify/runs", params={"biosimulations_run_ids": "run1"})
    assert response.status_code == 503
    assert "private" not in response.text
    record = ledger.insert_verification.call_args.args[0]
    assert record.workflow_id in response.json()["detail"]
    ledger.delete_verification.assert_not_awaited()
    temporal.start_workflow.assert_awaited_once()


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_terminal_history_fallback_allows_legacy_ownerless(
    client: AsyncClient, temporal_client: Client,
) -> None:
    import uuid
    from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflow, RunsVerifyWorkflowInput
    original = RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], compare_settings=_make_verify_output().compare_settings)
    handle = await temporal_client.start_workflow(RunsVerifyWorkflow.run, original, id=f"legacy-{uuid.uuid4()}", task_queue="no-worker")
    await handle.terminate()
    response = await client.get(f"/verify/{handle.id}")
    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    assert response.json()["owner_sub"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.CANCELED,
                                   WorkflowExecutionStatus.TIMED_OUT, WorkflowExecutionStatus.TERMINATED])
async def test_unqueryable_terminal_statuses_recover_from_history(client: AsyncClient, status: WorkflowExecutionStatus) -> None:
    from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
    original = RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], owner_sub="auth0|owner",
                                       compare_settings=_make_verify_output().compare_settings)
    temporal = _temporal_with_describe(workflow_type="RunsVerifyWorkflow", exec_status=status,
        query_side_effect=RPCError("private closed-before-start error", RPCStatusCode.INTERNAL, b""))
    desc = temporal.get_workflow_handle.return_value.describe.return_value
    desc.id, desc.run_id = "wf", "execution"
    desc.close_time = datetime.now(UTC)
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main._read_verification_input", new=AsyncMock(return_value=original)):
        response = await client.get("/verify/wf")
    assert response.status_code == 200
    assert response.json()["workflow_error"] == f"Verification workflow ended with status {status.name}"
    assert response.json()["workflow_status"] == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected", [(RPCError("private", RPCStatusCode.NOT_FOUND, b""), 404),
    (RPCError("private", RPCStatusCode.UNAVAILABLE, b""), 503), (ValueError("private payload"), 503)])
async def test_terminal_recovery_failure_is_sanitized(client: AsyncClient, failure: Exception, expected: int) -> None:
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.TERMINATED,
        query_side_effect=RPCError("closed", RPCStatusCode.INTERNAL, b""))
    temporal.get_workflow_handle.return_value.describe.return_value.id = "wf"
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=None), \
         patch("biosim_server.api.main._read_verification_input", new=AsyncMock(side_effect=failure)):
        response = await client.get("/verify/wf")
    assert response.status_code == expected
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_recovered_start_must_match_submission(client: AsyncClient) -> None:
    from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
    temporal, ledger = _make_temporal_for_runs(), AsyncMock()
    temporal.start_workflow.side_effect = RPCError("private", RPCStatusCode.UNAVAILABLE, b"")
    temporal.get_workflow_handle.return_value.describe = AsyncMock(return_value=MagicMock(workflow_type="RunsVerifyWorkflow"))
    wrong = RunsVerifyWorkflowInput(biosimulations_run_ids=["run1"], owner_sub="other",
                                   compare_settings=_make_verify_output().compare_settings)
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.api.main._read_verification_input", new=AsyncMock(return_value=wrong)):
        response = await client.post("/verify/runs", params={"biosimulations_run_ids": "run1"})
    assert response.status_code == 503
    ledger.delete_verification.assert_not_awaited()
