"""One persistence helper for every verification workflow.

The verification workflows used to each carry their own copy of the persist
activity call, with a retry policy that gave up after 20 attempts.  A storage
outage that lasted longer than that turned a computed result into a permanent
failure.  This module holds the single call site and the single policy:

* transient failures are retried without a cap, so a computed result is never
  degraded because storage was briefly unavailable;
* a permanent failure completes the workflow as ``BLOCKED``, keeping the
  computed output, because a workflow that keeps retrying also keeps its
  history, which is what recovery reads.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from biosim_server.biosim_verify.activities import (
    PERMANENT_ERROR_TYPE,
    PersistVerificationReportActivityInput,
    persist_verification_report_activity,
)
from biosim_server.biosim_verify.models import (
    ReportPersistenceState,
    VerificationProvenance,
    VerifyWorkflowOutput,
)

# 0 attempts means "no maximum": the workflow keeps the computed output alive
# in its history until storage accepts it or the failure is permanent.
PERSIST_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=0,
)

#: Re-exported from ``activities``, where it sits beside the raise it describes.
#: The workflow matches on it to complete a permanent failure as ``BLOCKED``.


async def persist_report(
    output: VerifyWorkflowOutput,
    provenance: VerificationProvenance,
) -> VerifyWorkflowOutput:
    """Publish the computed output, retrying transient failures without a cap.

    A permanent failure completes the workflow with the computed output marked
    ``BLOCKED``.  It never becomes FAILED: the verification itself succeeded.
    """
    try:
        return await workflow.execute_activity(
            persist_verification_report_activity,
            PersistVerificationReportActivityInput(output=output, provenance=provenance),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=PERSIST_RETRY,
        )
    except ActivityError as err:
        cause = err.cause
        if isinstance(cause, ApplicationError) and cause.type == PERMANENT_ERROR_TYPE:
            return output.model_copy(update={
                "persistence_state": ReportPersistenceState.BLOCKED,
                "report_available": False,
            })
        raise
