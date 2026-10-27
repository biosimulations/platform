"""Narrow public contracts consumed by the project and run detail pages."""

from pydantic import BaseModel, ConfigDict, Field


class LabeledIdentifier(BaseModel):
    uri: str | None = None
    label: str | None = None


class SimulatorSummary(BaseModel):
    name: str
    version: str


class RunExecution(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    simulator: SimulatorSummary
    project_size: int | None = Field(default=None, alias="projectSize")
    results_size: int | None = Field(default=None, alias="resultsSize")


class RunMetadataSummary(BaseModel):
    abstract: str | None = None
    description: str | None = None
    thumbnails: list[str] = Field(default_factory=list)
    creators: list[LabeledIdentifier] = Field(default_factory=list)
    keywords: list[LabeledIdentifier] = Field(default_factory=list)
    citations: list[LabeledIdentifier] = Field(default_factory=list)
    encodes: list[LabeledIdentifier] = Field(default_factory=list)


class RunSummary(BaseModel):
    id: str
    name: str
    run: RunExecution
    metadata: list[RunMetadataSummary] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created: str
    updated: str
    simulation_run: RunSummary = Field(alias="simulationRun")
