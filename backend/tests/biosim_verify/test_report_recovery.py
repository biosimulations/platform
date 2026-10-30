from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.biosim_verify.models import (
    CompareSettings,
    VerificationProvenance,
    VerificationRecord,
    VerificationReportEnvelope,
    VerificationType,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.report_recovery import process_record

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


def _record() -> VerificationRecord:
    return VerificationRecord(
        workflow_id="wf",
        workflow_run_id="run",
        verify_type=VerificationType.RUNS,
        owner_sub="owner",
        created=datetime.now(UTC),
        workflow_input={"compare_settings": {
            "user_description": "x", "include_outputs": False,
            "rel_tol": 0.1, "abs_tol_min": 0.1, "abs_tol_scale": 0.1,
        }},
    )


def _output() -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id="wf", workflow_run_id="run", owner_sub="owner",
        compare_settings=CompareSettings(
            user_description="x", include_outputs=False,
            rel_tol=0.1, abs_tol_min=0.1, abs_tol_scale=0.1,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED, timestamp=datetime.now(UTC).isoformat(),
    )


@pytest.mark.asyncio
async def test_completed_recovery_uses_pinned_result_not_query() -> None:
    record = _record()
    ledger = AsyncMock()
    store = AsyncMock()
    store.get.return_value = None
    store.save.return_value = VerificationReportEnvelope(
        report_id="wf", workflow_id="wf", workflow_run_id="run", owner_sub="owner",
        workflow_status=VerifyWorkflowStatus.COMPLETED, computed_at=datetime.now(UTC),
        saved_at=datetime.now(UTC), content_sha256="x", encoding="x", uncompressed_bytes=1,
        provenance=VerificationProvenance(
            verification_id="wf", workflow_run_id="run", owner_sub="owner",
        ),
    )
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(status=WorkflowExecutionStatus.COMPLETED, run_id="run"))
    handle.result = AsyncMock(return_value=_output())
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    assert await process_record(record=record, ledger=ledger, store=store, temporal=temporal, apply=True) == "saved"
    handle.result.assert_awaited_once()
    assert not hasattr(handle, "query") or not handle.query.called


@pytest.mark.asyncio
async def test_missing_history_marks_unavailable_without_starting_work() -> None:
    record = _record()
    ledger = AsyncMock()
    store = AsyncMock()
    store.get.return_value = None
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("gone", RPCStatusCode.NOT_FOUND, b""))
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    assert await process_record(record=record, ledger=ledger, store=store, temporal=temporal, apply=True) == "missing_history"
    ledger.mark_persistence_unavailable.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_is_failed_not_missing_simulation() -> None:
    record, ledger, store = _record(), AsyncMock(), AsyncMock()
    store.get.return_value = None
    store.save.return_value.report_id = 'wf'
    handle = MagicMock()
    handle.describe = AsyncMock(return_value=MagicMock(status=WorkflowExecutionStatus.CANCELED, run_id='run'))
    temporal = MagicMock()
    temporal.get_workflow_handle.return_value = handle
    assert await process_record(record=record, ledger=ledger, store=store, temporal=temporal, apply=True) == 'saved'
    assert store.save.await_args.args[0].workflow_status == VerifyWorkflowStatus.FAILED
    assert store.save.await_args.args[1].origin == 'terminal_execution_failure'


@pytest.mark.asyncio
async def test_database_failure_is_not_missing_history() -> None:
    ledger, store, temporal = AsyncMock(), AsyncMock(), MagicMock()
    store.get.side_effect = RuntimeError('database unavailable')
    assert await process_record(record=_record(), ledger=ledger, store=store, temporal=temporal, apply=True) == 'dependency_error'
    ledger.mark_persistence_unavailable.assert_not_awaited()
    temporal.get_workflow_handle.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_dryrun_checkpoint_cannot_skip_apply(tmp_path: object) -> None:
    from pathlib import Path
    from unittest.mock import patch
    from scripts.backfill_verification_reports import run
    checkpoint = Path(str(tmp_path)) / 'checkpoint.json'
    ledger, store = AsyncMock(), AsyncMock()
    ledger.list_persistence_candidates.return_value = [_record()]
    with patch('scripts.backfill_verification_reports.get_verification_database_service', return_value=ledger), \
         patch('scripts.backfill_verification_reports.get_verification_report_store', return_value=store), \
         patch('scripts.backfill_verification_reports.get_temporal_client', return_value=MagicMock()), \
         patch('scripts.backfill_verification_reports.process_record', new=AsyncMock(return_value='pending')):
        await run(checkpoint=checkpoint, apply=False, limit=10)
        import json
        assert json.loads(checkpoint.read_text())['last_created'] is None
        with pytest.raises(ValueError, match='separate checkpoint'):
            await run(checkpoint=checkpoint, apply=True, limit=10)
    store.save.assert_not_awaited()



def test_failure_record_treats_naive_mongo_time_as_utc() -> None:
    from biosim_server.biosim_verify.report_recovery import _output_from_record
    record = _record().model_copy(update={"created": datetime(2026, 10, 29, 12)})
    output = _output_from_record(record, VerifyWorkflowStatus.FAILED, "execution terminated")
    assert output.timestamp == "2026-10-29T12:00:00+00:00"
