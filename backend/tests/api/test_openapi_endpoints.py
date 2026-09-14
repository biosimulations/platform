"""OpenAPI-driven contract probes for every documented biosim-server operation.

Operations are sourced from ``app.openapi()`` so a new route without a probe
fails the meta-test. Auth expectations follow the implementation (optional vs
required bearer), not the FastAPI-emitted ``security: [HTTPBearer]`` field.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import assert_never
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.common.auth import get_current_user
from biosim_server.rbac_demo.models import PublicMessage
from biosim_server.version import __version__
from tests.fixtures.auth_fixtures import make_authenticated_user

_PATH_PARAMS = ("processing_id", "workflow_id", "run_id", "project_id")
_HIDDEN_PATHS = frozenset({"/health", "/ready", "/docs", "/openapi.json"})
_CORE_PATHS = frozenset({
    "/compatibility/check",
    "/simulations/run",
    "/simulations/runs",
    "/simulations/{processing_id}",
    "/simulations/{processing_id}/status",
    "/simulations/{processing_id}/results",
    "/simulations/{processing_id}/logs",
    "/simulations/{processing_id}/cancel",
    "/runs/{run_id}/summary",
    "/runs/{run_id}/page",
    "/projects",
    "/projects/reindex",
    "/projects/stats",
    "/projects/{project_id}/summary",
    "/projects/{project_id}/page",
    "/api/v1/me",
    "/api/v1/demo/public",
    "/api/v1/demo/private/me",
    "/api/v1/demo/private/animal",
    "/",
    "/version",
    "/verify/omex",
    "/verify/{workflow_id}",
    "/verify/runs",
})
_OPTIONAL_AUTH_OPERATION_IDS = frozenset({"run-simulations", "list-simulation-runs"})
_REQUIRED_AUTH_OPERATION_IDS = frozenset({
    "delete-simulation-run",
    "cancel-simulation-run",
    "get-current-user",
    "update-current-user",
    "delete-current-user",
    "demo-private-whoami",
    "demo-private-animal",
})


class AuthMode(Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"
    REQUIRED_ROLES = "required_roles"
    REINDEX_TOKEN = "reindex_token"


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    tags: tuple[str, ...]
    advertises_bearer: bool


def _operations() -> list[Operation]:
    spec = app.openapi()
    ops: list[Operation] = []
    for path, methods in spec["paths"].items():
        for method, raw in methods.items():
            if method.startswith("x-") or method == "parameters":
                continue
            if not isinstance(raw, dict):
                continue
            operation_id = raw.get("operationId")
            assert isinstance(operation_id, str), f"missing operationId for {method} {path}"
            tags_raw = raw.get("tags") or []
            assert isinstance(tags_raw, list)
            ops.append(
                Operation(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    tags=tuple(str(tag) for tag in tags_raw),
                    advertises_bearer=bool(raw.get("security")),
                )
            )
    return sorted(ops, key=lambda op: (op.path, op.method))


OPERATIONS: tuple[Operation, ...] = tuple(_operations())
OPERATION_IDS: frozenset[str] = frozenset(op.operation_id for op in OPERATIONS)

AUTH_MODE: dict[str, AuthMode] = {
    "check-compatibility": AuthMode.NONE,
    "run-simulations": AuthMode.OPTIONAL,
    "list-simulation-runs": AuthMode.OPTIONAL,
    "get-simulation-status": AuthMode.NONE,
    "delete-simulation-run": AuthMode.REQUIRED_ROLES,
    "get-simulation-status-explicit": AuthMode.NONE,
    "get-simulation-results": AuthMode.NONE,
    "get-simulation-logs": AuthMode.NONE,
    "cancel-simulation-run": AuthMode.REQUIRED,
    "get-run-summary": AuthMode.NONE,
    "get-run-page": AuthMode.NONE,
    "list-projects": AuthMode.NONE,
    "reindex-projects": AuthMode.REINDEX_TOKEN,
    "list-project-stats": AuthMode.NONE,
    "get-project-summary": AuthMode.NONE,
    "get-project-page": AuthMode.NONE,
    "get-current-user": AuthMode.REQUIRED,
    "update-current-user": AuthMode.REQUIRED,
    "delete-current-user": AuthMode.REQUIRED,
    "demo-public": AuthMode.NONE,
    "demo-private-whoami": AuthMode.REQUIRED,
    "demo-private-animal": AuthMode.REQUIRED_ROLES,
    "root__get": AuthMode.NONE,
    "get_version_version_get": AuthMode.NONE,
    "verify-omex": AuthMode.NONE,
    "get-verify-output": AuthMode.NONE,
    "verify-runs": AuthMode.NONE,
}

VALIDATION_SKIP: dict[str, str] = {
    "get-simulation-status": "path-only; omitting the id is a different route",
    "get-simulation-status-explicit": "path-only; omitting the id is a different route",
    "get-simulation-results": "path-only; omitting the id is a different route",
    "get-simulation-logs": "path-only; omitting the id is a different route",
    "delete-simulation-run": "path-only; unauthenticated probe is 401",
    "cancel-simulation-run": "path-only; unauthenticated probe is 401",
    "get-run-summary": "path-only; unauthenticated probe uses encoded-dot 404",
    "get-run-page": "path-only; unauthenticated probe uses encoded-dot 404",
    "get-project-summary": "path-only; unauthenticated probe uses encoded-dot 404",
    "get-project-page": "path-only; unauthenticated probe uses encoded-dot 404",
    "reindex-projects": "no body; gated by static token, not Pydantic",
    "get-current-user": "no request body",
    "delete-current-user": "no request body",
    "demo-public": "no request body",
    "demo-private-whoami": "no request body",
    "demo-private-animal": "no request body",
    "root__get": "no request body",
    "get_version_version_get": "no request body",
    "get-verify-output": "path-only; unauthenticated probe is mocked 404",
    "verify-runs": "all query params optional; start is mocked in the unauth probe",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.pop(get_current_user, None)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _concrete_path(path: str) -> str:
    concrete = path
    for name in _PATH_PARAMS:
        concrete = concrete.replace("{" + name + "}", "probe-id")
    return concrete


def _assert_status(response: httpx.Response, expected: int) -> None:
    assert response.status_code == expected, response.text


def _probe_check_compatibility(client: TestClient) -> None:
    _assert_status(client.post("/compatibility/check"), 400)


def _probe_run_simulations(client: TestClient) -> None:
    _assert_status(client.post("/simulations/run"), 422)


def _probe_list_simulation_runs(client: TestClient) -> None:
    with patch(
        "biosim_server.simulations.router.get_simulation_run_database_service",
        return_value=None,
    ):
        _assert_status(client.post("/simulations/runs", json={}), 503)


def _probe_get_simulation_status(client: TestClient) -> None:
    with patch("biosim_server.simulations.router.get_temporal_client", return_value=None):
        _assert_status(client.get("/simulations/probe-id"), 503)


def _probe_get_simulation_status_explicit(client: TestClient) -> None:
    with patch("biosim_server.simulations.router.get_temporal_client", return_value=None):
        _assert_status(client.get("/simulations/probe-id/status"), 503)


def _probe_get_simulation_results(client: TestClient) -> None:
    with patch(
        "biosim_server.simulations.router.get_simulation_run_database_service",
        return_value=None,
    ):
        _assert_status(client.get("/simulations/probe-id/results"), 503)


def _probe_get_simulation_logs(client: TestClient) -> None:
    with patch(
        "biosim_server.simulations.router.get_simulation_run_database_service",
        return_value=None,
    ):
        _assert_status(client.get("/simulations/probe-id/logs"), 503)


def _probe_get_run_page(client: TestClient) -> None:
    _assert_status(client.get("/runs/%2E/page"), 404)


def _probe_get_run_summary(client: TestClient) -> None:
    _assert_status(client.get("/runs/%2E/summary"), 404)


def _probe_list_projects(client: TestClient) -> None:
    with patch("biosim_server.projects.router.get_project_database_service", return_value=None):
        _assert_status(client.get("/projects"), 503)


def _probe_reindex_projects(client: TestClient) -> None:
    with patch("biosim_server.projects.router.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(project_reindex_token="")
        _assert_status(client.post("/projects/reindex"), 503)


def _probe_list_project_stats(client: TestClient) -> None:
    with patch("biosim_server.projects.router.get_project_database_service", return_value=None):
        _assert_status(client.get("/projects/stats"), 503)


def _probe_get_project_page(client: TestClient) -> None:
    _assert_status(client.get("/projects/%2E/page"), 404)


def _probe_get_project_summary(client: TestClient) -> None:
    _assert_status(client.get("/projects/%2E/summary"), 404)


def _probe_demo_public(client: TestClient) -> None:
    response = client.get("/api/v1/demo/public")
    _assert_status(response, 200)
    PublicMessage.model_validate(response.json())


def _probe_root(client: TestClient) -> None:
    response = client.get("/")
    _assert_status(response, 200)
    body = response.json()
    assert body == {"docs": "https://biosim.biosimulations.org/docs", "version": __version__}


def _probe_version(client: TestClient) -> None:
    response = client.get("/version")
    _assert_status(response, 200)
    assert response.json() == __version__


def _probe_verify_omex(client: TestClient) -> None:
    _assert_status(client.post("/verify/omex"), 422)


def _probe_get_verify_output(client: TestClient) -> None:
    temporal = MagicMock()
    handle = AsyncMock()
    handle.query.side_effect = Exception("workflow not found")
    temporal.get_workflow_handle.return_value = handle
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        response = client.get("/verify/probe-id")
    _assert_status(response, 404)
    assert "probe-id" in response.json()["detail"]


def _probe_verify_runs(client: TestClient) -> None:
    async def start_workflow(*_args: object, **kwargs: object) -> MagicMock:
        handle = MagicMock()
        handle.id = str(kwargs["id"])
        handle.run_id = "run-probe"
        return handle

    temporal = MagicMock()
    temporal.start_workflow = start_workflow
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        response = client.post("/verify/runs")
    _assert_status(response, 200)
    body = VerifyWorkflowOutput.model_validate(response.json())
    assert body.workflow_status == VerifyWorkflowStatus.PENDING
    assert body.workflow_id.startswith("runs-verification-")


UNAUTHENTICATED_RUNNERS: dict[str, Callable[[TestClient], None]] = {
    "check-compatibility": _probe_check_compatibility,
    "run-simulations": _probe_run_simulations,
    "list-simulation-runs": _probe_list_simulation_runs,
    "get-simulation-status": _probe_get_simulation_status,
    "get-simulation-status-explicit": _probe_get_simulation_status_explicit,
    "get-simulation-results": _probe_get_simulation_results,
    "get-simulation-logs": _probe_get_simulation_logs,
    "get-run-summary": _probe_get_run_summary,
    "get-run-page": _probe_get_run_page,
    "list-projects": _probe_list_projects,
    "reindex-projects": _probe_reindex_projects,
    "list-project-stats": _probe_list_project_stats,
    "get-project-summary": _probe_get_project_summary,
    "get-project-page": _probe_get_project_page,
    "demo-public": _probe_demo_public,
    "root__get": _probe_root,
    "get_version_version_get": _probe_version,
    "verify-omex": _probe_verify_omex,
    "get-verify-output": _probe_get_verify_output,
    "verify-runs": _probe_verify_runs,
}


def _validate_check_compatibility(client: TestClient) -> None:
    _assert_status(client.post("/compatibility/check"), 400)


def _validate_run_simulations(client: TestClient) -> None:
    _assert_status(client.post("/simulations/run", json={}), 422)


def _validate_list_simulation_runs(client: TestClient) -> None:
    _assert_status(client.post("/simulations/runs", json={"type": "bogus"}), 422)


def _validate_list_projects(client: TestClient) -> None:
    _assert_status(client.get("/projects", params={"page": 0}), 422)


def _validate_list_project_stats(client: TestClient) -> None:
    with patch("biosim_server.projects.router.get_project_database_service", return_value=AsyncMock()):
        _assert_status(client.get("/projects/stats", params={"filters": "{not json"}), 400)


def _validate_update_current_user(client: TestClient) -> None:
    user = make_authenticated_user()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        _assert_status(client.patch("/api/v1/me", json={"name": ""}), 422)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _validate_verify_omex(client: TestClient) -> None:
    _assert_status(client.post("/verify/omex"), 422)


VALIDATION_RUNNERS: dict[str, Callable[[TestClient], None]] = {
    "check-compatibility": _validate_check_compatibility,
    "run-simulations": _validate_run_simulations,
    "list-simulation-runs": _validate_list_simulation_runs,
    "list-projects": _validate_list_projects,
    "list-project-stats": _validate_list_project_stats,
    "update-current-user": _validate_update_current_user,
    "verify-omex": _validate_verify_omex,
}


def test_every_operation_id_is_accounted_for() -> None:
    """Every documented operationId has an auth mode, unauth probe, and validation entry."""
    assert OPERATION_IDS == frozenset(AUTH_MODE)
    accounted_unauth = frozenset(UNAUTHENTICATED_RUNNERS) | _REQUIRED_AUTH_OPERATION_IDS
    assert OPERATION_IDS == accounted_unauth
    assert OPERATION_IDS == frozenset(VALIDATION_RUNNERS) | frozenset(VALIDATION_SKIP)
    assert frozenset(VALIDATION_RUNNERS).isdisjoint(VALIDATION_SKIP)


def test_openapi_lists_expected_core_paths() -> None:
    assert _CORE_PATHS <= set(app.openapi()["paths"])


def test_hidden_routes_are_not_in_openapi_paths() -> None:
    assert _HIDDEN_PATHS.isdisjoint(app.openapi()["paths"])


def test_openapi_json_document_matches_in_process_spec(client: TestClient) -> None:
    response = client.get("/openapi.json")
    _assert_status(response, 200)
    body = response.json()
    assert body["openapi"].startswith("3.")
    assert body["info"]["title"] == "biosim-server"
    assert set(body["paths"]) == set(app.openapi()["paths"])


def test_optional_auth_operations_advertise_http_bearer() -> None:
    """FastAPI emits HTTPBearer for get_optional_user even though anonymous calls work."""
    spec = app.openapi()
    for operation in OPERATIONS:
        if operation.operation_id not in _OPTIONAL_AUTH_OPERATION_IDS:
            continue
        assert operation.advertises_bearer
        assert spec["paths"][operation.path][operation.method.lower()].get("security") == [
            {"HTTPBearer": []}
        ]


def test_required_auth_operations_advertise_http_bearer() -> None:
    for operation in OPERATIONS:
        if operation.operation_id not in _REQUIRED_AUTH_OPERATION_IDS:
            continue
        assert operation.advertises_bearer


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.operation_id)
def test_unauthenticated_probe(operation: Operation, client: TestClient) -> None:
    """No bearer token: 401 for required auth, otherwise the cheap mounted-route status."""
    mode = AUTH_MODE[operation.operation_id]
    match mode:
        case AuthMode.REQUIRED | AuthMode.REQUIRED_ROLES:
            response = client.request(operation.method, _concrete_path(operation.path))
            _assert_status(response, 401)
        case AuthMode.NONE | AuthMode.OPTIONAL | AuthMode.REINDEX_TOKEN:
            UNAUTHENTICATED_RUNNERS[operation.operation_id](client)
        case _ as unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.operation_id)
def test_validation_error_probe(operation: Operation, client: TestClient) -> None:
    """Invalid required input yields 422, or the handler's actual 400, when applicable."""
    runner = VALIDATION_RUNNERS.get(operation.operation_id)
    if runner is None:
        assert operation.operation_id in VALIDATION_SKIP
        return
    runner(client)


def test_page_response_schemas_and_auth() -> None:
    spec = app.openapi()
    for path, model in [
        ("/projects/{project_id}/page", "ProjectsPagePayload"),
        ("/runs/{run_id}/page", "RunsPagePayload"),
    ]:
        operation = spec["paths"][path]["get"]
        assert not operation.get("security")
        assert AUTH_MODE[operation["operationId"]] == AuthMode.NONE
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}",
        }
