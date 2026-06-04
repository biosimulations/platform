"""Activity for recording user-facing simulation run status.

The biosimulations.org submit/poll/save now runs in OmexSimWorkflow (shared with
the verification path); this module only records the per-submission status that
backs the /simulations/runs listing.
"""

import logging

from pydantic import BaseModel
from temporalio import activity

from biosim_server.dependencies import get_simulation_run_database_service


class UpdateRunStatusInput(BaseModel):
    run_id: str                              # the per-simulator job UUID == SimulationRunRecord.run_id
    status: str                              # "CREATED" | "SUCCEEDED" | "FAILED"
    biosimulations_run_id: str | None = None


@activity.defn
async def update_run_status_activity(input: UpdateRunStatusInput) -> None:
    """Write a run's terminal status back to the queryable runs collection.

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
    activity.logger.info(f"Updated run {input.run_id} status -> {input.status}")
