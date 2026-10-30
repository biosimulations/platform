"""Operational pagination, alarms, and backup integrity without live provider calls."""
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest

from biosim_server.biosim_verify.report_recovery import inventory_retained_executions
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
from tests.biosim_verify.test_report_recovery import _record, _output
from tests.biosim_verify.test_report_storage import _provenance
from biosim_server.biosim_verify.report_backup import export_bundle, restore_bundle
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.common.storage.file_service_local import FileServiceLocal
from scripts.backfill_verification_reports import run

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@pytest.mark.asyncio
async def test_temporal_inventory_resumes_page_tokens_and_quarantines_unknown_owner() -> None:
    ledger, store, temporal = AsyncMock(), AsyncMock(), MagicMock()
    ledger.get_verification.return_value = None
    page = SimpleNamespace(current_page=[SimpleNamespace(id='wf', run_id='run', workflow_type='RunsVerifyWorkflow',
        start_time=datetime.now(UTC))], next_page_token=b'next', fetch_next_page=AsyncMock())
    temporal.list_workflows.return_value = page
    # Omitted owner differs from an explicitly public owner=None.
    original = RunsVerifyWorkflowInput(biosimulations_run_ids=['run'], compare_settings=_output().compare_settings)
    with patch('biosim_server.biosim_verify.report_recovery.read_start_input', new=AsyncMock(return_value=original)):
        counts, token = await inventory_retained_executions(temporal=temporal, ledger=ledger, store=store,
            apply=True, limit=1, next_page_token=b'previous')
    assert counts == {'unknown_ownership': 1} and token == b'next'
    assert temporal.list_workflows.call_args.kwargs['next_page_token'] == b'previous'
    ledger.insert_verification.assert_not_awaited()
    store.save.assert_not_awaited()
    original.owner_sub = None
    with patch('biosim_server.biosim_verify.report_recovery.read_start_input', new=AsyncMock(return_value=original)), \
         patch('biosim_server.biosim_verify.report_recovery.process_record', new=AsyncMock(return_value='saved')):
        await inventory_retained_executions(temporal=temporal, ledger=ledger, store=store, apply=True)
    ledger.insert_verification.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_both_cursors_time_bounds_and_late_closure(tmp_path: Path) -> None:
    checkpoint = tmp_path / 'checkpoint.json'
    ledger, store, temporal = AsyncMock(), AsyncMock(), MagicMock()
    first, second = _record(), _record().model_copy(update={'workflow_id': 'wf2'})
    ledger.list_persistence_candidates.side_effect = [[first], [second], [], []]
    inventory = AsyncMock(side_effect=[({'pending': 1}, b'page2'), ({'saved': 1}, None), ({'saved': 1}, None)])
    with patch('scripts.backfill_verification_reports.get_verification_database_service', return_value=ledger), \
         patch('scripts.backfill_verification_reports.get_verification_report_store', return_value=store), \
         patch('scripts.backfill_verification_reports.get_temporal_client', return_value=temporal), \
         patch('scripts.backfill_verification_reports.inventory_retained_executions', new=inventory), \
         patch('scripts.backfill_verification_reports.process_record', new=AsyncMock(side_effect=['pending', 'saved'])):
        one = await run(checkpoint=checkpoint, apply=True, inventory=True, limit=1)
        assert not one['complete'] and one['inventory_token'] == b'page2'.hex()
        two = await run(checkpoint=checkpoint, apply=True, inventory=True, limit=1)
        assert not two['complete']
        assert inventory.await_args is not None
        assert inventory.await_args.kwargs['next_page_token'] == b'page2'
        assert ledger.list_persistence_candidates.await_args.args[2] == first.workflow_id
        assert inventory.await_args.kwargs['until'] == datetime.fromisoformat(one['until'])
        three = await run(checkpoint=checkpoint, apply=True, inventory=True, limit=1)
        assert three['complete'] and three['outcomes']['saved'] == 2
        # A fresh pass inventories earlier executions again, including a late close.
        four = await run(checkpoint=checkpoint, apply=True, inventory=True, limit=1)
        assert four['complete'] and four['outcomes']['saved'] == 1


@pytest.mark.asyncio
async def test_reconcile_alarms_without_exposing_private_request(caplog: pytest.LogCaptureFixture) -> None:
    from scripts.reconcile_verification_reports import reconcile
    ledger, store = AsyncMock(), AsyncMock()
    old = _record().model_copy(update={'created': datetime.now(UTC) - timedelta(hours=2)})
    ledger.list_persistence_candidates.return_value = [old]
    ledger.acquire_persistence_scan.return_value = (None, None)
    with patch('scripts.reconcile_verification_reports.get_verification_database_service', return_value=ledger), \
         patch('scripts.reconcile_verification_reports.get_verification_report_store', return_value=store), \
         patch('scripts.reconcile_verification_reports.get_temporal_client', return_value=None), \
         patch('scripts.reconcile_verification_reports.process_record', new=AsyncMock(return_value='pending')), \
         caplog.at_level(logging.ERROR):
        counts = await reconcile(100, True)
    assert counts['stale_pending'] == 1
    event = json.loads(caplog.records[-1].message)
    assert event['event'] == 'verification_persistence_attention'
    assert 'owner' not in caplog.text and 'workflow_input' not in caplog.text


@pytest.mark.asyncio
async def test_backup_corruption_refuses_all_destination_writes(mongo_test_client: Any, tmp_path: Path) -> None:
    store = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / 'objects'))
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await store.ensure_indexes()
    await ledger.ensure_indexes()
    output = _output()
    await store.save(output, _provenance(output))
    bundle = tmp_path / 'bundle'
    manifest = await export_bundle(bundle, store, ledger)
    name = manifest['reports'][0]
    (bundle / name).write_bytes(b'broken')
    destination = MagicMock()
    with pytest.raises(ValueError, match='checksum'):
        await restore_bundle(bundle, destination, ledger, apply=True)
    destination.ensure_indexes.assert_not_called()
    await store.delete_all()


@pytest.mark.asyncio
async def test_persistence_failure_records_blocked_state(mongo_test_client: Any, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from temporalio.testing import ActivityEnvironment
    from temporalio.exceptions import ApplicationError
    from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput, persist_verification_report_activity
    store = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / 'objects'))
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    await store.ensure_indexes()
    row = _record()
    await ledger.insert_verification(row)
    output = _output()
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_verification_report_store', lambda: store)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_verification_database_service', lambda: ledger)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_mongo_client', lambda: mongo_test_client)
    store._settings.verification_report_max_decompressed_bytes = 1
    with pytest.raises(ApplicationError, match='integrity/encoding'):
        await ActivityEnvironment().run(persist_verification_report_activity,
            PersistVerificationReportActivityInput(output=output, provenance=_provenance(output)))
    persisted = await ledger.get_verification(row.workflow_id)
    assert persisted is not None and persisted.persistence_state == 'blocked'
    assert await store.get(row.workflow_id) is None
    await ledger.delete_all_verifications()


@pytest.mark.asyncio
async def test_storage_outage_cannot_acknowledge_and_retry_needs_no_get(
    mongo_test_client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio.testing import ActivityEnvironment
    from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput, persist_verification_report_activity
    store = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / 'objects'))
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    await store.ensure_indexes()
    await ledger.insert_verification(_record())
    output = _output()
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_verification_report_store', lambda: store)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_verification_database_service', lambda: ledger)
    monkeypatch.setattr('biosim_server.biosim_verify.activities.get_mongo_client', lambda: mongo_test_client)
    payload = PersistVerificationReportActivityInput(output=output, provenance=_provenance(output))
    with patch.object(store, 'save', new=AsyncMock(side_effect=OSError('storage offline'))):
        with pytest.raises(OSError):
            await ActivityEnvironment().run(persist_verification_report_activity, payload)
    row = await ledger.get_verification(output.workflow_id)
    assert row is not None and row.persistence_state == 'pending'
    assert await store.get(output.workflow_id) is None
    acknowledged = await ActivityEnvironment().run(persist_verification_report_activity, payload)
    assert acknowledged.persistence_state == 'stored'
    row = await ledger.get_verification(output.workflow_id)
    assert row is not None and row.persistence_state == 'stored' and row.persistence_error is None
    await store.delete_all()
    await ledger.delete_all_verifications()


@pytest.mark.asyncio
async def test_scheduled_reconciliation_resumes_and_fences_cursor(mongo_test_client: Any) -> None:
    from scripts.reconcile_verification_reports import reconcile
    ledger = VerificationDatabaseServiceMongo(mongo_test_client)
    await ledger.ensure_indexes()
    for index in range(3):
        await ledger.insert_verification(_record().model_copy(update={'workflow_id': f'page-{index}'}))
    visited = []

    async def pending(**kwargs: Any) -> str:
        visited.append(kwargs['record'].workflow_id)
        return 'pending'

    with patch('scripts.reconcile_verification_reports.get_verification_database_service', return_value=ledger), \
         patch('scripts.reconcile_verification_reports.get_verification_report_store', return_value=MagicMock()), \
         patch('scripts.reconcile_verification_reports.process_record', new=pending):
        for _ in range(3):
            result = await reconcile(2, True, max_records=1)
            assert result['scanned'] == 1
        assert visited == ['page-0', 'page-1', 'page-2']
        await reconcile(2, True)
        await reconcile(2, True, max_records=1)
        assert visited[-1] == 'page-0'
    assert await ledger.acquire_persistence_scan('fencing-test', 'owner1') == (None, None)
    assert await ledger.acquire_persistence_scan('fencing-test', 'owner2') is None
    with pytest.raises(RuntimeError, match='lease was lost'):
        await ledger.checkpoint_persistence_scan('fencing-test', 'owner2', None, None, release=True)
    await ledger.checkpoint_persistence_scan('fencing-test', 'owner1', None, None, release=True)
    await ledger.delete_all_verifications()
