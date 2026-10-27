"""Pure-unit tests for _reconcile_terminal_status (D1 fix in main.py).

Tests 7-9 from the plan: IN_PROGRESS + closed Temporal status → FAILED;
IN_PROGRESS + RUNNING → unchanged; already-terminal output → unchanged.
"""

import pytest
from temporalio.client import WorkflowExecutionStatus

from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.api.main import _reconcile_terminal_status


def _output(status: VerifyWorkflowStatus) -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id="wf-test",
        compare_settings=CompareSettings(
            user_description="t",
            include_outputs=False,
            rel_tol=1e-4,
            abs_tol_min=1e-3,
            abs_tol_scale=1e-5,
        ),
        workflow_status=status,
        timestamp="2025-01-01T00:00:00Z",
    )


@pytest.mark.parametrize("exec_status", [
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.CANCELED,
])
def test_in_progress_plus_closed_becomes_failed(exec_status: WorkflowExecutionStatus) -> None:
    """IN_PROGRESS output + a closed Temporal execution status → FAILED with error text."""
    out = _output(VerifyWorkflowStatus.IN_PROGRESS)
    result = _reconcile_terminal_status(out, exec_status)
    assert result.workflow_status == VerifyWorkflowStatus.FAILED
    assert result.workflow_error is not None
    assert exec_status.name in result.workflow_error
    # Original object must not be mutated (model_copy returns a new object)
    assert out.workflow_status == VerifyWorkflowStatus.IN_PROGRESS


def test_in_progress_plus_running_is_unchanged() -> None:
    """IN_PROGRESS + RUNNING → no change (workflow is legitimately still running)."""
    out = _output(VerifyWorkflowStatus.IN_PROGRESS)
    result = _reconcile_terminal_status(out, WorkflowExecutionStatus.RUNNING)
    assert result.workflow_status == VerifyWorkflowStatus.IN_PROGRESS
    assert result.workflow_error is None


@pytest.mark.parametrize("already_done", [
    VerifyWorkflowStatus.COMPLETED,
    VerifyWorkflowStatus.FAILED,
    VerifyWorkflowStatus.RUN_ID_NOT_FOUND,
])
def test_already_terminal_output_is_unchanged(already_done: VerifyWorkflowStatus) -> None:
    """A workflow that already reported a terminal status is never overridden."""
    out = _output(already_done)
    result = _reconcile_terminal_status(out, WorkflowExecutionStatus.FAILED)
    assert result.workflow_status == already_done
