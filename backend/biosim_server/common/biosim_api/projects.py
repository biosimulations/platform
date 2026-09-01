"""Mirror of the biosimulations.org ``GET /projects/{id}/summary`` envelope.

This model is a faithful mirror of *one* endpoint. Files, specifications, logs,
results and ontology terms are separate upstream resources with separate
lifetimes and (for results) unbounded payloads -- they get their own sibling
models and routes, and are never folded in here.
"""

from biosim_server.common.biosim_api.common import UpstreamModel
from biosim_server.common.biosim_api.runs import SimulationRunSummary
from pydantic import Field


class ProjectSummary(UpstreamModel):
    """Envelope returned by biosimulations.org ``GET /projects/{id}/summary``."""

    id: str
    # Project-catalog timestamps. A project's metadata can be edited long after
    # its run finished, so these are NOT simulation_run.submitted/updated.
    created: str | None = None
    updated: str | None = None
    simulation_run: SimulationRunSummary = Field(alias="simulationRun")
