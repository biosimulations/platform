"""Real-payload projection and fail-closed upstream drift regressions."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from biosim_server.summaries.mapping import map_project_summary, map_run_summary

FIXTURES = Path(__file__).parents[1] / "fixtures" / "local_data"


def payload(kind: str = "run") -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / f"{kind}_summary_response.json").read_text())
    return result


def test_real_run_leaves() -> None:
    summary = map_run_summary(payload())
    assert summary.id == "61fea483f499ccf25faafc4d"
    assert summary.name == (
        "Budding yeast cell cycle (Irons, J Theor Biol, 2009; SBML-qual; BoolNet; synchronous)"
    )
    assert summary.run.simulator.name == "BoolNet"
    assert summary.run.simulator.version == "2.1.5"
    assert summary.run.project_size == 64521
    assert summary.run.results_size == 10975
    metadata = summary.metadata[0]
    assert metadata.abstract == "Boolean model of the budding yeast cell cycle."
    assert metadata.description == payload()["metadata"][0]["description"]
    assert metadata.thumbnails == ["Figure2.jpg"]
    assert metadata.creators[0].label == "D. J. Irons"
    assert metadata.creators[0].uri is None
    assert metadata.keywords == []
    assert metadata.citations[0].uri == "http://identifiers.org/doi:10.1186/1752-0509-7-135"
    assert metadata.citations[0].label == (
        "DJ Irons. Logical analysis of the budding yeast cell cycle. "
        "J Theor Biol 257, 4 (2009): 543-559."
    )
    assert metadata.encodes[0].uri == "http://identifiers.org/GO:0007049"
    assert metadata.encodes[0].label == "cell cycle"


def test_project_embeds_shared_run_contract() -> None:
    raw = payload("project")
    summary = map_project_summary(raw)
    assert summary.id == "Yeast-cell-cycle-Irons-J-Theor-Biol-2009"
    assert summary.created == "2021-11-11T23:36:06.756Z"
    assert summary.updated == "2022-02-05T21:05:26.046Z"
    assert summary.simulation_run == map_run_summary(raw["simulationRun"])
    assert summary.simulation_run == map_run_summary(payload())
    assert "simulationRun" in summary.model_dump(by_alias=True)


@pytest.mark.parametrize("rename", [False, True])
@pytest.mark.parametrize("path", [
    ("id",), ("name",), ("run",), ("run", "simulator"),
    ("run", "simulator", "name"), ("run", "simulator", "version"),
])
def test_required_run_field_drift(path: tuple[str, ...], rename: bool) -> None:
    raw = payload()
    target = raw
    for key in path[:-1]:
        target = target[key]
    value = target.pop(path[-1])
    if rename:
        target["renamed"] = value
    with pytest.raises(ValidationError):
        map_run_summary(raw)
    project = payload("project")
    project["simulationRun"] = raw
    with pytest.raises(ValidationError):
        map_project_summary(project)


@pytest.mark.parametrize("rename", [False, True])
@pytest.mark.parametrize("key", ["id", "simulationRun", "created", "updated"])
def test_required_project_field_drift(key: str, rename: bool) -> None:
    raw = payload("project")
    value = raw.pop(key)
    if rename:
        raw["renamed"] = value
    with pytest.raises(ValidationError):
        map_project_summary(raw)


def test_extra_fields_are_stripped_at_every_level() -> None:
    raw = payload("project")
    run = raw["simulationRun"]
    for target in [raw, run, run["run"], run["run"]["simulator"], run["metadata"][0],
                   run["metadata"][0]["creators"][0]]:
        target.update(dict.fromkeys(["tasks", "outputs", "submitted", "owner", "unknownFutureField"], "unused"))
    owned = map_project_summary(raw).model_dump(by_alias=True)
    assert set(owned) == {"id", "created", "updated", "simulationRun"}
    assert set(owned["simulationRun"]) == {"id", "name", "run", "metadata"}
    assert set(owned["simulationRun"]["run"]) == {"simulator", "projectSize", "resultsSize"}
    assert set(owned["simulationRun"]["run"]["simulator"]) == {"name", "version"}
    for field in ["tasks", "outputs", "submitted", "owner", "unknownFutureField"]:
        assert f'"{field}"' not in json.dumps(owned)


@pytest.mark.parametrize("missing", [False, True])
def test_empty_or_missing_metadata(missing: bool) -> None:
    raw = payload()
    raw["metadata"] = []
    if missing:
        del raw["metadata"]
    assert map_run_summary(raw).metadata == []


def test_optional_leaves_and_first_metadata_only() -> None:
    raw = payload()
    raw["metadata"] = [{}, {"abstract": "not selected"}]
    del raw["run"]["projectSize"]
    del raw["run"]["resultsSize"]
    summary = map_run_summary(raw)
    assert summary.run.project_size is None
    assert summary.run.results_size is None
    assert len(summary.metadata) == 1
    assert summary.metadata[0].model_dump() == {
        "abstract": None, "description": None, "thumbnails": [],
        "creators": [], "keywords": [], "citations": [], "encodes": [],
    }


def test_null_metadata_entry_fails() -> None:
    raw = payload()
    raw["metadata"] = [None]
    with pytest.raises(ValidationError):
        map_run_summary(raw)
