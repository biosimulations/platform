"""Tests for GET /projects/{id}/summary — the passthrough to biosimulations.org.

The project *detail* contract still belongs to the legacy API, so this route
forwards the id upstream and returns the body unchanged. Covered here:
  * the upstream URL actually requested (id propagation + quoting), against a
    patched aiohttp session, exercising ``BiosimServiceRest.get_project_summary``,
  * the FastAPI route — TestClient with a mocked biosim service — for the
    passthrough body, anonymous access, and upstream 404 / failure mapping.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import ClientResponseError
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest

# A trimmed sample of the upstream payload the project detail page consumes
# (frontend/app/pages/projects/[id].vue): the project envelope plus the nested
# SimulationRunSummary. Passed through verbatim, so its exact shape is opaque here.
_SUMMARY: dict[str, Any] = {
    "id": "BIOMD0000000012",
    "created": "2024-01-01T00:00:00.000Z",
    "updated": "2024-02-01T00:00:00.000Z",
    "simulationRun": {
        "id": "run-abc",
        "name": "Elowitz repressilator",
        "run": {"simulator": {"name": "COPASI", "version": "4.34.251"}},
        "metadata": [{"title": "Repressilator", "abstract": "A synthetic oscillator."}],
        "submitted": "2024-01-01T00:00:00.000Z",
        "updated": "2024-02-01T00:00:00.000Z",
    },
}


def _upstream_error(status: int) -> ClientResponseError:
    return ClientResponseError(request_info=MagicMock(), history=(), status=status)


def test_project_summary_returns_upstream_body_unchanged() -> None:
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get("/projects/BIOMD0000000012/summary")
    assert response.status_code == 200
    assert response.json() == _SUMMARY
    # The path id reaches the client verbatim; quoting happens at the HTTP layer.
    biosim.get_project_summary.assert_awaited_once_with("BIOMD0000000012")


def test_project_summary_needs_no_token() -> None:
    """The project catalog is anonymous-readable; the detail route stays that way."""
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get("/projects/BIOMD0000000012/summary")
    assert response.status_code == 200


def test_project_summary_forwards_no_caller_credentials() -> None:
    """A caller's Authorization header is not propagated into the upstream call."""
    biosim = AsyncMock()
    biosim.get_project_summary.return_value = _SUMMARY
    with patch("biosim_server.projects.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(
            "/projects/BIOMD0000000012/summary",
            headers={"Authorization": "Bearer some-caller-token"},
        )
    assert response.status_code == 200
    # The service takes the id and nothing else -- there is no header seam to leak.
    biosim.get_project_summary.assert_awaited_once_with("BIOMD0000000012")


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
        response = TestClient(app).get("/projects/BIOMD0000000012/summary")
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
        response = TestClient(app).get("/projects/BIOMD0000000012/summary")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "127.0.0.1" not in detail
    assert "Cannot connect" not in detail


def test_project_summary_without_biosim_service_is_503() -> None:
    with patch("biosim_server.projects.router.get_biosim_service", return_value=None):
        response = TestClient(app).get("/projects/BIOMD0000000012/summary")
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
    resp.json.return_value = _SUMMARY
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
        summary = await BiosimServiceRest().get_project_summary("BIOMD0000000012")

    assert summary == _SUMMARY
    session.get.assert_called_once_with(
        "https://api.biosimulations.org/projects/BIOMD0000000012/summary"
    )


@pytest.mark.asyncio
async def test_rest_client_quotes_the_project_id() -> None:
    """A hostile id stays one path segment instead of reshaping the upstream URL."""
    resp = AsyncMock()
    resp.json.return_value = {}
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
