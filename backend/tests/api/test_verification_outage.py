"""GET /verify during a ledger outage: progress stays available, nothing is guessed (S-8, D6)."""
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pymongo.errors import ServerSelectionTimeoutError
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.api.main import app
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    ReportPersistenceState,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.report_storage import ReportCorrupt
from biosim_server.common.auth import AuthenticatedUser, get_optional_user
from tests.api.test_main import _make_verify_output, _temporal_with_describe

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

OWNER = AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
OTHER = AuthenticatedUser(sub="auth0|other", email="other@example.test")
ADMIN = AuthenticatedUser(sub="auth0|admin", email="admin@example.test", roles=["admin"])


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_optional_user, None)


def _down_ledger() -> AsyncMock:
    ledger = AsyncMock()
    ledger.get_verification.side_effect = ServerSelectionTimeoutError("mongodb://user:secret@db unreachable")
    return ledger


def _store() -> MagicMock:
    store = MagicMock()
    store.get = AsyncMock(side_effect=AssertionError("the report store is down too"))
    store.save = AsyncMock(side_effect=AssertionError("no catch-up writes during an outage"))
    store.hydrate_activity_reference = AsyncMock(side_effect=lambda output: output)
    return store


@contextmanager
def _outage(temporal: Any, *, ledger: Any = None, store: Any = None, user: AuthenticatedUser | None = None) -> Iterator[Any]:
    ledger = ledger or _down_ledger()
    store = store or _store()
    app.dependency_overrides[get_optional_user] = lambda: user
    history = AsyncMock(side_effect=AssertionError("no history recovery during an outage"))
    with patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.computed_output_from_history", new=history):
        yield ledger, store


def _assert_no_writes(ledger: AsyncMock, store: MagicMock) -> None:
    assert [call[0] for call in ledger.mock_calls] == ["get_verification"]
    store.save.assert_not_awaited()


def _progress(owner: str | None = None) -> VerifyWorkflowOutput:
    return _make_verify_output(status=VerifyWorkflowStatus.IN_PROGRESS, owner_sub=owner).model_copy(
        update={"workflow_run_id": "run"})


@pytest.mark.asyncio
async def test_a_running_verification_still_reports_progress(client: AsyncClient) -> None:
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.RUNNING, query_result=_progress())
    with _outage(temporal) as (ledger, store):
        response = await client.get("/verify/wf")
    assert response.status_code == 200
    assert response.json()["workflow_status"] == "IN_PROGRESS"
    assert response.headers["cache-control"] == "private, no-store"
    _assert_no_writes(ledger, store)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ReportPersistenceState.BLOCKED, ReportPersistenceState.PENDING])
async def test_a_completed_result_that_was_never_stored_is_returned_as_pending(
    client: AsyncClient, state: ReportPersistenceState,
) -> None:
    result = _make_verify_output(owner_sub=None).model_copy(update={
        "workflow_run_id": "run", "persistence_state": state,
        "workflow_results": GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={}),
    })
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.COMPLETED, query_result=result)
    with _outage(temporal) as (ledger, store):
        response = await client.get("/verify/wf")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == "COMPLETED" and body["workflow_results"] is not None
    assert body["persistence_state"] == "pending" and body["report_available"] is False
    assert response.headers["cache-control"] == "private, no-store"
    _assert_no_writes(ledger, store)


def _assert_retry(response: Any) -> None:
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json() == {"detail": "Verification ledger unavailable"}
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_a_stored_report_is_a_503_to_retry(client: AsyncClient) -> None:
    slim = _make_verify_output(owner_sub=None).model_copy(update={
        "workflow_run_id": "run", "persistence_state": ReportPersistenceState.STORED, "report_available": True,
    })
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.COMPLETED, query_result=slim)
    with _outage(temporal) as (ledger, store):
        response = await client.get("/verify/wf")
    _assert_retry(response)
    _assert_no_writes(ledger, store)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.TERMINATED,
                                    WorkflowExecutionStatus.TIMED_OUT, WorkflowExecutionStatus.CANCELED])
async def test_a_failed_execution_is_a_503_to_retry(client: AsyncClient, status: WorkflowExecutionStatus) -> None:
    temporal = _temporal_with_describe(exec_status=status, query_result=_progress())
    with _outage(temporal) as (ledger, store):
        response = await client.get("/verify/wf")
    _assert_retry(response)
    _assert_no_writes(ledger, store)
    temporal.get_workflow_handle.return_value.query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_at", ["describe", "query"])
async def test_an_unknown_workflow_is_never_a_404_during_an_outage(client: AsyncClient, missing_at: str) -> None:
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.RUNNING, query_result=_progress())
    handle = temporal.get_workflow_handle.return_value
    gone = RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
    if missing_at == "describe":
        handle.describe.side_effect = gone
    else:
        handle.query.side_effect = gone
    with _outage(temporal) as (ledger, store):
        response = await client.get("/verify/wf")
    _assert_retry(response)
    _assert_no_writes(ledger, store)


@pytest.mark.asyncio
@pytest.mark.parametrize("user, status", [(None, 401), (OTHER, 403), (OWNER, 200), (ADMIN, 200)])
async def test_a_private_verification_still_needs_its_owner(
    client: AsyncClient, user: AuthenticatedUser | None, status: int,
) -> None:
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.RUNNING,
                                       query_result=_progress(owner=OWNER.sub))
    with _outage(temporal, user=user):
        response = await client.get("/verify/wf")
    assert response.status_code == status
    if status == 200:
        assert response.json()["owner_sub"] == OWNER.sub


@pytest.mark.asyncio
async def test_a_report_store_outage_falls_back_the_same_way(client: AsyncClient) -> None:
    ledger = AsyncMock()
    ledger.get_verification.return_value = None
    store = _store()
    store.get = AsyncMock(side_effect=ServerSelectionTimeoutError("report store unreachable"))
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.RUNNING, query_result=_progress())
    with _outage(temporal, ledger=ledger, store=store):
        response = await client.get("/verify/wf")
    assert response.status_code == 200
    assert response.json()["workflow_status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_a_corrupt_report_is_not_an_outage(client: AsyncClient) -> None:
    """Only a dependency error falls back; a report that cannot be decoded stays a plain 503."""
    ledger = AsyncMock()
    ledger.get_verification.return_value = MagicMock(owner_sub=None)
    store = _store()
    store.get = AsyncMock(side_effect=ReportCorrupt("checksum mismatch"))
    temporal = _temporal_with_describe(exec_status=WorkflowExecutionStatus.RUNNING, query_result=_progress())
    with _outage(temporal, ledger=ledger, store=store):
        response = await client.get("/verify/wf")
    assert response.status_code == 503
    assert response.json() == {"detail": "Stored verification report is unavailable"}
    assert "retry-after" not in response.headers
    temporal.get_workflow_handle.return_value.describe.assert_not_awaited()
