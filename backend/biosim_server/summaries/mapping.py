"""Validate legacy inputs before projecting them into our public contract."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from biosim_server.summaries.models import ProjectSummary, RunSummary


class _UpstreamIdentifier(BaseModel):
    model_config = ConfigDict(extra="ignore")
    uri: str | None = None
    label: str | None = None


class _UpstreamSimulator(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    version: str


class _UpstreamExecution(BaseModel):
    model_config = ConfigDict(extra="ignore")
    simulator: _UpstreamSimulator
    project_size: int | None = Field(default=None, alias="projectSize")
    results_size: int | None = Field(default=None, alias="resultsSize")


class _UpstreamMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    abstract: str | None = None
    description: str | None = None
    thumbnails: list[str] = Field(default_factory=list)
    creators: list[_UpstreamIdentifier] = Field(default_factory=list)
    keywords: list[_UpstreamIdentifier] = Field(default_factory=list)
    citations: list[_UpstreamIdentifier] = Field(default_factory=list)
    encodes: list[_UpstreamIdentifier] = Field(default_factory=list)


class _UpstreamRun(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    run: _UpstreamExecution
    metadata: list[_UpstreamMetadata] = Field(default_factory=list)


class _UpstreamProject(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    created: str
    updated: str
    simulation_run: _UpstreamRun = Field(alias="simulationRun")


def map_run_summary(payload: dict[str, Any]) -> RunSummary:
    upstream = _UpstreamRun.model_validate(payload)
    owned = upstream.model_dump(by_alias=True)
    owned["metadata"] = owned["metadata"][:1]
    return RunSummary.model_validate(owned)


def map_project_summary(payload: dict[str, Any]) -> ProjectSummary:
    upstream = _UpstreamProject.model_validate(payload)
    return ProjectSummary(
        id=upstream.id,
        created=upstream.created,
        updated=upstream.updated,
        simulationRun=map_run_summary(upstream.simulation_run.model_dump(by_alias=True)),
    )
