"""GET /verify/{workflow_id} reads the durable report store first.

Five behaviours, each guarding a decision that is easy to get wrong:

1. a stored report is served even when Temporal is unavailable -- the whole point;
2. a corrupt stored report falls back to Temporal instead of returning 500;
3. ownership is enforced on a store hit, so the store is not a cross-user oracle;
4. a finished report that was never saved is caught up, but only after authorization;
5. a store outage falls back to Temporal instead of failing the request.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.report_store import ReportCorrupt
from biosim_server.common.auth import AuthenticatedUser, get_current_user, get_optional_user


def _output(
    workflow_id: str = "omex-verification-stored",
    status: VerifyWorkflowStatus = VerifyWorkflowStatus.COMPLETED,
    owner_sub: str | None = "auth0|owner",
) -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id=workflow_id,
        compare_settings=CompareSettings(
            user_description="stored", include_outputs=False,
            rel_tol=1e-4, abs_tol_min=1e-3, abs_tol_scale=1e-5,
        ),
        workflow_status=status,
        timestamp="2025-01-01T00:00:00Z",
        owner_sub=owner_sub,
    )


def _store_returning(output: VerifyWorkflowOutput | None) -> Any:
    store = MagicMock()
    store.get = AsyncMock(return_value=output)
    store.save = AsyncMock(return_value=True)
    return store


async def _get(workflow_id: str, user: AuthenticatedUser | None) -> Any:
    """Call GET /verify/{id} as *user*, with the auth dependency overridden."""
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/verify/{workflow_id}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_optional_user, None)


@pytest.mark.asyncio
async def test_store_hit_is_served_with_temporal_unavailable() -> None:
    """A stored report answers even when Temporal is down.

    This is the feature: the report outlives Temporal retention, so the store
    read must happen before the Temporal client is required.
    """
    stored = _output()
    store = _store_returning(stored)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=None), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(stored.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    assert response.json()["workflow_id"] == stored.workflow_id
    assert response.json()["workflow_status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_store_hit_records_the_terminal_status() -> None:
    """A store hit still teaches the ledger the terminal status, for the listing."""
    stored = _output()
    store = _store_returning(stored)
    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=None), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get(stored.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    ledger.record_terminal_status.assert_awaited_once_with(stored.workflow_id, stored.workflow_status)


@pytest.mark.asyncio
async def test_corrupt_stored_report_falls_back_to_temporal() -> None:
    """A damaged stored report is a miss, not a 500: Temporal may still answer."""
    store = MagicMock()
    store.get = AsyncMock(side_effect=ReportCorrupt("payload is garbage"))
    temporal_output = _output(workflow_id="omex-verification-corrupt")
    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=None, id=temporal_output.workflow_id,
        run_id="run-1", close_time=None, start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=temporal_output)
    temporal.get_workflow_handle.return_value = handle

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(temporal_output.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    assert response.json()["workflow_id"] == temporal_output.workflow_id


@pytest.mark.asyncio
async def test_store_hit_requires_authentication_for_an_owned_report() -> None:
    """Anonymous caller on someone else's stored report gets 401, not the report."""
    stored = _output(owner_sub="auth0|owner")
    store = _store_returning(stored)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=None), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(stored.workflow_id, None)

    assert response.status_code == 401
    # The report itself must not leak through the error body.
    assert "compare_settings" not in response.text


@pytest.mark.asyncio
async def test_store_hit_rejects_another_user() -> None:
    """A different authenticated user gets 403 on an owned stored report."""
    stored = _output(owner_sub="auth0|owner")
    store = _store_returning(stored)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=None), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(stored.workflow_id, AuthenticatedUser(sub="auth0|other", email="x@e.com"))

    assert response.status_code == 403
    assert "compare_settings" not in response.text


@pytest.mark.asyncio
async def test_ownerless_stored_report_is_public() -> None:
    """An ownerless stored report stays readable by anyone, matching the ledger rule."""
    stored = _output(owner_sub=None)
    store = _store_returning(stored)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=None), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(stored.workflow_id, None)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_catch_up_save_runs_after_authorization() -> None:
    """A finished report with no stored copy is saved on the way out."""
    temporal_output = _output(workflow_id="omex-verification-catchup")
    store = _store_returning(None)
    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=None, id=temporal_output.workflow_id,
        run_id="run-1", close_time=None, start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=temporal_output)
    temporal.get_workflow_handle.return_value = handle

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(temporal_output.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    store.save.assert_awaited_once()
    saved = store.save.await_args.args[0]
    assert saved.workflow_id == temporal_output.workflow_id


@pytest.mark.asyncio
async def test_catch_up_save_does_not_run_for_an_unauthorized_caller() -> None:
    """Authorization comes first, so an unauthorized caller never triggers a write."""
    temporal_output = _output(workflow_id="omex-verification-catchup-denied", owner_sub="auth0|owner")
    store = _store_returning(None)
    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=None, id=temporal_output.workflow_id,
        run_id="run-1", close_time=None, start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=temporal_output)
    temporal.get_workflow_handle.return_value = handle

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(temporal_output.workflow_id, AuthenticatedUser(sub="auth0|other", email="x@e.com"))

    assert response.status_code == 403
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_error_falls_back_to_temporal() -> None:
    """A store outage must not fail the GET; Temporal is the fallback."""
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    temporal_output = _output(workflow_id="omex-verification-store-down")
    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=None, id=temporal_output.workflow_id,
        run_id="run-1", close_time=None, start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=temporal_output)
    temporal.get_workflow_handle.return_value = handle

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(temporal_output.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    assert response.json()["workflow_id"] == temporal_output.workflow_id


# ---------------------------------------------------------------------------
# Fix 1: a broken store must not be reported as permanent absence
# ---------------------------------------------------------------------------


def _temporal_describe_not_found() -> Any:
    """A Temporal client whose describe raises NOT_FOUND, i.e. history is gone."""
    from temporalio.service import RPCError, RPCStatusCode

    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("nf", RPCStatusCode.NOT_FOUND, b""))
    temporal.get_workflow_handle.return_value = handle
    return temporal


@pytest.mark.asyncio
async def test_store_unavailable_and_temporal_not_found_is_503() -> None:
    """A broken store plus a missing history is a 503, never "not found".

    Reporting 404 here would tell the caller the verification never existed, when
    the report may still be in a store that is merely unreachable.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-gone", None)

    assert response.status_code == 503
    assert "storage is unavailable" in response.json()["detail"]


@pytest.mark.asyncio
async def test_corrupt_store_and_temporal_not_found_is_503() -> None:
    """A corrupt stored report plus a missing history is a 503, not "no longer retained"."""
    store = MagicMock()
    store.get = AsyncMock(side_effect=ReportCorrupt("payload is garbage"))
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-corrupt-gone", None)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_store_miss_and_temporal_not_found_is_still_404() -> None:
    """A genuine miss plus a missing history stays 404: the fix must not swallow real 404s."""
    store = _store_returning(None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-really-gone", None)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Fix 2: a rebuilt shell must not occupy the write-once slot
# ---------------------------------------------------------------------------


def _temporal_terminated(*, with_computed: bool) -> Any:
    """A Temporal client whose workflow is TERMINATED and whose query fails.

    ``with_computed`` controls whether history contains the persist activity's
    scheduled event, which is the only proof that a result was ever computed.
    """
    from temporalio.api.history.v1 import HistoryEvent
    from temporalio.client import WorkflowExecutionStatus, WorkflowQueryFailedError

    from biosim_server.common.temporal import pydantic_data_converter

    # Deliberately unlike the rebuilt shell in three ways the test can assert on:
    # it is COMPLETED (the shell is FAILED), it carries results (the shell has
    # none), and its timestamp is the workflow's own rather than the close time.
    computed = _output(
        workflow_id="omex-verification-terminated",
        status=VerifyWorkflowStatus.COMPLETED,
    )
    computed.timestamp = "2025-01-01T00:00:00Z"
    computed.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[], comparison_statistics={"shared": []})

    def _events(*_args: Any, **_kwargs: Any) -> Any:
        # fetch_history_events is a sync method returning an async iterator, so
        # this must be a plain def yielding from an async generator.
        return _history(with_computed)

    async def _history(with_computed: bool) -> Any:
        # The recovery path reads the start event first, so history always has it.
        start = HistoryEvent()
        start.workflow_execution_started_event_attributes.workflow_type.name = "OmexVerifyWorkflow"
        start.workflow_execution_started_event_attributes.input.payloads.extend(
            await pydantic_data_converter.encode([
                OmexVerifyWorkflowInput(
                    omex_file=OmexFile(file_hash_md5="hash", uploaded_filename="m.omex",
                                       bucket_name="test", omex_gcs_path="m.omex", file_size=1),
                    requested_simulators=[], compare_settings=computed.compare_settings,
                    cache_buster="0")]))
        yield start
        if with_computed:
            payloads = await pydantic_data_converter.encode([computed])
            event = HistoryEvent()
            event.activity_task_scheduled_event_attributes.activity_type.name = \
                "save_verification_report_activity"
            event.activity_task_scheduled_event_attributes.input.payloads.extend(payloads)
            yield event

    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=WorkflowExecutionStatus.TERMINATED,
        id=computed.workflow_id, run_id="run-1",
        close_time=datetime(2025, 1, 1, tzinfo=UTC),
        start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(side_effect=WorkflowQueryFailedError("terminated"))
    handle.fetch_history_events = _events

    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    temporal.data_converter = pydantic_data_converter
    return temporal


@pytest.mark.asyncio
async def test_terminated_before_computing_does_not_consume_the_store_slot() -> None:
    """TERMINATED with no computed result in history: serve the shell, store nothing.

    Storing the rebuilt output would occupy the write-once slot with a permanent
    FAILED report, so a later recovery of the real result could never be saved.
    """
    store = _store_returning(None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated(with_computed=False)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminated_after_computing_stores_the_computed_result() -> None:
    """TERMINATED with a computed result in history: that result is what gets stored."""
    store = _store_returning(None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated(with_computed=True)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    # The scan proved a result existed, so the save is allowed -- and what it
    # saved must be that result, not the rebuilt shell. Assert the properties
    # that distinguish them, or a mutant that saves the shell would pass.
    store.save.assert_awaited_once()
    saved = store.save.await_args.args[0]
    assert saved.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert saved.workflow_results is not None
    assert saved.timestamp == "2025-01-01T00:00:00Z"
    # The response is the computed result too, not the shell.
    body = response.json()
    assert body["workflow_status"] == "COMPLETED"
    assert body["workflow_results"] is not None


@pytest.mark.asyncio
async def test_failed_scan_does_not_store_the_rebuilt_shell() -> None:
    """A scan that raises proves nothing, so the shell must not be written.

    The store is write-once: writing a FAILED shell here would permanently block
    the real result, which may still be in history. A skipped write is retried by
    the next GET; a wrong write cannot be undone.
    """
    store = _store_returning(None)
    temporal = _temporal_terminated(with_computed=False)
    # The first fetch_history_events call serves the start event (the recovery
    # path needs it); the second, the scan, raises.
    handle = temporal.get_workflow_handle.return_value
    real_events = handle.fetch_history_events
    calls = {"n": 0}

    def _flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_events(*args, **kwargs)
        raise RuntimeError("temporal hiccup during scan")

    handle.fetch_history_events = _flaky

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    store.save.assert_not_awaited()
