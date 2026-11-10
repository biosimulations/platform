"""Recovery offers are computed on demand, never on a progress poll (S-1, D3)."""
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio.client import WorkflowExecutionStatus

from biosim_server.biosim_verify.models import (
    CompareSettings,
    VerificationRecord,
    VerificationType,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.common.auth import AuthenticatedUser
from biosim_server.dependencies import set_omex_database_service
from tests.api.test_main import _temporal_with_describe
from tests.biosim_verify.test_linked_recovery import _archive_source, _as_user, _client, recovery_world  # noqa: F401
from tests.biosim_verify.test_recovery_admission import _spy_storage

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

OWNER = AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
OTHER = AuthenticatedUser(sub="auth0|other", email="other@example.test")
ADMIN = AuthenticatedUser(sub="auth0|admin", email="admin@example.test", roles=["admin"])


@pytest.mark.asyncio
async def test_a_report_response_never_carries_recovery(recovery_world: Any) -> None:  # noqa: F811
    await _archive_source(recovery_world, "source-plain")
    async with await _client() as client:
        response = await client.get("/verify/source-plain")
    assert response.status_code == 200
    assert response.json()["workflow_status"] == "COMPLETED"
    assert response.json()["recovery"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, caller, visible", [
    (None, None, True),          # public: anyone
    (None, OTHER, True),
    ("auth0|owner", None, False),  # private: hidden from anonymous callers and other users
    ("auth0|owner", OTHER, False),
    ("auth0|owner", OWNER, True),
    ("auth0|owner", ADMIN, True),
])
async def test_recovery_offers_follow_get_visibility(
    recovery_world: Any, owner: str | None, caller: AuthenticatedUser | None, visible: bool,  # noqa: F811
) -> None:
    await _archive_source(recovery_world, "source-offers", owner=owner)
    try:
        _as_user(caller)
        async with await _client() as client:
            response = await client.get("/verify/source-offers/recovery")
    finally:
        _as_user(None)
    if visible:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["reconstruct"]["eligible"] is True
        assert set(response.json()) == {"reconstruct", "reverify_archive"}
    else:
        assert response.status_code == 404
        assert response.json() == {"detail": "Verification not found: source-offers"}  # no existence leak


@pytest.mark.asyncio
async def test_an_unknown_verification_has_no_offers(recovery_world: Any) -> None:  # noqa: F811
    async with await _client() as client:
        response = await client.get("/verify/never-existed/recovery")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_running_poll_reads_no_provenance_archive_or_storage(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    settings = CompareSettings(user_description="x", include_outputs=False, rel_tol=1e-4, abs_tol_min=1e-3,
                               abs_tol_scale=1e-5)
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="running-wf", verify_type=VerificationType.OMEX, created=datetime.now(UTC), omex_hash="a" * 32,
    ))
    progress = VerifyWorkflowOutput(workflow_id="running-wf", workflow_run_id="run", compare_settings=settings,
                                    workflow_status=VerifyWorkflowStatus.IN_PROGRESS, timestamp="2026-11-02")
    temporal = _temporal_with_describe(workflow_type="OmexVerifyWorkflow",
                                       exec_status=WorkflowExecutionStatus.RUNNING, query_result=progress)
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal)
    storage = _spy_storage(recovery_world.files, monkeypatch)
    provenance = AsyncMock(side_effect=AssertionError("provenance read on a poll"))
    monkeypatch.setattr(recovery_world.store, "get_provenance", provenance)
    omex_db = AsyncMock()
    set_omex_database_service(omex_db)
    derived = AsyncMock(side_effect=AssertionError("derived links read on a poll"))
    monkeypatch.setattr(recovery_world.ledger, "list_derived", derived)
    async with await _client() as client:
        for _ in range(3):
            response = await client.get("/verify/running-wf")
            assert response.status_code == 200
            assert response.json()["workflow_status"] == "IN_PROGRESS"
    assert storage == []
    provenance.assert_not_awaited()
    assert omex_db.mock_calls == []
    derived.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_finished_report_reads_its_ledger_row_once(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    await _archive_source(recovery_world, "source-once")
    reads = AsyncMock(wraps=recovery_world.ledger.get_verification)
    monkeypatch.setattr(recovery_world.ledger, "get_verification", reads)
    omex_db = AsyncMock()
    set_omex_database_service(omex_db)
    async with await _client() as client:
        response = await client.get("/verify/source-once")
    assert response.status_code == 200
    assert reads.await_count == 1
    assert omex_db.mock_calls == []  # the archive is checked only when offers are requested
