"""GET /verify/{workflow_id} reads the durable report store first.

Six behaviours, each guarding a decision that is easy to get wrong:

1. a stored report is served even when Temporal is unavailable -- the whole point;
2. a corrupt stored report falls back to Temporal instead of returning 500;
3. ownership is enforced on a store hit, so the store is not a cross-user oracle;
4. a finished report that was never saved is caught up, but only after authorization;
5. a store outage falls back to Temporal instead of failing the request;
6. that outage costs one bounded wait, because a store call that hangs is cut off
   and a failed read skips the catch-up save.
"""

import asyncio
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
from biosim_server.common.auth.roles import ADMIN_ROLE


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


def _temporal_owning(output: VerifyWorkflowOutput) -> Any:
    """A Temporal client whose query answers with an owned verification."""
    temporal = MagicMock()
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=None, id=output.workflow_id,
        run_id="run-1", close_time=None, start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=output)
    temporal.get_workflow_handle.return_value = handle
    return temporal


@pytest.mark.asyncio
async def test_store_outage_does_not_skip_ownership_for_another_user() -> None:
    """The UNAVAILABLE branch must not become a way around the ownership check.

    A store outage only means "fall back to Temporal". The Temporal answer is
    still somebody's private verification, so another user gets 403.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    store.save = AsyncMock(return_value=True)
    owned = _output(workflow_id="omex-verification-private", owner_sub="auth0|owner")

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_owning(owned)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(owned.workflow_id, AuthenticatedUser(sub="auth0|intruder", email="i@e.com"))

    assert response.status_code == 403
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_outage_does_not_skip_authentication_for_an_owned_report() -> None:
    """With the store down, an owned verification still needs a token: 401."""
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    store.save = AsyncMock(return_value=True)
    owned = _output(workflow_id="omex-verification-private-anon", owner_sub="auth0|owner")

    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_owning(owned)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get(owned.workflow_id, None)

    assert response.status_code == 401
    store.save.assert_not_awaited()


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
    # An unreachable store may come back, so the client gets a retry hint.
    assert response.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_corrupt_store_and_temporal_not_found_is_503() -> None:
    """A corrupt stored report plus a missing history is a 503 for a caller who may see it.

    No Retry-After here: retrying cannot repair corrupt bytes, so a hint would
    only make the client poll a permanent failure.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=ReportCorrupt("payload is garbage"))
    ledger = _ledger(owner_sub=None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get("omex-verification-corrupt-gone", None)

    assert response.status_code == 503
    assert "corrupt" in response.json()["detail"]
    assert "Retry-After" not in response.headers


# ---------------------------------------------------------------------------
# Fix 2: a corruption diagnostic must not disclose a private report's existence
# ---------------------------------------------------------------------------


def _corrupt_store_and_missing_history() -> Any:
    store = MagicMock()
    store.get = AsyncMock(side_effect=ReportCorrupt("payload is garbage"))
    return store


@pytest.mark.asyncio
async def test_corruption_diagnostic_is_served_to_the_owner() -> None:
    """The owner of a corrupt report learns it is corrupt, not that it vanished."""
    with patch("biosim_server.api.main.get_verification_report_store",
               return_value=_corrupt_store_and_missing_history()), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service",
               return_value=_ledger(owner_sub="auth0|owner")):
        response = await _get("omex-verification-private-report",
                              AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 503
    assert "corrupt" in response.json()["detail"]


@pytest.mark.asyncio
async def test_corruption_diagnostic_is_served_to_an_admin() -> None:
    """An admin may see any verification, so the diagnostic is theirs too."""
    admin = AuthenticatedUser(sub="auth0|admin", email="a@e.com", roles=[ADMIN_ROLE])
    with patch("biosim_server.api.main.get_verification_report_store",
               return_value=_corrupt_store_and_missing_history()), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service",
               return_value=_ledger(owner_sub="auth0|owner")):
        response = await _get("omex-verification-private-report", admin)

    assert response.status_code == 503
    assert "corrupt" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [None, "other"])
async def test_corruption_diagnostic_is_withheld_from_others(caller: str | None) -> None:
    """A private report's corruption is not disclosed to an anonymous or unrelated caller.

    The generic 404 is the same answer a verification that never existed gets, so
    the store cannot be used to probe for private reports.
    """
    user = None if caller is None else AuthenticatedUser(sub="auth0|intruder", email="i@e.com")
    with patch("biosim_server.api.main.get_verification_report_store",
               return_value=_corrupt_store_and_missing_history()), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service",
               return_value=_ledger(owner_sub="auth0|owner")):
        response = await _get("omex-verification-private-report", user)

    assert response.status_code == 404
    assert "corrupt" not in response.json()["detail"]
    assert "no longer retained" not in response.json()["detail"]


@pytest.mark.asyncio
async def test_corruption_diagnostic_is_served_for_a_public_report() -> None:
    """An ownerless verification is public, so anyone may learn it is corrupt."""
    with patch("biosim_server.api.main.get_verification_report_store",
               return_value=_corrupt_store_and_missing_history()), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service",
               return_value=_ledger(owner_sub=None)):
        response = await _get("omex-verification-public-report", None)

    assert response.status_code == 503
    assert "corrupt" in response.json()["detail"]


@pytest.mark.asyncio
async def test_corruption_diagnostic_is_withheld_when_ownership_is_unknown() -> None:
    """A ledger that cannot answer is not evidence, so the diagnostic is withheld.

    Two cases: no ledger configured at all, and a ledger whose lookup raises.
    Neither may turn a failed lookup into a disclosure.
    """
    for ledger in (None, _ledger(raises=RuntimeError("mongo is down"))):
        with patch("biosim_server.api.main.get_verification_report_store",
                   return_value=_corrupt_store_and_missing_history()), \
             patch("biosim_server.api.main.get_temporal_client",
                   return_value=_temporal_describe_not_found()), \
             patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
            response = await _get("omex-verification-unknown-owner", None)

        assert response.status_code == 404
        assert "corrupt" not in response.json()["detail"]


@pytest.mark.asyncio
async def test_unreachable_store_still_answers_503_without_ownership_evidence() -> None:
    """An unreachable store discloses nothing, so it needs no ownership check.

    This is the deliberate asymmetry with CORRUPT: "the store is down" says
    nothing about whether a report exists.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_describe_not_found()), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=None):
        response = await _get("omex-verification-unreachable", None)

    assert response.status_code == 503
    assert "storage is unavailable" in response.json()["detail"]
    assert response.headers["Retry-After"] == "5"


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


def _temporal_terminated(
    *, with_computed: bool, converter: Any = None, computed_override: Any = None,
    owner_sub: str | None = None,
) -> Any:
    """A Temporal client whose workflow is TERMINATED and whose query fails.

    ``with_computed`` controls whether history contains the persist activity's
    scheduled event, which is the only proof that a result was ever computed.

    ``converter`` selects the payload converter used to write and read the
    history. Production connects without one (``dependencies.py``), so it uses
    ``DataConverter.default``; the tests default to the pydantic converter they
    have always used, and are parametrized over both where it matters.

    ``computed_override`` replaces the decoded output, so a test can present a
    payload that does not belong to this execution.

    ``owner_sub`` is written into the start input *and* the computed output, the
    way the real workflow copies ``verify_input.owner_sub`` into its result, so
    the two agree unless a test deliberately overrides one.
    """
    from temporalio.api.history.v1 import HistoryEvent
    from temporalio.client import WorkflowExecutionStatus, WorkflowQueryFailedError

    from biosim_server.common.temporal import pydantic_data_converter

    data_converter = converter if converter is not None else pydantic_data_converter

    # Deliberately unlike the rebuilt shell in three ways the test can assert on:
    # it is COMPLETED (the shell is FAILED), it carries results (the shell has
    # none), and its timestamp is the workflow's own rather than the close time.
    computed = _output(
        workflow_id="omex-verification-terminated",
        status=VerifyWorkflowStatus.COMPLETED,
        owner_sub=owner_sub,
    )
    computed.timestamp = "2025-01-01T00:00:00Z"
    computed.workflow_run_id = "run-1"
    computed.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[], comparison_statistics={"shared": []})
    payload_output = computed_override if computed_override is not None else computed

    def _events(*_args: Any, **_kwargs: Any) -> Any:
        # fetch_history_events is a sync method returning an async iterator, so
        # this must be a plain def yielding from an async generator.
        return _history(with_computed)

    async def _history(with_computed: bool) -> Any:
        # The recovery path reads the start event first, so history always has it.
        start = HistoryEvent()
        start.workflow_execution_started_event_attributes.workflow_type.name = "OmexVerifyWorkflow"
        start.workflow_execution_started_event_attributes.input.payloads.extend(
            await data_converter.encode([
                OmexVerifyWorkflowInput(
                    omex_file=OmexFile(file_hash_md5="hash", uploaded_filename="m.omex",
                                       bucket_name="test", omex_gcs_path="m.omex", file_size=1),
                    requested_simulators=[], compare_settings=computed.compare_settings,
                    owner_sub=owner_sub, cache_buster="0")]))
        yield start
        if with_computed:
            payloads = await data_converter.encode([payload_output])
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
    temporal.data_converter = data_converter
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


# ---------------------------------------------------------------------------
# Fix 3: the recovered output must belong to the execution it came from
# ---------------------------------------------------------------------------


def _mismatched(workflow_id: str | None = None, owner_sub: str | None = None) -> Any:
    """A computed result that differs from the shell in identity only."""
    other = _output(
        workflow_id=workflow_id or "omex-verification-terminated",
        status=VerifyWorkflowStatus.COMPLETED,
        owner_sub=owner_sub,
    )
    other.timestamp = "2025-01-01T00:00:00Z"
    other.workflow_run_id = "run-1"
    other.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[], comparison_statistics={"shared": []})
    return other


@pytest.mark.asyncio
async def test_computed_result_for_another_workflow_is_not_served_or_stored() -> None:
    """A payload from a different workflow id is no proof, so nothing is written.

    The caller was authorized against the shell rebuilt from this execution's
    start input. Serving and storing another execution's output would put it in
    the write-once slot under the wrong identity, permanently.
    """
    store = _store_returning(None)
    temporal = _temporal_terminated(
        with_computed=True, computed_override=_mismatched(workflow_id="omex-verification-other"))
    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    # The rebuilt shell is served: FAILED, no results, and this execution's id.
    body = response.json()
    assert body["workflow_id"] == "omex-verification-terminated"
    assert body["workflow_status"] == "FAILED"
    assert body["workflow_results"] is None
    store.save.assert_not_awaited()
    # A MISMATCH is read from a history that never changes, so every later GET
    # serves the same shell: its FAILED status is a settled answer, recorded once,
    # and under this execution's id rather than the mismatched payload's.
    ledger.record_terminal_status.assert_awaited_once_with(
        "omex-verification-terminated", VerifyWorkflowStatus.FAILED)


@pytest.mark.asyncio
async def test_computed_result_with_another_run_id_is_not_served_or_stored() -> None:
    """A payload from a different run of the same workflow id is also no proof."""
    store = _store_returning(None)
    other_run = _mismatched()
    other_run.workflow_run_id = "run-2"
    temporal = _temporal_terminated(with_computed=True, computed_override=other_run)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_computed_result_for_another_owner_is_not_served_or_stored() -> None:
    """A payload owned by someone else must not be stored under this identity.

    The owner_sub is what the store's ownership check reads, so writing a report
    carrying another user's sub would hand them a report they never asked for and
    hide it from the caller who did.
    """
    store = _store_returning(None)
    # The start input (which the caller was authorized against) is ownerless, so
    # the caller is allowed through; the payload in history claims an owner.
    temporal = _temporal_terminated(
        with_computed=True, owner_sub=None,
        computed_override=_mismatched(owner_sub="auth0|someone-else"))
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_computed_result_is_served_and_stored_for_its_owner() -> None:
    """The happy path for an owned verification: owner reads it, result is stored.

    This is the test that pins the owner argument. The start input and the
    computed output share an owner, and the caller is that owner, so the identity
    check must pass: the response is the COMPLETED result and it is saved once.

    If the call site stopped passing ``recovered.owner_sub`` and the parameter
    defaulted to None, the owner comparison would see ``auth0|owner`` against
    None and reject the real result -- serving the FAILED shell and writing
    nothing. That is what makes this test fail when the argument is dropped.
    """
    store = _store_returning(None)
    owner = AuthenticatedUser(sub="auth0|owner", email="o@e.com")
    temporal = _temporal_terminated(with_computed=True, owner_sub="auth0|owner")
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", owner)

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == "COMPLETED"
    assert body["workflow_results"] is not None
    assert body["owner_sub"] == "auth0|owner"
    store.save.assert_awaited_once()
    saved = store.save.await_args.args[0]
    assert saved.owner_sub == "auth0|owner"
    assert saved.workflow_status == VerifyWorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# Fix 4: the recovery scan must work with the converter production uses
# ---------------------------------------------------------------------------


def _production_converter() -> Any:
    """The converter production actually uses: connect() passes none, so default."""
    from temporalio.converter import DataConverter
    return DataConverter.default


@pytest.mark.asyncio
@pytest.mark.parametrize("converter_name", ["pydantic", "default"])
async def test_recovery_scan_works_with_either_data_converter(converter_name: str) -> None:
    """The scan must decode history with the converter production configured.

    Production calls TemporalClient.connect() with no converter, so it uses
    DataConverter.default, while these tests have always used the pydantic one.
    A payload format change in either would otherwise break only production.
    """
    from biosim_server.common.temporal import pydantic_data_converter

    converter = pydantic_data_converter if converter_name == "pydantic" else _production_converter()
    store = _store_returning(None)
    temporal = _temporal_terminated(with_computed=True, converter=converter)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=MagicMock()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == "COMPLETED"
    assert body["workflow_results"] is not None
    store.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fix 5: a history scan is bounded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_scan_stops_at_the_event_limit() -> None:
    """Past the event cap the scan counts as failed: shell served, nothing stored.

    The cap only protects GET latency on a pathological history. Treating it as
    "no proof" is the safe answer, since the store is write-once -- and the same
    reasoning applies to the ledger status, which must not record a guess.
    """
    from biosim_server.api import main as api_main

    store = _store_returning(None)
    temporal = _temporal_terminated(with_computed=True)
    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger), \
         patch.object(api_main, "_HISTORY_SCAN_EVENT_LIMIT", 1):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    # The limit of 1 is consumed by the start event, so the computed result is
    # never reached and the shell is what comes back.
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()
    # Running past the cap is a failed scan, so the shell's FAILED status is a
    # guess and must not be frozen into the ledger.
    ledger.record_terminal_status.assert_not_awaited()


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

    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    store.save.assert_not_awaited()
    # The ledger status is a second irreversible write, and a failed scan cannot
    # tell whether a comparison finished. Recording the shell's FAILED status
    # would freeze a guess in the listing, where nothing retries it.
    ledger.record_terminal_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_scan_records_the_terminal_status() -> None:
    """A scan that succeeded does record the status, so the guard is not a blanket skip."""
    store = _store_returning(None)
    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated(with_computed=False)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    ledger.record_terminal_status.assert_awaited_once()
    recorded = ledger.record_terminal_status.await_args.args
    assert recorded[0] == "omex-verification-terminated"
    assert recorded[1] == VerifyWorkflowStatus.FAILED


# ---------------------------------------------------------------------------
# Fix 6: a store outage costs the GET one bounded wait, not three
# ---------------------------------------------------------------------------


async def _hang(*_args: Any, **_kwargs: Any) -> Any:
    """Never return, like a Motor call to an unreachable Mongo before its 30 s timeout.

    A store that raises at once would pass these tests with or without the
    timeout; only a call that hangs shows whether the GET waits it out.
    """
    await asyncio.Event().wait()


async def _get_within(seconds: float, workflow_id: str, user: AuthenticatedUser | None) -> Any:
    """``_get``, but fail the test instead of hanging if the endpoint never answers."""
    return await asyncio.wait_for(_get(workflow_id, user), timeout=seconds)


def _ledger(
    owner_sub: str | None = None, *, row: Any = "default", raises: Exception | None = None,
) -> Any:
    """A ledger mock whose get_verification answers with a row owned by *owner_sub*.

    ``row=None`` means the ledger has no record of the verification. ``raises``
    makes the lookup fail, which is the "ownership evidence unavailable" case.
    """
    ledger = MagicMock()
    ledger.record_terminal_status = AsyncMock()
    if raises is not None:
        ledger.get_verification = AsyncMock(side_effect=raises)
    elif row is None:
        ledger.get_verification = AsyncMock(return_value=None)
    else:
        record = MagicMock()
        record.owner_sub = owner_sub
        ledger.get_verification = AsyncMock(return_value=record)
    return ledger


@pytest.mark.asyncio
async def test_hanging_store_read_falls_back_to_temporal_within_the_timeout() -> None:
    """A store read that never returns is cut off and treated as an outage.

    Temporal still answers, the caller gets the report, and the catch-up save is
    not attempted: the store has just failed to answer, so a second call would
    only spend a second timeout on the same request.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=_hang)
    store.save = AsyncMock(return_value=True)
    finished = _output(workflow_id="omex-verification-store-hangs")
    ledger = _ledger()
    with patch("biosim_server.api.main._STORE_CALL_TIMEOUT_SECONDS", 0.05), \
         patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_owning(finished)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get_within(
            5, finished.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    assert response.json()["workflow_id"] == finished.workflow_id
    store.save.assert_not_awaited()
    ledger.record_terminal_status.assert_awaited_once_with(finished.workflow_id, finished.workflow_status)


@pytest.mark.asyncio
async def test_hanging_catch_up_save_does_not_hold_the_get() -> None:
    """The read answered "nothing stored", then the save hangs: the save is cut off.

    The report is still served and the ledger status still recorded; the next
    GET finds no stored report and tries the save again.
    """
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock(side_effect=_hang)
    finished = _output(workflow_id="omex-verification-save-hangs")
    ledger = _ledger()
    with patch("biosim_server.api.main._STORE_CALL_TIMEOUT_SECONDS", 0.05), \
         patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_owning(finished)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get_within(
            5, finished.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    store.save.assert_awaited_once()
    ledger.record_terminal_status.assert_awaited_once_with(finished.workflow_id, finished.workflow_status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("mongo is down"), ReportCorrupt("payload is garbage")],
    ids=["unavailable", "corrupt"],
)
async def test_failed_store_read_skips_the_catch_up_save(failure: Exception) -> None:
    """Only a confirmed miss justifies a write.

    After an outage the save would wait on the same unreachable store; after a
    corrupt read a document already exists, so the write-once save could only
    return False. Either way the report is served from Temporal.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=failure)
    store.save = AsyncMock(return_value=True)
    finished = _output(workflow_id="omex-verification-read-failed")
    ledger = _ledger()
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=_temporal_owning(finished)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        response = await _get(finished.workflow_id, AuthenticatedUser(sub="auth0|owner", email="o@e.com"))

    assert response.status_code == 200
    store.save.assert_not_awaited()
    # Only the report write is skipped; the listing still learns the status.
    ledger.record_terminal_status.assert_awaited_once_with(finished.workflow_id, finished.workflow_status)


@pytest.mark.asyncio
async def test_store_outage_skips_the_save_on_the_recovery_path_too() -> None:
    """The recovery path passes its store read on, so an outage skips its save as well.

    The scan finds the computed result and serves it, but does not try to store
    it while the store is unreachable; a later GET can.
    """
    store = MagicMock()
    store.get = AsyncMock(side_effect=RuntimeError("mongo is down"))
    store.save = AsyncMock(return_value=True)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated(with_computed=True)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=_ledger()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    assert response.json()["workflow_results"] is not None
    store.save.assert_not_awaited()


# ---------------------------------------------------------------------------
# Fix 1: a reconciled shell must not claim the write-once slot
# ---------------------------------------------------------------------------


def _temporal_terminated_with_stuck_query(
    *, status: VerifyWorkflowStatus, with_results: bool,
) -> Any:
    """A TERMINATED execution whose query *answers*, with a non-terminal status.

    This is the case the recovery scan never sees: the query succeeded, so no
    history scan runs. The workflow was terminated before it reached an exit
    point, so its own output still says IN_PROGRESS and carries no result.
    """
    from temporalio.client import WorkflowExecutionStatus

    stuck = _output(workflow_id="omex-verification-stuck", status=status, owner_sub=None)
    stuck.workflow_run_id = "run-1"
    if with_results:
        stuck.workflow_results = GenerateStatisticsActivityOutput(
            sims_run_info=[], comparison_statistics={"shared": []})

    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(
        workflow_type="OmexVerifyWorkflow", status=WorkflowExecutionStatus.TERMINATED,
        id=stuck.workflow_id, run_id="run-1",
        close_time=datetime(2025, 1, 1, tzinfo=UTC),
        start_time=datetime(2025, 1, 1, tzinfo=UTC)))
    handle.query = AsyncMock(return_value=stuck)

    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    return temporal


@pytest.mark.asyncio
async def test_reconciled_shell_does_not_claim_the_write_once_slot() -> None:
    """A terminated execution whose query said IN_PROGRESS is served FAILED, not stored.

    The FAILED status is synthesized by _reconcile_terminal_status, not computed by
    the workflow. Storing it would occupy the write-once slot with a permanent
    FAILED report and make the real result unstorable forever.
    """
    store = _store_returning(None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated_with_stuck_query(
                   status=VerifyWorkflowStatus.IN_PROGRESS, with_results=False)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=_ledger()):
        response = await _get("omex-verification-stuck", None)

    assert response.status_code == 200
    # The caller still sees a terminal status, so it stops polling.
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_produced_terminal_failure_with_no_statistics_is_still_saved() -> None:
    """A real FAILED exit point with empty statistics is a computed result and is stored.

    This is the other half of fix 1: the guard must not become "never save a FAILED
    report", which would lose the genuine failures the workflow itself recorded.
    """
    store = _store_returning(None)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client",
               return_value=_temporal_terminated_with_stuck_query(
                   status=VerifyWorkflowStatus.FAILED, with_results=False)), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=_ledger()):
        response = await _get("omex-verification-stuck", None)

    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_awaited_once()
    saved = store.save.await_args.args[0]
    assert saved.workflow_status == VerifyWorkflowStatus.FAILED


# ---------------------------------------------------------------------------
# Fix 3: a recovered result must be this run's, and terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_computed_result_without_a_run_id_is_not_served_or_stored() -> None:
    """A payload with no run id cannot be proven to be this run's, so it is rejected.

    An earlier version accepted a missing payload run id whenever the execution
    description supplied one, which let another run's payload through.
    """
    store = _store_returning(None)
    no_run = _mismatched()
    no_run.workflow_run_id = None
    temporal = _temporal_terminated(with_computed=True, computed_override=no_run)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=_ledger()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonterminal_computed_result_is_not_served_or_stored() -> None:
    """The persist activity runs only at an exit point, so a non-terminal payload is not a result.

    Accepting one would let a mid-flight snapshot claim the write-once slot.
    """
    store = _store_returning(None)
    nonterminal = _mismatched()
    nonterminal.workflow_status = VerifyWorkflowStatus.IN_PROGRESS
    temporal = _temporal_terminated(with_computed=True, computed_override=nonterminal)
    with patch("biosim_server.api.main.get_verification_report_store", return_value=store), \
         patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main.get_verification_database_service", return_value=_ledger()):
        response = await _get("omex-verification-terminated", None)

    assert response.status_code == 200
    assert response.json()["workflow_status"] == "FAILED"
    store.save.assert_not_awaited()
