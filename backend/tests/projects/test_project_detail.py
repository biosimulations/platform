"""GET /projects/{id}/detail — the optional one-round-trip aggregate.

What these tests pin is the aggregation *policy*, not just the happy path:
which calls are mandatory, which degrade, which are conditional, and which must
never happen at all.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.biosim_api import (
    OutputResults,
    ProjectFile,
    ProjectSummary,
    RunLog,
    SedDocumentSpec,
)
from tests.projects.test_project_summary import _SUMMARY, _SUMMARY_JSON, _upstream_error

_PROJECT_ID = _SUMMARY_JSON["id"]
_RUN_ID = _SUMMARY_JSON["simulationRun"]["id"]

_FILES = [ProjectFile.model_validate({"location": "./model.xml", "size": 12, "format": "sbml"})]
_SPECS = [SedDocumentSpec.model_validate(
    {"id": "./simulation.sedml", "outputs": [{"_type": "SedPlot2D", "id": "plot_1"}]}
)]
_LOG = RunLog.model_validate({"status": "SUCCEEDED", "sedDocuments": []})


def _service(**overrides: Any) -> AsyncMock:
    """A biosim service with every aggregate dependency wired to succeed."""
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    biosim.get_run_files.return_value = _FILES
    biosim.get_run_specifications.return_value = _SPECS
    biosim.get_run_log.return_value = _LOG
    biosim.get_output_results.return_value = OutputResults()
    for key, value in overrides.items():
        setattr(biosim, key, value)
    return biosim


def _get(biosim: AsyncMock, url: str = f"/projects/{_PROJECT_ID}/detail") -> Any:
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        return TestClient(app).get(url)


def test_detail_composes_summary_files_and_specification() -> None:
    biosim = _service()
    response = _get(biosim)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["id"] == _PROJECT_ID
    assert body["summary"]["simulationRun"]["run"]["projectSize"] == 64521
    assert body["files"][0]["location"] == "./model.xml"
    assert body["specifications"][0]["outputs"][0]["_type"] == "SedPlot2D"
    assert body["log"] is None
    biosim.get_run_files.assert_awaited_once_with(_RUN_ID)
    biosim.get_run_specifications.assert_awaited_once_with(_RUN_ID)


def test_detail_never_refetches_the_embedded_run_summary() -> None:
    """The run summary is already inside the project summary."""
    biosim = _service()
    assert _get(biosim).status_code == 200
    biosim.get_run_summary.assert_not_awaited()


def test_detail_never_fetches_results_or_kisao() -> None:
    """Results are one call per plot with an unbounded payload; KISAO repeats per
    algorithm. Neither belongs in a page-load aggregate."""
    biosim = _service()
    assert _get(biosim).status_code == 200
    biosim.get_output_results.assert_not_awaited()
    biosim.get_kisao_term.assert_not_awaited()


def test_detail_does_not_fetch_the_log_by_default() -> None:
    biosim = _service()
    response = _get(biosim)
    assert response.status_code == 200
    assert response.json()["log"] is None
    biosim.get_run_log.assert_not_awaited()


def test_detail_fetches_the_log_only_when_requested() -> None:
    biosim = _service()
    response = _get(biosim, f"/projects/{_PROJECT_ID}/detail?include=log")
    assert response.status_code == 200
    assert response.json()["log"]["status"] == "SUCCEEDED"
    biosim.get_run_log.assert_awaited_once_with(_RUN_ID)


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


def test_detail_tolerates_a_files_failure() -> None:
    biosim = _service(get_run_files=AsyncMock(side_effect=_upstream_error(500)))
    response = _get(biosim)
    assert response.status_code == 200
    body = response.json()
    assert body["files"] == []
    # The mandatory part is untouched by a secondary failure.
    assert body["summary"]["id"] == _PROJECT_ID
    assert body["specifications"] != []


def test_detail_tolerates_a_specification_failure() -> None:
    biosim = _service(
        get_run_specifications=AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
    )
    response = _get(biosim)
    assert response.status_code == 200
    body = response.json()
    assert body["specifications"] == []
    assert body["files"][0]["location"] == "./model.xml"


def test_detail_tolerates_a_log_failure() -> None:
    biosim = _service(get_run_log=AsyncMock(side_effect=_upstream_error(404)))
    response = _get(biosim, f"/projects/{_PROJECT_ID}/detail?include=log")
    assert response.status_code == 200
    assert response.json()["log"] is None


def test_detail_tolerates_every_secondary_failing_at_once() -> None:
    biosim = _service(
        get_run_files=AsyncMock(side_effect=_upstream_error(500)),
        get_run_specifications=AsyncMock(side_effect=_upstream_error(500)),
        get_run_log=AsyncMock(side_effect=_upstream_error(500)),
    )
    response = _get(biosim, f"/projects/{_PROJECT_ID}/detail?include=log")
    assert response.status_code == 200
    body = response.json()
    assert (body["files"], body["specifications"], body["log"]) == ([], [], None)
    assert body["summary"]["id"] == _PROJECT_ID


# --------------------------------------------------------------------------
# mandatory call + missing identifier
# --------------------------------------------------------------------------


@pytest.mark.parametrize("upstream_status,expected", [(404, 404), (400, 400), (500, 502)])
def test_detail_fails_when_the_summary_fails(upstream_status: int, expected: int) -> None:
    """The summary is load-bearing -- its failure is the request's failure."""
    biosim = _service(get_project_summary=AsyncMock(side_effect=_upstream_error(upstream_status)))
    assert _get(biosim).status_code == expected


def test_detail_without_a_run_id_skips_every_dependent_call() -> None:
    """No run id means no key for a dependent request -- return the summary alone
    rather than building a malformed upstream URL."""
    summary = ProjectSummary.model_validate(
        {"id": _PROJECT_ID, "simulationRun": {"name": "no id", "metadata": []}}
    )
    biosim = _service(get_project_summary=AsyncMock(return_value=summary))
    response = _get(biosim, f"/projects/{_PROJECT_ID}/detail?include=log")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["id"] == _PROJECT_ID
    assert (body["files"], body["specifications"], body["log"]) == ([], [], None)
    biosim.get_run_files.assert_not_awaited()
    biosim.get_run_specifications.assert_not_awaited()
    biosim.get_run_log.assert_not_awaited()


def test_detail_is_anonymous_and_forwards_no_credentials() -> None:
    biosim = _service()
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(
            f"/projects/{_PROJECT_ID}/detail",
            headers={"Authorization": "Bearer caller-token"},
        )
    assert response.status_code == 200
    biosim.get_project_summary.assert_awaited_once_with(_PROJECT_ID)
    biosim.get_run_files.assert_awaited_once_with(_RUN_ID)


def test_detail_without_biosim_service_is_503() -> None:
    with patch("biosim_server.projects.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/projects/{_PROJECT_ID}/detail")
    assert response.status_code == 503


def test_detail_route_does_not_shadow_stats_or_summary() -> None:
    """Adding /{id}/detail must leave the existing project routes resolving."""
    projects_db = AsyncMock()
    projects_db.query_project_stats.return_value = []
    with patch("biosim_server.projects.router.get_project_database_service", return_value=projects_db):
        assert TestClient(app).get("/projects/stats").status_code == 200

    biosim = _service()
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        summary_response = TestClient(app).get(f"/projects/{_PROJECT_ID}/summary")
    assert summary_response.status_code == 200
    # The summary route is untouched: it returns the envelope, not the aggregate.
    assert "files" not in summary_response.json()
    assert summary_response.json()["id"] == _PROJECT_ID
