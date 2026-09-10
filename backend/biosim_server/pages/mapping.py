"""Validate upstream inputs and project only the owned page fields."""

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic.alias_generators import to_camel

from biosim_server.common.kisao_data import KISAO_TERMS
from biosim_server.summaries.models import LabeledIdentifier
from biosim_server.pages.models import (
    PageFile, PageSpecification, PageSimulationLog, ProjectsPagePayload, RunsPagePayload,
)

_OLS_KISAO = (
    "https://www.ebi.ac.uk/ols4/ontologies/kisao/terms"
    "?iri=http%3A%2F%2Fwww.biomodels.net%2Fkisao%2FKISAO%23{ols_id}"
)


def _coerce_algorithm(value: object) -> object:
    """Legacy logs send a KiSAO id string; owned logs use AlgorithmDetails."""
    if isinstance(value, str):
        value = {"id": value}
    if not isinstance(value, dict):
        return value
    algorithm_id = value.get("id")
    if not isinstance(algorithm_id, str) or not algorithm_id:
        return value
    ols_id = algorithm_id.replace(":", "_")
    term = KISAO_TERMS.get(ols_id.replace("KISAO_", "KISAO:", 1))
    if not value.get("name") and term:
        value = {**value, "name": term["name"]}
    if not value.get("url"):
        value = {**value, "url": _OLS_KISAO.format(ols_id=ols_id)}
    return value


class _UpstreamModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class _UpstreamIdentifier(LabeledIdentifier):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    label: str


class _UpstreamLinkedIdentifier(_UpstreamIdentifier):
    uri: str | None = None


class _UpstreamFile(_UpstreamModel):
    format: str
    location: str
    size: int
    url: str


class _UpstreamGenerator(_UpstreamModel):
    id: str
    name: str | None = None


class _UpstreamLineStyle(_UpstreamModel):
    color: str | None = None
    thickness: float | None = None
    type: str | None = None


class _UpstreamMarkerStyle(_UpstreamModel):
    fill_color: str | None = None
    line_color: str | None = None
    line_thickness: float | None = None
    size: float | None = None
    type: str | None = None


class _UpstreamCurveStyle(_UpstreamModel):
    base: str | None = None
    line: _UpstreamLineStyle | None = None
    marker: _UpstreamMarkerStyle | None = None


class _UpstreamCurve(_UpstreamModel):
    id: str
    name: str | None = None
    x_data_generator: str | _UpstreamGenerator
    y_data_generator: str | _UpstreamGenerator
    style: _UpstreamCurveStyle | None = None


class _UpstreamDataSet(_UpstreamModel):
    id: str
    label: str
    name: str | None = None


class _UpstreamOutput(_UpstreamModel):
    type: str = Field(alias="_type")
    id: str
    name: str | None = None
    x_scale: str | None = None
    y_scale: str | None = None
    curves: list[_UpstreamCurve] | None = None
    data_sets: list[_UpstreamDataSet] | None = None


class _UpstreamSpecification(_UpstreamModel):
    id: str
    outputs: list[_UpstreamOutput]


class _UpstreamAlgorithm(_UpstreamModel):
    id: str
    name: str | None = None
    url: str | None = None
    description: str | None = None


class _UpstreamExecutionReason(_UpstreamModel):
    message: str | None = None
    type: str | None = None


class _UpstreamLog(_UpstreamModel):
    status: str
    output: str | None = None
    algorithm: _UpstreamAlgorithm | None = None
    skip_reason: _UpstreamExecutionReason | None = None
    exception: _UpstreamExecutionReason | None = None

    @field_validator("algorithm", mode="before")
    @classmethod
    def algorithm_from_kisao_id(cls, value: object) -> object:
        return _coerce_algorithm(value)


class _UpstreamTaskLog(_UpstreamLog):
    id: str


class _UpstreamDataSetLog(_UpstreamModel):
    id: str | None = None
    status: str | None = None
    output: str | None = None


class _UpstreamOutputLog(_UpstreamTaskLog):
    data_sets: list[_UpstreamDataSetLog] | None = None


class _UpstreamDocumentLog(_UpstreamLog):
    location: str
    tasks: list[_UpstreamTaskLog] | None = None
    outputs: list[_UpstreamOutputLog] | None = None


class _UpstreamSimulationLog(_UpstreamLog):
    sed_documents: list[_UpstreamDocumentLog] | None = None


class _UpstreamSimulator(_UpstreamModel):
    id: str | None = None
    name: str
    version: str


class _UpstreamExecution(_UpstreamModel):
    simulator: _UpstreamSimulator
    status: str | None = None
    project_size: int | None = None
    results_size: int | None = None


class _UpstreamMetadata(_UpstreamModel):
    title: str | None = None
    description: str | None = None
    abstract: str | None = None
    creators: list[_UpstreamIdentifier] = Field(default_factory=list)
    keywords: list[_UpstreamIdentifier] = Field(default_factory=list)
    thumbnails: list[str] = Field(default_factory=list)
    citations: list[_UpstreamLinkedIdentifier] = Field(default_factory=list)
    encodes: list[_UpstreamLinkedIdentifier] = Field(default_factory=list)


class _UpstreamRun(_UpstreamModel):
    id: str
    name: str
    submitted: str | None = None
    updated: str | None = None
    run: _UpstreamExecution
    metadata: list[_UpstreamMetadata] = Field(default_factory=list)


class _UpstreamProject(_UpstreamModel):
    id: str
    created: str
    updated: str
    simulation_run: _UpstreamRun


class _UpstreamLanguage(_UpstreamModel):
    acronym: str


class _UpstreamSedModel(_UpstreamModel):
    language: str | _UpstreamLanguage


class _UpstreamFullSpecification(_UpstreamSpecification):
    models: list[_UpstreamSedModel] = Field(default_factory=list)


class _UpstreamDocuments(_UpstreamModel):
    sed_documents: list[_UpstreamFullSpecification]


def normalize_specifications(payload: object) -> list[_UpstreamFullSpecification]:
    if isinstance(payload, list):
        return TypeAdapter(list[_UpstreamFullSpecification]).validate_python(payload)
    if isinstance(payload, dict) and "sedDocuments" in payload:
        return _UpstreamDocuments.model_validate(payload).sed_documents
    return [_UpstreamFullSpecification.model_validate(payload)]


def map_files(payload: object) -> list[PageFile]:
    entries = TypeAdapter(list[_UpstreamFile]).validate_python(payload)
    return [PageFile.model_validate(entry.model_dump()) for entry in entries]


def map_specifications(documents: list[_UpstreamFullSpecification]) -> list[PageSpecification]:
    return [PageSpecification.model_validate(doc.model_dump(by_alias=True, exclude={"models"}))
            for doc in documents]


def model_formats(documents: list[_UpstreamFullSpecification]) -> list[str]:
    """Match project search's first-seen ordering and URN acronym extraction."""
    formats: dict[str, None] = {}
    for document in documents:
        for model in document.models:
            language = model.language
            acronym = (language.split(":")[-1].split(".")[0].upper()
                       if isinstance(language, str) else language.acronym)
            if acronym:
                formats.setdefault(acronym, None)
    return list(formats)


def map_logs(payload: object) -> PageSimulationLog | None:
    if payload is None:
        return None
    upstream = _UpstreamSimulationLog.model_validate(payload)
    return PageSimulationLog.model_validate(upstream.model_dump())


def parse_project(payload: object) -> _UpstreamProject:
    return _UpstreamProject.model_validate(payload)


def parse_run(payload: object) -> _UpstreamRun:
    return _UpstreamRun.model_validate(payload)


def _metadata(run: _UpstreamRun) -> _UpstreamMetadata:
    # Missing metadata is known upstream behavior; never merge later records.
    return run.metadata[0] if run.metadata else _UpstreamMetadata()


def map_project_page(
    project: _UpstreamProject, files: object, specifications: object,
) -> ProjectsPagePayload:
    run = project.simulation_run
    metadata = _metadata(run)
    documents = normalize_specifications(specifications)
    formats = model_formats(documents)  # Derive before discarding specification models.
    return ProjectsPagePayload.model_validate({
        "project": project.model_dump(exclude={"simulation_run"}),
        "simulationRun": {
            **metadata.model_dump(exclude={"title"}),
            "id": run.id, "name": run.name,
            "simulator": run.run.simulator.model_dump(exclude={"id"}),
            "modelFormats": formats,
            "projectSize": run.run.project_size, "resultsSize": run.run.results_size,
        },
        "files": map_files(files), "specifications": map_specifications(documents),
    })


def map_run_page(
    run: _UpstreamRun, files: object, specifications: object, logs: object,
) -> RunsPagePayload:
    metadata = _metadata(run)
    return RunsPagePayload.model_validate({
        "info": {
            "id": run.id, "name": run.name,
            "simulator": run.run.simulator.id or run.run.simulator.name,
            "simulatorVersion": run.run.simulator.version,
            "submitted": run.submitted, "updated": run.updated, "status": run.run.status,
        },
        "summary": {
            **metadata.model_dump(exclude={"title", "citations", "encodes"}),
            "name": metadata.title,
            "projectSize": run.run.project_size, "resultsSize": run.run.results_size,
        },
        "files": map_files(files),
        "specifications": map_specifications(normalize_specifications(specifications)),
        "logs": map_logs(logs),
    })
