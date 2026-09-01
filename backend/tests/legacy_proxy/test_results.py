"""GET /results/{run_id}/{output_id} — lazily fetched numeric output.

Two things matter here beyond parsing: the composite output id survives the trip
(it contains a '/'), and the values array stays permissive enough for repeated
tasks, whose results are nested rather than flat.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.common.biosim_api import OutputResults
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

_RUN_ID = "61fea483f499ccf25faafc4d"
_OUTPUT_ID = "simulation.sedml/plot_1"

_RESULTS_JSON: dict[str, Any] = {
    "outputId": _OUTPUT_ID,
    "data": [
        {"id": "gen_time", "label": "time", "values": [0.0, 0.5, 1.0]},
        {"id": "gen_x", "label": "X", "values": [1.0, 0.8, 0.6]},
    ],
}


def test_results_parse_flat_values() -> None:
    results = OutputResults.model_validate(_RESULTS_JSON)
    assert results.output_id == _OUTPUT_ID
    assert [d.id for d in results.data] == ["gen_time", "gen_x"]
    assert results.data[0].label == "time"
    assert results.data[0].values == [0.0, 0.5, 1.0]


def test_results_accept_nested_values_from_repeated_tasks() -> None:
    """A repeated task nests its results; list[float] would reject this outright."""
    results = OutputResults.model_validate(
        {
            "outputId": _OUTPUT_ID,
            "data": [{"id": "g", "label": "X", "values": [[1.0, 2.0], [3.0, 4.0]]}],
        }
    )
    assert results.data[0].values == [[1.0, 2.0], [3.0, 4.0]]

    deeper = OutputResults.model_validate(
        {"data": [{"id": "g", "values": [[[1.0], [2.0]], [[3.0], [4.0]]]}]}
    )
    assert deeper.data[0].values[0][0] == [1.0]


def test_results_tolerate_empty_and_missing_data() -> None:
    assert OutputResults.model_validate({"outputId": _OUTPUT_ID, "data": []}).data == []
    empty = OutputResults.model_validate({})
    assert empty.data == []
    assert empty.output_id is None
    no_values = OutputResults.model_validate({"data": [{"id": "g"}]})
    assert no_values.data[0].values == []
    assert no_values.data[0].label is None


@pytest.mark.asyncio
async def test_rest_client_encodes_the_slash_in_the_output_id() -> None:
    """The '/' inside an output id is data, not a path separator."""
    patcher, session = stub_session(_RESULTS_JSON)
    with patcher:
        await BiosimServiceRest().get_output_results(_RUN_ID, _OUTPUT_ID)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/results/{_RUN_ID}/simulation.sedml%2Fplot_1",
        params={"includeData": "true"},
    )


@pytest.mark.asyncio
async def test_rest_client_forwards_include_data() -> None:
    patcher, session = stub_session(_RESULTS_JSON)
    with patcher:
        results = await BiosimServiceRest().get_output_results(_RUN_ID, "plot_1")
    _, kwargs = session.get.call_args
    assert kwargs["params"] == {"includeData": "true"}
    assert results.output_id == _OUTPUT_ID


@pytest.mark.asyncio
async def test_rest_client_quotes_a_hostile_run_id() -> None:
    patcher, session = stub_session(_RESULTS_JSON)
    with patcher:
        await BiosimServiceRest().get_output_results("../secret?x=1", "out")
    args, _ = session.get.call_args
    assert args[0] == "https://api.biosimulations.org/results/..%2Fsecret%3Fx%3D1/out"


def test_results_route_accepts_a_slash_containing_output_id() -> None:
    """The route is declared with `:path`, so the composite id arrives intact."""
    biosim = AsyncMock()
    biosim.get_output_results.return_value = OutputResults.model_validate(_RESULTS_JSON)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(
            f"/results/{_RUN_ID}/simulation.sedml/plot_1?includeData=true"
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outputId"] == _OUTPUT_ID
    assert body["data"][0]["values"] == [0.0, 0.5, 1.0]
    biosim.get_output_results.assert_awaited_once_with(_RUN_ID, "simulation.sedml/plot_1")


def test_results_route_accepts_a_percent_encoded_output_id() -> None:
    biosim = AsyncMock()
    biosim.get_output_results.return_value = OutputResults.model_validate(_RESULTS_JSON)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/results/{_RUN_ID}/simulation.sedml%2Fplot_1")
    assert response.status_code == 200
    biosim.get_output_results.assert_awaited_once_with(_RUN_ID, "simulation.sedml/plot_1")


def test_results_route_maps_upstream_404_while_unavailable() -> None:
    """Results do not exist until the run finishes; that is a 404, not a 502."""
    biosim = AsyncMock()
    biosim.get_output_results.side_effect = upstream_error(404)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/results/{_RUN_ID}/plot_1")
    assert response.status_code == 404
    assert _RUN_ID in response.json()["detail"]


def test_results_route_maps_upstream_5xx_to_502() -> None:
    biosim = AsyncMock()
    biosim.get_output_results.side_effect = upstream_error(500)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/results/{_RUN_ID}/plot_1")
    assert response.status_code == 502


def test_results_route_without_biosim_service_is_503() -> None:
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/results/{_RUN_ID}/plot_1")
    assert response.status_code == 503
