"""GET /files/{run_id} — the run archive's file listing (a bare JSON array)."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.common.biosim_api import ProjectFile
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

_RUN_ID = "61fea483f499ccf25faafc4d"

_FILES_JSON: list[dict[str, Any]] = [
    {
        "id": f"{_RUN_ID}:simulation.sedml",
        "name": "simulation.sedml",
        "format": "http://identifiers.org/combine.specifications/sed-ml",
        "location": "./simulation.sedml",
        "size": 4023,
        "url": "https://files.biosimulations.org/x/simulation.sedml",
        "master": True,
        "simulationRun": _RUN_ID,
    },
    {
        "name": "plot.vg.json",
        "format": "http://purl.org/NET/mediatypes/application/vnd.vega.v5+json",
        "location": "plot.vg.json",
        "size": None,
        "url": None,
    },
]


@pytest.mark.asyncio
async def test_rest_client_parses_the_bare_array() -> None:
    patcher, session = stub_session(_FILES_JSON)
    with patcher:
        files = await BiosimServiceRest().get_run_files(_RUN_ID)
    session.get.assert_called_once_with(f"https://api.biosimulations.org/files/{_RUN_ID}")
    assert len(files) == 2
    assert files[0].location == "./simulation.sedml"
    assert files[0].size == 4023
    assert files[0].format == "http://identifiers.org/combine.specifications/sed-ml"
    assert files[0].url == "https://files.biosimulations.org/x/simulation.sedml"
    assert files[0].simulation_run == _RUN_ID
    # Null size/url must not raise; a Vega file is identified by its format URI.
    assert files[1].size is None
    assert files[1].url is None
    assert "vega" in (files[1].format or "")


@pytest.mark.asyncio
async def test_rest_client_handles_an_empty_listing() -> None:
    patcher, _ = stub_session([])
    with patcher:
        assert await BiosimServiceRest().get_run_files(_RUN_ID) == []


@pytest.mark.asyncio
async def test_rest_client_degrades_on_a_non_array_body() -> None:
    """The contract is an array; anything else degrades rather than 500s."""
    patcher, _ = stub_session({"unexpected": "object"})
    with patcher:
        assert await BiosimServiceRest().get_run_files(_RUN_ID) == []


@pytest.mark.asyncio
async def test_rest_client_quotes_the_run_id() -> None:
    patcher, session = stub_session([])
    with patcher:
        await BiosimServiceRest().get_run_files("../secret?x=1")
    session.get.assert_called_once_with(
        "https://api.biosimulations.org/files/..%2Fsecret%3Fx%3D1"
    )


def test_files_route_returns_array_with_upstream_keys() -> None:
    biosim = AsyncMock()
    biosim.get_run_files.return_value = [ProjectFile.model_validate(f) for f in _FILES_JSON]
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/files/{_RUN_ID}")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body[0]["location"] == "./simulation.sedml"
    assert body[0]["simulationRun"] == _RUN_ID
    biosim.get_run_files.assert_awaited_once_with(_RUN_ID)


def test_files_route_maps_upstream_404() -> None:
    biosim = AsyncMock()
    biosim.get_run_files.side_effect = upstream_error(404)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/files/{_RUN_ID}")
    assert response.status_code == 404


def test_files_route_without_biosim_service_is_503() -> None:
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/files/{_RUN_ID}")
    assert response.status_code == 503
