"""Contract projection against full synthetic satellite and captured identity data."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from biosim_server.pages.mapping import (
    map_files, map_logs, map_project_page, map_run_page, map_specifications,
    normalize_specifications, parse_project, parse_run,
)
from tests.summaries.test_mapping import FIXTURES, payload


def satellite(name: str) -> Any:
    return json.loads((FIXTURES / f"page_{name}_response.json").read_text())


def test_run_fields_and_first_metadata() -> None:
    raw = payload()
    first = raw["metadata"][0]
    raw["metadata"].append({"title": "LATER", "description": "LATER", "creators": [{"label": "LATER"}]})
    result = map_run_page(parse_run(raw), satellite("files"), satellite("specifications"), satellite("logs"))
    owned = result.model_dump(by_alias=True)
    assert set(owned) == {"info", "summary", "files", "specifications", "logs"}
    assert owned["info"] == {
        "id": raw["id"], "name": raw["name"], "simulator": "boolnet", "simulatorVersion": "2.1.5",
        "submitted": raw["submitted"], "updated": raw["updated"], "status": "SUCCEEDED",
    }
    assert owned["summary"] == {
        "name": first["title"], "description": first["description"], "abstract": first["abstract"],
        "creators": first["creators"], "keywords": first["keywords"], "thumbnails": first["thumbnails"],
        "projectSize": 64521, "resultsSize": 10975,
    }
    assert "LATER" not in result.model_dump_json()
    del raw["run"]["simulator"]["id"]
    assert map_run_page(parse_run(raw), [], [], None).info.simulator == "BoolNet"


def test_project_fields_and_model_formats() -> None:
    raw = payload("project")
    first = raw["simulationRun"]["metadata"][0]
    raw["simulationRun"]["metadata"].append({"description": "LATER", "citations": [{"label": "LATER", "uri": "x"}]})
    owned = map_project_page(parse_project(raw), satellite("files"), satellite("specifications")).model_dump(by_alias=True)
    assert set(owned) == {"project", "simulationRun", "files", "specifications"}
    assert owned["project"] == {k: raw[k] for k in ["id", "created", "updated"]}
    assert owned["simulationRun"] == {
        "id": raw["simulationRun"]["id"], "name": raw["simulationRun"]["name"],
        **{k: first[k] for k in ["description", "abstract", "creators", "keywords", "thumbnails", "citations", "encodes"]},
        "simulator": {"name": "BoolNet", "version": "2.1.5"}, "modelFormats": ["SBML", "CELLML"],
        "projectSize": 64521, "resultsSize": 10975,
    }
    assert "models" not in owned["specifications"][0]
    assert "LATER" not in json.dumps(owned)


@pytest.mark.parametrize("rename", [False, True])
@pytest.mark.parametrize("path", [
    ("id",), ("name",), ("run",), ("run", "simulator"),
    ("run", "simulator", "name"), ("run", "simulator", "version"),
])
def test_required_run_drift(path: tuple[str, ...], rename: bool) -> None:
    raw = payload()
    target = raw
    for key in path[:-1]:
        target = target[key]
    value = target.pop(path[-1])
    if rename:
        target["renamed"] = value
    with pytest.raises(ValidationError):
        parse_run(raw)
    project = payload("project")
    project["simulationRun"] = raw
    with pytest.raises(ValidationError):
        parse_project(project)


@pytest.mark.parametrize("field", ["submitted", "updated"])
def test_run_page_info_requires_timestamps(field: str) -> None:
    raw = payload()
    del raw[field]
    parsed = parse_run(raw)
    with pytest.raises(ValidationError):
        map_run_page(parsed, [], [], None)
    project = payload("project")
    del project["simulationRun"][field]
    assert map_project_page(parse_project(project), [], []).project.id == project["id"]


def test_run_page_info_requires_status() -> None:
    raw = payload()
    del raw["run"]["status"]
    with pytest.raises(ValidationError):
        map_run_page(parse_run(raw), [], [], None)
    project = payload("project")
    del project["simulationRun"]["run"]["status"]
    assert map_project_page(parse_project(project), [], []).project.id == project["id"]


def test_nullable_sizes_and_citation_uris() -> None:
    project = payload("project")
    project["simulationRun"]["run"]["projectSize"] = None
    del project["simulationRun"]["run"]["resultsSize"]
    project["simulationRun"]["metadata"][0]["citations"] = [{"label": "paper", "uri": None}]
    project["simulationRun"]["metadata"][0]["encodes"] = [{"label": "model"}]
    owned = map_project_page(parse_project(project), [], []).model_dump(by_alias=True)["simulationRun"]
    assert owned["projectSize"] is None
    assert owned["resultsSize"] is None
    assert owned["citations"] == [{"label": "paper", "uri": None}]
    assert owned["encodes"] == [{"label": "model", "uri": None}]
    run = payload()
    run["run"]["projectSize"] = None
    del run["run"]["resultsSize"]
    summary = map_run_page(parse_run(run), [], [], None).model_dump(by_alias=True)["summary"]
    assert summary["projectSize"] is None
    assert summary["resultsSize"] is None


@pytest.mark.parametrize("key", ["id", "created", "updated", "simulationRun"])
@pytest.mark.parametrize("rename", [False, True])
def test_required_project_drift(key: str, rename: bool) -> None:
    raw = payload("project")
    value = raw.pop(key)
    if rename:
        raw["renamed"] = value
    with pytest.raises(ValidationError):
        parse_project(raw)


@pytest.mark.parametrize("shape", ["array", "object", "wrapped"])
def test_specifications_projection_and_generator_union(shape: str) -> None:
    raw = satellite("specifications")
    inputs = {"array": raw, "object": raw[0], "wrapped": {"sedDocuments": raw}}
    owned = map_specifications(normalize_specifications(inputs[shape]))[0].model_dump(by_alias=True)
    assert set(owned) == {"id", "outputs"}
    plot, report = owned["outputs"]
    assert set(plot) == {"_type", "id", "name", "xScale", "yScale", "curves", "dataSets"}
    assert plot["curves"][0]["xDataGenerator"] == "time"
    assert plot["curves"][0]["yDataGenerator"] == {"id": "value", "name": "Value"}
    assert plot["curves"][0]["style"] == raw[0]["outputs"][0]["curves"][0]["style"]
    assert report["dataSets"] == [{"id": "dataset", "name": "Dataset", "label": "Value"}]
    assert "ignored" not in json.dumps(owned)


@pytest.mark.parametrize("raw", [{}, {"unrelated": []}, {"sedDocuments": {}}, [None], {"id": "doc"}])
def test_invalid_specifications_fail(raw: object) -> None:
    with pytest.raises(ValidationError):
        normalize_specifications(raw)


def test_files_projection() -> None:
    owned = map_files(satellite("files"))[0].model_dump(by_alias=True)
    assert owned == {k: v for k, v in satellite("files")[0].items() if k != "ignored"}


def test_logs_nested_tree() -> None:
    result = map_logs(satellite("logs"))
    assert result is not None
    owned = result.model_dump(by_alias=True)
    document = owned["sedDocuments"][0]
    assert document["location"] == "simulation.sedml"
    assert document["tasks"][0]["id"] == "task"
    assert document["outputs"][0]["dataSets"] == [{"id": "dataset", "status": "SUCCEEDED", "output": "Dataset output"}]
    assert owned["algorithm"] == satellite("logs")["algorithm"]
    assert owned["skipReason"] == satellite("logs")["skipReason"]
    assert owned["exception"] == satellite("logs")["exception"]
    assert "ignored" not in json.dumps(owned)
    assert "hasDataSets" not in json.dumps(owned)
    assert map_logs(None) is None


def test_log_algorithm_kisao_string_is_projected() -> None:
    raw = satellite("logs")
    raw["sedDocuments"][0]["tasks"][0]["algorithm"] = "KISAO_0000449"
    result = map_logs(raw)
    assert result is not None
    assert result.sed_documents is not None
    tasks = result.sed_documents[0].tasks
    assert tasks is not None
    algorithm = tasks[0].algorithm
    assert algorithm is not None
    owned = algorithm.model_dump(by_alias=True)
    assert owned["id"] == "KISAO_0000449"
    assert owned["name"] == "synchronous logical model simulation method"
    assert owned["url"] and "KISAO_0000449" in owned["url"]


@pytest.mark.parametrize("resource,path", [
    ("files", (0, "format")), ("files", (0, "location")), ("files", (0, "size")), ("files", (0, "url")),
    ("specifications", (0, "id")), ("specifications", (0, "outputs")),
    ("specifications", (0, "outputs", 0, "_type")),
    ("specifications", (0, "outputs", 0, "curves", 0, "xDataGenerator")),
    ("specifications", (0, "outputs", 1, "dataSets", 0, "label")),
    ("logs", ("status",)), ("logs", ("sedDocuments", 0, "location")),
    ("logs", ("sedDocuments", 0, "tasks", 0, "id")), ("logs", ("algorithm", "id")),
])
@pytest.mark.parametrize("rename", [False, True])
def test_satellite_required_drift(resource: str, path: tuple[str | int, ...], rename: bool) -> None:
    raw = satellite(resource)
    target = raw
    for key in path[:-1]:
        target = target[key]
    value = target.pop(path[-1])
    if rename:
        target["renamed"] = value
    with pytest.raises(ValidationError):
        {"files": map_files, "specifications": normalize_specifications, "logs": map_logs}[resource](raw)


@pytest.mark.parametrize("missing", [False, True])
def test_known_empty_metadata_and_nullable_logs(missing: bool) -> None:
    raw = payload()
    raw["metadata"] = []
    if missing:
        del raw["metadata"]
    owned = map_run_page(parse_run(raw), [], [], None).model_dump(by_alias=True)
    assert owned["summary"]["name"] is None
    assert owned["summary"]["creators"] == []
    assert owned["logs"] is None
    assert owned["files"] == owned["specifications"] == []


def test_unpublished_run_needs_no_project_and_generator_name_is_optional() -> None:
    raw = payload()
    assert "project" not in raw
    specifications = satellite("specifications")
    curve = specifications[0]["outputs"][0]["curves"][0]
    curve["xDataGenerator"] = {"id": "time"}
    curve["yDataGenerator"] = "value"
    owned = map_run_page(parse_run(raw), [], specifications, None).model_dump(by_alias=True)
    projected = owned["specifications"][0]["outputs"][0]["curves"][0]
    assert projected["xDataGenerator"] == {"id": "time", "name": None}
    assert projected["yDataGenerator"] == "value"
    assert owned["info"]["id"] == raw["id"]


def test_optional_title_does_not_fall_back_to_run_name() -> None:
    raw = payload()
    del raw["metadata"][0]["title"]
    assert map_run_page(parse_run(raw), [], [], None).summary.name is None
