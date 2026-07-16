"""Models for the BioSim DB project search API.

Backs the two GET endpoints that replace the legacy
``/projects/summary_filtered`` (see ``docs/project-search-api-plan.md``):

- ``GET /projects``        -> paginated slim ``ProjectStub`` results
- ``GET /projects/stats``  -> tag/category facet counts (``ProjectQueryStat``)

Field names mirror ``frontend/app/models/projects.ts`` so the frontend types do
not drift.
"""

from pydantic import BaseModel, ConfigDict, Field


class ValueFrequency(BaseModel):
    """One facet value and how many matching projects carry it."""

    value: str
    count: int


class ProjectQueryStat(BaseModel):
    """Facet counts for a single filterable target (e.g. taxa, modelFormat)."""

    target: str
    value_frequencies: list[ValueFrequency] = Field(
        default_factory=list, serialization_alias="valueFrequencies"
    )


class ProjectSearchFilter(BaseModel):
    """One active filter: restrict ``target`` to any of ``allowable_values``.

    Matches the frontend ``ProjectSearchFilter`` interface. Sent to the API as a
    JSON-encoded array in the ``filters`` query param.
    """

    target: str
    allowable_values: list[str] = Field(default_factory=list)


class ProjectStub(BaseModel):
    """Slim, list-optimized project record. Mirrors the frontend ``ProjectStub``.

    ``model_format`` and ``image_url`` require a join beyond ``projectSummary``
    (Specifications / Files) and are best-effort until that join lands; they
    default to empty rather than failing the whole page.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    simulation_run: str = Field(serialization_alias="simulationRun")
    created: str
    updated: str
    name: str
    summary: str
    model_format: str = ""
    image_url: str | None = None


class ProjectStubPage(BaseModel):
    """A page of results plus the total match count (for the pager)."""

    items: list[ProjectStub]
    total: int
