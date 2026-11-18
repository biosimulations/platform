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
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
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
