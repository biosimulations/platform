"""Live contract checks for the passthrough routes against api.biosimulations.org.

Marked ``integration``: excluded from CI (`pytest -m "not integration"`) because
they need the network. Run them when you touch a mirror model, to catch upstream
shapes the offline fixtures do not encode:

    uv run pytest tests/legacy_proxy/test_live_upstream.py -m integration -v

TestClient is used WITHOUT its context manager, so the app lifespan never runs
and no Mongo/Temporal is needed -- but a real ``BiosimServiceRest`` is registered,
so each call goes route -> client -> live upstream.
"""

from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.dependencies import get_biosim_service, set_biosim_service

pytestmark = pytest.mark.integration

# A long-published project, stable since 2022.
_PROJECT_ID = "Yeast-cell-cycle-Irons-J-Theor-Biol-2009"
_KISAO_ID = "KISAO_0000019"  # CVODE


@pytest.fixture
def client() -> Iterator[TestClient]:
    previous = get_biosim_service()
    set_biosim_service(BiosimServiceRest())
    try:
        yield TestClient(app)
    finally:
        set_biosim_service(previous)


@pytest.fixture
def run_id(client: TestClient) -> str:
    response = client.get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 200
    run: str = response.json()["simulationRun"]["id"]
    return run


def test_project_summary_parses_live(client: TestClient) -> None:
    body = client.get(f"/projects/{_PROJECT_ID}/summary").json()
    assert body["id"] == _PROJECT_ID
    assert body["created"] and body["updated"]
    sim_run = body["simulationRun"]
    assert sim_run["id"] and sim_run["name"]
    assert sim_run["run"]["projectSize"] > 0
    assert sim_run["run"]["simulator"]["name"]


def test_run_timestamps_are_not_iso_and_must_stay_strings(client: TestClient) -> None:
    """Upstream sends JS `Date.toString()` here, e.g.
    'Sat Feb 05 2022 16:23:31 GMT+0000 (Coordinated Universal Time)'.

    Typing these as `datetime` would reject the real payload outright -- this is
    the regression guard for that.
    """
    sim_run = client.get(f"/projects/{_PROJECT_ID}/summary").json()["simulationRun"]
    for field in ("submitted", "updated"):
        assert isinstance(sim_run[field], str)


def test_run_summary_matches_the_embedded_one(client: TestClient, run_id: str) -> None:
    """The dedup that licenses skipping /runs/{id}/summary in project context."""
    embedded = client.get(f"/projects/{_PROJECT_ID}/summary").json()["simulationRun"]
    standalone = client.get(f"/runs/{run_id}/summary").json()
    assert standalone["id"] == embedded["id"]
    assert standalone["name"] == embedded["name"]
    assert standalone["run"]["resultsSize"] == embedded["run"]["resultsSize"]


def test_files_listing_is_an_array(client: TestClient, run_id: str) -> None:
    files = client.get(f"/files/{run_id}").json()
    assert isinstance(files, list) and files
    assert all(f["location"] for f in files)
    assert any("sed-ml" in (f["format"] or "") for f in files)


def test_specifications_is_an_array_with_serialized_model_refs(
    client: TestClient, run_id: str
) -> None:
    """Upstream returns an array, and tasks reference models by id, not inline."""
    response = client.get(f"/specifications/{run_id}")
    assert response.status_code == 200
    docs = response.json()
    assert isinstance(docs, list) and docs
    doc = docs[0]
    assert doc["tasks"] and isinstance(doc["tasks"][0]["model"], str)
    # The language lives on the sibling models array, as a bare URN string.
    assert doc["models"] and isinstance(doc["models"][0]["language"], str)
    assert doc["models"][0]["language"].startswith("urn:sedml:language:")
    assert {o["_type"] for o in doc["outputs"]} <= {"SedReport", "SedPlot2D", "SedPlot3D"}


def test_log_parses_at_every_level(client: TestClient, run_id: str) -> None:
    log = client.get(f"/logs/{run_id}").json()
    assert log["status"]
    doc = log["sedDocuments"][0]
    assert doc["location"]
    for output in doc["outputs"]:
        assert "status" in output


def test_results_accept_a_composite_output_id(client: TestClient, run_id: str) -> None:
    """The output id contains a '/' and must survive the round trip."""
    doc = client.get(f"/specifications/{run_id}").json()[0]
    location = (doc["id"] or "").removeprefix("./")
    output_id = f"{location}/{doc['outputs'][0]['id']}"

    body = client.get(f"/results/{run_id}/{output_id}").json()
    assert body["outputId"] == output_id
    assert body["data"] and body["data"][0]["values"]

    encoded = output_id.replace("/", "%2F")
    assert client.get(f"/results/{run_id}/{encoded}").json()["outputId"] == output_id


@pytest.mark.parametrize("kisao_id", [_KISAO_ID, _KISAO_ID.replace("_", ":")])
def test_kisao_resolves_for_both_spellings(client: TestClient, kisao_id: str) -> None:
    body = client.get(f"/ontologies/KISAO/{kisao_id}").json()
    assert body["name"] == "CVODE"
    assert body["description"]


def test_detail_aggregates_without_the_log_by_default(client: TestClient) -> None:
    body: dict[str, Any] = client.get(f"/projects/{_PROJECT_ID}/detail").json()
    assert body["summary"]["id"] == _PROJECT_ID
    assert body["files"] and body["specifications"]
    assert body["log"] is None

    with_log = client.get(f"/projects/{_PROJECT_ID}/detail?include=log").json()
    assert with_log["log"]["status"]


def test_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/projects/does-not-exist-xyz/summary").status_code == 404
