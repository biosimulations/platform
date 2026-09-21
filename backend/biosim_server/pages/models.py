"""Narrow public detail-page contracts, independent of compatibility summaries."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from biosim_server.summaries.models import LabeledIdentifier


class PageModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class PageIdentifier(LabeledIdentifier):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    label: str


class PageLinkedIdentifier(PageIdentifier):
    uri: str | None = None


class PageFile(PageModel):
    format: str
    location: str
    size: int
    url: str


class PageGenerator(PageModel):
    id: str
    name: str | None = None


class PageLineStyle(PageModel):
    color: str | None = None
    thickness: float | None = None
    type: str | None = None


class PageMarkerStyle(PageModel):
    fill_color: str | None = None
    line_color: str | None = None
    line_thickness: float | None = None
    size: float | None = None
    type: str | None = None


class PageCurveStyle(PageModel):
    base: str | None = None
    line: PageLineStyle | None = None
    marker: PageMarkerStyle | None = None


class PageCurve(PageModel):
    id: str
    name: str | None = None
    x_data_generator: str | PageGenerator
    y_data_generator: str | PageGenerator
    style: str | PageCurveStyle | None = None


class PageDataSet(PageModel):
    id: str
    label: str
    name: str | None = None


class PageOutput(PageModel):
    type: str = Field(alias="_type")
    id: str
    name: str | None = None
    x_scale: str | None = None
    y_scale: str | None = None
    curves: list[PageCurve] | None = None
    data_sets: list[PageDataSet] | None = None


class PageSpecification(PageModel):
    id: str
    outputs: list[PageOutput]


class PageAlgorithm(PageModel):
    id: str
    name: str | None = None
    url: str | None = None
    description: str | None = None


class PageExecutionReason(PageModel):
    message: str | None = None
    type: str | None = None


class PageLog(PageModel):
    status: str
    output: str | None = None
    algorithm: PageAlgorithm | None = None
    skip_reason: PageExecutionReason | None = None
    exception: PageExecutionReason | None = None


class PageTaskLog(PageLog):
    id: str


class PageDataSetLog(PageModel):
    id: str | None = None
    status: str | None = None
    output: str | None = None


class PageOutputLog(PageTaskLog):
    data_sets: list[PageDataSetLog] | None = None


class PageDocumentLog(PageLog):
    location: str
    tasks: list[PageTaskLog] | None = None
    outputs: list[PageOutputLog] | None = None


class PageSimulationLog(PageLog):
    sed_documents: list[PageDocumentLog] | None = None


class PageRunInfo(PageModel):
    id: str
    name: str
    simulator: str
    simulator_version: str
    submitted: str
    updated: str
    status: str


class PageRunSummary(PageModel):
    name: str | None = None
    description: str | None = None
    abstract: str | None = None
    creators: list[PageIdentifier]
    keywords: list[PageIdentifier]
    thumbnails: list[str]
    project_size: int | None = None
    results_size: int | None = None


class PageSimulator(PageModel):
    name: str
    version: str


class PageProjectRun(PageRunSummary):
    id: str
    name: str
    citations: list[PageLinkedIdentifier]
    encodes: list[PageLinkedIdentifier]
    simulator: PageSimulator
    model_formats: list[str]


class PageProject(PageModel):
    id: str
    created: str
    updated: str


class ProjectsPagePayload(PageModel):
    project: PageProject
    simulation_run: PageProjectRun
    files: list[PageFile]
    specifications: list[PageSpecification]


class RunsPagePayload(PageModel):
    info: PageRunInfo
    summary: PageRunSummary
    files: list[PageFile]
    specifications: list[PageSpecification]
    logs: PageSimulationLog | None
