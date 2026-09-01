"""Tests for GET /projects/{id}/summary — the passthrough to biosimulations.org.

The project *detail* contract still belongs to the legacy API, so this route
forwards the id upstream and returns a typed nested envelope. Covered here:
  * the upstream URL actually requested (id propagation + quoting), against a
    patched aiohttp session, exercising ``BiosimServiceRest.get_project_summary``,
  * parsing of nested metadata/run fields (including null/empty extra items),
  * the FastAPI route — TestClient with a mocked biosim service — for the
    serialized camelCase body, anonymous access, and upstream 404 / failure mapping.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import ClientResponseError
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.common.biosim_api import ProjectSummary, SimulationRunSummary

_PROJECT_ID = "Yeast-cell-cycle-Irons-J-Theor-Biol-2009"

_SUMMARY_JSON: dict[str, Any] = {
    "id": _PROJECT_ID,
    "created": "2022-02-04T18:32:03.144Z",
    "updated": "2022-03-01T09:15:44.001Z",
    "simulationRun": {
        "id": "61fea483f499ccf25faafc4d",
        "name": "Budding yeast cell cycle",
        "submitted": "2022-02-04T18:30:11.934Z",
        "updated": "2022-02-04T18:31:41.807Z",
        "run": {
            "projectSize": 64521,
            "resultsSize": 10975,
            "status": "SUCCEEDED",
            "simulator": {
                "id": "ginsim",
                "name": "GINsim",
                "version": "3.0.0b",
                "digest": "sha256:7b884d0a",
                "url": "https://ginsim.org",
            },
        },
        "metadata": [
            {
                "abstract": "Boolean model of the budding yeast cell cycle.",
                "description": "Longer description...",
                "creators": [
                    {"uri": None, "label": "D. J. Irons"},
                    {"uri": None, "label": "A. Naldi"},
                ],
                "keywords": [
                    {"uri": None, "label": "cell cycle"},
                    {"uri": None, "label": "boolean"},
                ],
                "citations": [
                    {"uri": "https://doi.org/10.1016/j.jtbi.2009.01.006", "label": "Irons 2009"},
                ],
                "encodes": [
                    {"uri": "http://identifiers.org/taxonomy/4932", "label": "S. cerevisiae"},
                ],
                "thumbnails": ["Figure2.jpg"],
            },
            {
                "abstract": None,
                "description": None,
                "creators": [],
                "keywords": [],
                "thumbnails": [],
            },
        ],
    },
}

_SUMMARY = ProjectSummary.model_validate(_SUMMARY_JSON)


def _upstream_error(status: int) -> ClientResponseError:
    return ClientResponseError(request_info=MagicMock(), history=(), status=status)


def _assert_parsed_summary(summary: ProjectSummary) -> None:
    """Every modeled project-summary path, plus the null/empty second metadata item."""
    assert summary.id == _PROJECT_ID
    assert summary.created == "2022-02-04T18:32:03.144Z"
    # The project record's `updated`, NOT the run's -- different subjects.
    assert summary.updated == "2022-03-01T09:15:44.001Z"
    sim_run = summary.simulation_run
    assert sim_run.id == "61fea483f499ccf25faafc4d"
    assert sim_run.name == "Budding yeast cell cycle"
    assert sim_run.submitted == "2022-02-04T18:30:11.934Z"
    assert sim_run.updated == "2022-02-04T18:31:41.807Z"
    assert sim_run.updated != summary.updated
    meta0 = sim_run.metadata[0]
    meta1 = sim_run.metadata[1]
    assert meta0.abstract == "Boolean model of the budding yeast cell cycle."
    assert meta0.description == "Longer description..."
    assert [c.label for c in meta0.creators] == ["D. J. Irons", "A. Naldi"]
    assert [k.label for k in meta0.keywords] == ["cell cycle", "boolean"]
    assert [c.label for c in meta0.citations] == ["Irons 2009"]
    assert meta0.citations[0].uri == "https://doi.org/10.1016/j.jtbi.2009.01.006"
    assert [e.label for e in meta0.encodes] == ["S. cerevisiae"]
    assert meta0.encodes[0].uri == "http://identifiers.org/taxonomy/4932"
    assert meta0.thumbnails == ["Figure2.jpg"]
    assert sim_run.run is not None
    assert sim_run.run.project_size == 64521
    assert sim_run.run.results_size == 10975
    assert sim_run.run.simulator is not None
    # Display name from the nested simulator object; the flat /runs/{id} payload
    # carries the *slug* under a different name (simulator_id).
    assert sim_run.run.simulator.name == "GINsim"
    assert sim_run.run.simulator.version == "3.0.0b"
    assert meta1.abstract is None
    assert meta1.description is None
    assert meta1.creators == []
    assert meta1.keywords == []
    assert meta1.citations == []
    assert meta1.encodes == []
    assert meta1.thumbnails == []


def test_project_summary_returns_typed_nested_envelope() -> None:
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _PROJECT_ID
    sim_run = body["simulationRun"]
    meta0 = sim_run["metadata"][0]
    meta1 = sim_run["metadata"][1]
    assert meta0["abstract"] == "Boolean model of the budding yeast cell cycle."
    assert meta0["description"] == "Longer description..."
    assert meta0["creators"][0]["label"] == "D. J. Irons"
    assert meta0["keywords"][0]["label"] == "cell cycle"
    assert meta0["thumbnails"] == ["Figure2.jpg"]
    assert sim_run["run"]["projectSize"] == 64521
    assert sim_run["run"]["resultsSize"] == 10975
    assert meta1["abstract"] is None
    assert meta1["creators"] == []
    assert meta1["keywords"] == []
    assert meta1["thumbnails"] == []
    assert meta0["citations"][0]["label"] == "Irons 2009"
    assert meta0["encodes"][0]["label"] == "S. cerevisiae"
    assert sim_run["run"]["simulator"]["name"] == "GINsim"
    assert body["created"] == "2022-02-04T18:32:03.144Z"
    assert body["updated"] == "2022-03-01T09:15:44.001Z"
    # extra="allow" should round-trip unmodeled keys (verify, do not assume).
    assert sim_run["run"]["status"] == "SUCCEEDED"
    assert sim_run["id"] == "61fea483f499ccf25faafc4d"
    # The path id reaches the client verbatim; quoting happens at the HTTP layer.
    biosim.get_project_summary.assert_awaited_once_with(_PROJECT_ID)


def test_project_summary_needs_no_token() -> None:
    """The project catalog is anonymous-readable; the detail route stays that way."""
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 200


def test_project_summary_forwards_no_caller_credentials() -> None:
    """A caller's Authorization header is not propagated into the upstream call."""
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(
            f"/projects/{_PROJECT_ID}/summary",
            headers={"Authorization": "Bearer some-caller-token"},
        )
    assert response.status_code == 200
    # The service takes the id and nothing else -- there is no header seam to leak.
    biosim.get_project_summary.assert_awaited_once_with(_PROJECT_ID)


def test_project_summary_upstream_404_is_404() -> None:
    biosim = AsyncMock()
    biosim.get_project_summary.side_effect = _upstream_error(404)
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get("/projects/nope/summary")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_project_summary_upstream_error_is_502() -> None:
    """A 5xx upstream is a gateway failure on our side of the call."""
    biosim = AsyncMock()
    biosim.get_project_summary.side_effect = _upstream_error(500)
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 502


def test_project_summary_upstream_4xx_is_forwarded_not_502() -> None:
    """An upstream 4xx is caused by the caller's id, so it is forwarded as-is.

    biosimulations.org 400s a malformed project id; reporting that as 502 would
    blame the gateway for a bad request. Verified live: an id of ``abc?x=1``
    upstream-400s.
    """
    biosim = AsyncMock()
    biosim.get_project_summary.side_effect = _upstream_error(400)
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get("/projects/abc/summary")
    assert response.status_code == 400
    assert "400" in response.json()["detail"]


def test_project_summary_upstream_unreachable_is_502() -> None:
    """A transport failure is a 502, and the response must not echo the upstream address."""
    biosim = AsyncMock()
    biosim.get_project_summary.side_effect = aiohttp.ClientConnectionError(
        "Cannot connect to host 127.0.0.1:9 ssl:default"
    )
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "127.0.0.1" not in detail
    assert "Cannot connect" not in detail


def test_project_summary_without_biosim_service_is_503() -> None:
    with patch("biosim_server.projects.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 503


def test_project_summary_route_does_not_shadow_stats() -> None:
    """`/projects/stats` must keep resolving to the facet endpoint, not `{id}/summary`."""
    projects_db = AsyncMock()
    projects_db.query_project_stats.return_value = []
    with patch("biosim_server.projects.router.get_project_database_service", return_value=projects_db):
        response = TestClient(app).get("/projects/stats")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_rest_client_requests_the_upstream_summary_url() -> None:
    """BiosimServiceRest hits {biosimulations_api}/projects/{id}/summary."""
    resp = AsyncMock()
    resp.json.return_value = _SUMMARY_JSON
    resp.raise_for_status = MagicMock()

    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=session_cm):
        summary = await BiosimServiceRest().get_project_summary(_PROJECT_ID)

    assert summary == _SUMMARY
    _assert_parsed_summary(summary)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/projects/{_PROJECT_ID}/summary"
    )


@pytest.mark.asyncio
async def test_rest_client_quotes_the_project_id() -> None:
    """A hostile id stays one path segment instead of reshaping the upstream URL."""
    resp = AsyncMock()
    resp.json.return_value = _SUMMARY_JSON
    resp.raise_for_status = MagicMock()

    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=session_cm):
        await BiosimServiceRest().get_project_summary("../runs/secret?x=1")

    session.get.assert_called_once_with(
        "https://api.biosimulations.org/projects/..%2Fruns%2Fsecret%3Fx%3D1/summary"
    )


def _assert_subset(expected: Any, actual: Any, path: str = "") -> None:
    """Every key/value in `expected` survives into `actual`, recursively.

    `actual` may carry *more* keys (defaults for fields the fixture omits); it
    may never drop or rename one.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {type(actual)}"
        for key, value in expected.items():
            assert key in actual, f"{path}/{key} was dropped from the serialized body"
            _assert_subset(value, actual[key], f"{path}/{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected an array, got {type(actual)}"
        assert len(expected) == len(actual), f"{path}: array length changed"
        for i, item in enumerate(expected):
            _assert_subset(item, actual[i], f"{path}[{i}]")
    else:
        assert expected == actual, f"{path}: {expected!r} != {actual!r}"


def test_project_summary_roundtrip_preserves_upstream_wire_keys() -> None:
    """Typing a field must not change the key it serializes under.

    `created`, `updated`, `simulationRun.id/name` and the nested simulator used
    to survive only via extra="allow". Now that they are declared fields, their
    aliases must reproduce the upstream camelCase keys byte for byte.
    """
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 200
    _assert_subset(_SUMMARY_JSON, response.json())


def test_project_summary_without_run_object_parses() -> None:
    """A project whose simulationRun has no `run` block must not 500.

    `run` used to be a required field, so a sparse upstream payload raised
    ValidationError inside the route -- which catches only aiohttp errors and
    therefore surfaced as an unhandled 500.
    """
    payload: dict[str, Any] = {
        "id": _PROJECT_ID,
        "simulationRun": {"id": "abc123", "name": "no run block", "metadata": []},
    }
    summary = ProjectSummary.model_validate(payload)
    assert summary.simulation_run.run is None

    biosim = AsyncMock()
    biosim.get_project_summary.return_value = summary
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert response.status_code == 200
    assert response.json()["simulationRun"]["run"] is None


def test_project_summary_tolerates_a_sparse_metadata_block() -> None:
    """Absent metadata/creators/keywords are empty lists, never None or a stub."""
    summary = ProjectSummary.model_validate({"id": "p", "simulationRun": {}})
    assert summary.simulation_run.metadata == []
    assert summary.simulation_run.id is None
    assert summary.simulation_run.run is None

    with_meta = ProjectSummary.model_validate(
        {"id": "p", "simulationRun": {"metadata": [{}], "run": {}}}
    )
    meta = with_meta.simulation_run.metadata[0]
    assert (meta.creators, meta.keywords, meta.citations, meta.encodes) == ([], [], [], [])
    assert meta.thumbnails == []
    assert meta.abstract is None
    assert with_meta.simulation_run.run is not None
    # An empty run block yields no synthesized simulator -- absence is meaningful.
    assert with_meta.simulation_run.run.simulator is None
    assert with_meta.simulation_run.run.project_size is None


def test_embedded_simulation_run_is_the_shared_run_summary_type() -> None:
    """`simulationRun` and GET /runs/{id}/summary are the same upstream object.

    This is what licenses skipping the redundant /runs/{id}/summary call in
    project context -- if the shapes ever diverge, this test fails first.
    """
    embedded = _SUMMARY_JSON["simulationRun"]
    standalone = SimulationRunSummary.model_validate(embedded)
    assert standalone == _SUMMARY.simulation_run
    assert standalone.id == "61fea483f499ccf25faafc4d"
    assert standalone.run is not None
    assert standalone.run.results_size == 10975
