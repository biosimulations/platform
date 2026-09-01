"""GET /runs/{id}/summary — the run page's entry point.

The same object is embedded in a project summary under ``simulationRun``, so
this route exists for the *run* context only. Covered here: the upstream URL and
id quoting, the serialized camelCase body, anonymous access, and the shared
upstream-error mapping.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest, _sim_run_from_response
from biosim_server.biosim_runs.models import BiosimulatorVersion
from biosim_server.common.biosim_api import SimulationRunSummary
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

_RUN_ID = "61fea483f499ccf25faafc4d"

_RUN_SUMMARY_JSON: dict[str, Any] = {
    "id": _RUN_ID,
    "name": "Budding yeast cell cycle",
    "submitted": "2022-02-04T18:30:11.934Z",
    "updated": "2022-02-04T18:31:41.807Z",
    "run": {
        "projectSize": 64521,
        "resultsSize": 10975,
        "status": "SUCCEEDED",
        "simulator": {"id": "ginsim", "name": "GINsim", "version": "3.0.0b"},
    },
    "metadata": [
        {
            "abstract": "Boolean model of the budding yeast cell cycle.",
            "description": "Longer description...",
            "creators": [{"uri": None, "label": "D. J. Irons"}],
            "keywords": [{"uri": None, "label": "cell cycle"}],
            "thumbnails": ["Figure2.jpg"],
        }
    ],
}

_RUN_SUMMARY = SimulationRunSummary.model_validate(_RUN_SUMMARY_JSON)


def _patched_service(**attrs: Any) -> Any:
    biosim = AsyncMock()
    for key, value in attrs.items():
        setattr(getattr(biosim, key), "return_value", value)
    return biosim


# --------------------------------------------------------------------------
# GET /runs/{id} -- the flat run payload feeding BiosimSimulationRun
# --------------------------------------------------------------------------


def _simulator_version() -> BiosimulatorVersion:
    return BiosimulatorVersion(
        id="copasi", name="COPASI", version="4.34.251",
        image_url="ghcr.io/biosimulators/copasi:4.34.251", image_digest="sha256:abc",
        created="2021-08-15T00:23:05.813Z", updated="2021-12-22T19:13:28.331Z",
    )


def test_run_response_captures_simulator_id_and_version_string() -> None:
    """The upstream slug and version string are kept alongside the resolved object."""
    res = {
        "id": "abc123", "name": "run", "status": "SUCCEEDED",
        "simulator": "copasi", "simulatorVersion": "4.34.251",
    }
    run = _sim_run_from_response(res, _simulator_version())
    assert run.simulator_id == "copasi"
    assert run.simulator_version_string == "4.34.251"
    # The richer resolved object is untouched and still occupies `simulator_version`.
    assert run.simulator_version.image_digest == "sha256:abc"
    assert run.simulator_version.name == "COPASI"


def test_run_response_without_simulator_fields_is_none() -> None:
    """Older/in-flight payloads omit them; that must not raise."""
    res = {"id": "abc123", "name": "run", "status": "QUEUED"}
    run = _sim_run_from_response(res, _simulator_version())
    assert run.simulator_id is None
    assert run.simulator_version_string is None


# --------------------------------------------------------------------------
# BiosimServiceRest.get_run_summary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_client_requests_the_upstream_run_summary_url() -> None:
    patcher, session = stub_session(_RUN_SUMMARY_JSON)
    with patcher:
        summary = await BiosimServiceRest().get_run_summary(_RUN_ID)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/runs/{_RUN_ID}/summary"
    )
    assert summary.id == _RUN_ID
    assert summary.run is not None
    assert summary.run.project_size == 64521
    assert summary.run.simulator is not None
    assert summary.run.simulator.name == "GINsim"


@pytest.mark.asyncio
async def test_rest_client_quotes_the_run_id() -> None:
    """A hostile id stays one path segment instead of reshaping the upstream URL."""
    patcher, session = stub_session(_RUN_SUMMARY_JSON)
    with patcher:
        await BiosimServiceRest().get_run_summary("../projects/secret?x=1")
    session.get.assert_called_once_with(
        "https://api.biosimulations.org/runs/..%2Fprojects%2Fsecret%3Fx%3D1/summary"
    )


# --------------------------------------------------------------------------
# route
# --------------------------------------------------------------------------


def test_run_summary_route_returns_camelcase_body() -> None:
    biosim = _patched_service(get_run_summary=_RUN_SUMMARY)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/runs/{_RUN_ID}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["projectSize"] == 64521
    assert body["run"]["resultsSize"] == 10975
    assert body["run"]["simulator"]["name"] == "GINsim"
    assert body["metadata"][0]["thumbnails"] == ["Figure2.jpg"]
    # extra="allow" keeps unmodeled upstream keys.
    assert body["run"]["status"] == "SUCCEEDED"
    biosim.get_run_summary.assert_awaited_once_with(_RUN_ID)


def test_run_summary_route_is_anonymous_and_forwards_no_credentials() -> None:
    biosim = _patched_service(get_run_summary=_RUN_SUMMARY)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(
            f"/runs/{_RUN_ID}/summary", headers={"Authorization": "Bearer caller-token"}
        )
    assert response.status_code == 200
    # The service takes the id and nothing else -- there is no header seam to leak.
    biosim.get_run_summary.assert_awaited_once_with(_RUN_ID)


@pytest.mark.parametrize(
    "upstream_status,expected",
    [(404, 404), (400, 400), (403, 403), (500, 502), (503, 502)],
)
def test_run_summary_route_maps_upstream_status(upstream_status: int, expected: int) -> None:
    biosim = AsyncMock()
    biosim.get_run_summary.side_effect = upstream_error(upstream_status)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/runs/{_RUN_ID}/summary")
    assert response.status_code == expected


def test_run_summary_route_transport_failure_hides_upstream_address() -> None:
    biosim = AsyncMock()
    biosim.get_run_summary.side_effect = aiohttp.ClientConnectionError(
        "Cannot connect to host 127.0.0.1:9 ssl:default"
    )
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/runs/{_RUN_ID}/summary")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "127.0.0.1" not in detail
    assert "Cannot connect" not in detail


def test_run_summary_route_without_biosim_service_is_503() -> None:
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/runs/{_RUN_ID}/summary")
    assert response.status_code == 503
