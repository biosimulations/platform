"""The project catalog is a published, anonymous-readable catalog and stays that way.

There are no private projects in this backend (project write/publication
architecture is still an open product decision), so gating these reads behind
Auth0 would be a regression, not a hardening. These tests are the guard rail
against that happening by accident while ownership/visibility spread across the
rest of the API.
"""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.projects.models import ProjectStubPage


def test_list_projects_needs_no_token() -> None:
    projects_db = AsyncMock()
    projects_db.query_project_stubs.return_value = ([], 0)
    with patch("biosim_server.projects.router.get_project_database_service", return_value=projects_db):
        response = TestClient(app).get("/projects")
    assert response.status_code == 200
    assert response.json() == ProjectStubPage(items=[], total=0).model_dump(by_alias=True)


def test_project_stats_needs_no_token() -> None:
    projects_db = AsyncMock()
    projects_db.query_project_stats.return_value = []
    with patch("biosim_server.projects.router.get_project_database_service", return_value=projects_db):
        response = TestClient(app).get("/projects/stats")
    assert response.status_code == 200
    assert response.json() == []


def test_reindex_is_not_anonymously_callable() -> None:
    """The one write-ish project route is gated (503 while its token is unset)."""
    assert TestClient(app).post("/projects/reindex").status_code in (401, 403, 503)
