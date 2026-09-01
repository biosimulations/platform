"""Mirrors of the biosimulations.org run-summary contract.

``SimulationRunSummary`` deliberately serves **two** upstream endpoints:

* ``GET /runs/{id}/summary`` — the run page's entry point, and
* the ``simulationRun`` member of ``GET /projects/{id}/summary``.

They are the same object upstream (the frontend assigns both to its single
``SimulationRunSummary`` TypeScript interface), so in project context the
``/runs/{id}/summary`` call is redundant and must not be made.
"""

from biosim_server.common.biosim_api.common import LabeledIdentifier, UpstreamModel
from pydantic import Field


class RunMetadata(UpstreamModel):
    """One entry of a run summary's ``metadata`` array.

    Consumers read ``metadata[0]``, but an empty array is a normal upstream
    state — this model never assumes an element exists.
    """

    abstract: str | None = None
    description: str | None = None
    creators: list[LabeledIdentifier] = Field(default_factory=list)
    keywords: list[LabeledIdentifier] = Field(default_factory=list)
    citations: list[LabeledIdentifier] = Field(default_factory=list)
    encodes: list[LabeledIdentifier] = Field(default_factory=list)
    # Bare archive filenames (e.g. "Figure2.jpg"), NOT URLs. The download URL is
    # built from the run id -- see projects.database._image_url.
    thumbnails: list[str] = Field(default_factory=list)


class SimulatorDetails(UpstreamModel):
    """The run summary's nested ``run.simulator`` object.

    ``name`` here is a *display* name, distinct from the ``simulator`` slug on
    the flat ``GET /runs/{id}`` payload (see BiosimSimulationRun.simulator_id).
    They often coincide, but they are not the same field.
    """

    id: str | None = None
    name: str | None = None
    version: str | None = None
    digest: str | None = None
    url: str | None = None


class RunDetails(UpstreamModel):
    """The run summary's nested ``run`` block. Sizes are in bytes."""

    project_size: int | None = Field(default=None, alias="projectSize")
    results_size: int | None = Field(default=None, alias="resultsSize")
    simulator: SimulatorDetails | None = None


class SimulationRunSummary(UpstreamModel):
    """``GET /runs/{id}/summary``, and the ``simulationRun`` of a project summary."""

    id: str | None = None
    name: str | None = None
    # Run-lifecycle timestamps. NOT the project record's created/updated, which
    # track a different subject and live on ProjectSummary.
    submitted: str | None = None
    updated: str | None = None
    metadata: list[RunMetadata] = Field(default_factory=list)
    # Optional: an upstream project whose run block is absent must still
    # validate. Requiring it turned a sparse upstream payload into a 500.
    run: RunDetails | None = None
