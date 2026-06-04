"""Activity for recording user-facing simulation run status.

The biosimulations.org submit/poll/save now runs in OmexSimWorkflow (shared with
the verification path); this module only records the per-submission status that
backs the /simulations/runs listing.
"""

import logging

from pydantic import BaseModel, model_validator
from temporalio import activity

from biosim_server.dependencies import get_simulation_run_database_service


class UpdateRunStatusInput(BaseModel):
    run_id: str                              # the per-simulator job UUID == SimulationRunRecord.run_id
    # Either or both may be omitted: an early write may carry only biosimulations_run_id,
    # while the terminal write carries status (SUCCEEDED/FAILED). None fields are not
    # written to the document. At least one of the two must be set -- a call with both
    # None would degenerate to bumping only `updated`, which is never the intent.
    status: str | None = None                # "CREATED" | "SUCCEEDED" | "FAILED" when set
    biosimulations_run_id: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "UpdateRunStatusInput":
        if self.status is None and self.biosimulations_run_id is None:
            raise ValueError("UpdateRunStatusInput must set at least one of status or biosimulations_run_id")
        return self


@activity.defn
async def update_run_status_activity(input: UpdateRunStatusInput) -> None:
    """Write run state to the SimulationRunRecord for the listing/status endpoints.

    Degrades to a no-op when the runs database service is not configured (e.g.
    unit tests that don't boot the full app), so it never fails the workflow."""
    activity.logger.setLevel(logging.INFO)
    runs_db = get_simulation_run_database_service()
    if runs_db is None:
        activity.logger.warning("Simulation run database service not available; skipping status update")
        return
    await runs_db.update_simulation_run(
        input.run_id,
        status=input.status,
        biosimulations_run_id=input.biosimulations_run_id,
    )
    activity.logger.info(
        f"Updated run {input.run_id}: "
        f"status={input.status!r} biosimulations_run_id={input.biosimulations_run_id!r}"
    )
