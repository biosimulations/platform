"""The save activity's retry contract.

The distinction these tests pin is the one Temporal acts on: a report that is
not finished violates the store's contract, so no amount of retrying can make
it storable and the activity must fail with ``non_retryable=True``. A storage
failure is the opposite -- the workflow's retry policy is the only recovery
path -- so it must propagate as an ordinary error.
"""

from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from biosim_server.biosim_verify import activities
from biosim_server.biosim_verify.models import CompareSettings, VerifyWorkflowOutput, VerifyWorkflowStatus


def _report(status: VerifyWorkflowStatus) -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id="wf-save-activity",
        compare_settings=CompareSettings(user_description="", include_outputs=False,
                                         rel_tol=1e-6, abs_tol_min=1e-9, abs_tol_scale=1.0),
        workflow_status=status,
        timestamp="2026-11-18T00:00:00Z",
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace the process-wide store handle with a mock the test controls."""
    mock = AsyncMock()
    monkeypatch.setattr(activities, "get_verification_report_store", lambda: mock)
    return mock


@pytest.mark.asyncio
async def test_finished_report_is_saved(store: AsyncMock) -> None:
    store.save.return_value = True
    output = _report(VerifyWorkflowStatus.COMPLETED)

    assert await ActivityEnvironment().run(activities.save_verification_report_activity, output) is True
    store.save.assert_awaited_once_with(output)


@pytest.mark.asyncio
async def test_a_second_save_reports_that_the_report_already_exists(store: AsyncMock) -> None:
    """The store's write-once False is a result, not an error."""
    store.save.return_value = False

    assert await ActivityEnvironment().run(activities.save_verification_report_activity,
                                           _report(VerifyWorkflowStatus.FAILED)) is False


@pytest.mark.asyncio
async def test_unfinished_report_is_non_retryable(store: AsyncMock) -> None:
    """The store's contract guard becomes a failure Temporal will not retry."""
    store.save.side_effect = ValueError("Only a finished verification can be stored")

    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(activities.save_verification_report_activity,
                                        _report(VerifyWorkflowStatus.IN_PROGRESS))

    assert excinfo.value.non_retryable is True
    assert str(excinfo.value) == "Only a finished verification can be stored"
    store.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_store_is_non_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker that never initialized the store cannot be fixed by retrying."""
    monkeypatch.setattr(activities, "get_verification_report_store", lambda: None)

    with pytest.raises(ApplicationError) as excinfo:
        await ActivityEnvironment().run(activities.save_verification_report_activity,
                                        _report(VerifyWorkflowStatus.COMPLETED))

    assert excinfo.value.non_retryable is True


@pytest.mark.asyncio
async def test_storage_failure_stays_retryable(store: AsyncMock) -> None:
    """A transient database error must reach Temporal as an ordinary exception."""
    store.save.side_effect = RuntimeError("mongo unavailable")

    with pytest.raises(RuntimeError) as excinfo:
        await ActivityEnvironment().run(activities.save_verification_report_activity,
                                        _report(VerifyWorkflowStatus.COMPLETED))

    assert not isinstance(excinfo.value, ApplicationError)
