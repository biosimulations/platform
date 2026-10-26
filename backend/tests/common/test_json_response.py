"""Non-finite floats render as JSON null instead of failing the response.

Regression for compose-api-prod-comparison-212 (Oct 2026): COPASI left one report
variable NaN, so its self-comparison score was NaN, and every GET /verify for the
completed run raised ``ValueError: Out of range float values are not JSON
compliant`` -- an unhandled 500 that the browser reported as a CORS error.
"""

import json
import math
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.biosim_runs import Hdf5DataValues
from biosim_server.biosim_verify.models import ComparisonStatistics, GenerateStatisticsActivityOutput, RunData
from biosim_server.common.json_response import NonFiniteSafeJSONResponse
from tests.api.test_main import _make_verify_output, _temporal_with_describe

DATASET = "BIOMD0000000155_url.sedml/autogen_report_for_task1"


def test_finite_content_renders_exactly_like_starlette() -> None:
    content = {"score": [0.0, 1.5e-3], "nested": {"ok": True, "items": [1, "a", None]}}

    assert NonFiniteSafeJSONResponse(content).body == b'{"score":[0.0,0.0015],"nested":{"ok":true,"items":[1,"a",null]}}'


def test_non_finite_floats_render_as_null_at_any_depth() -> None:
    content = {"a": math.nan, "b": [1.0, math.inf, (-math.inf, 2.0)], "c": {"d": [{"e": math.nan}]}}

    body = json.loads(bytes(NonFiniteSafeJSONResponse(content).body))

    assert body == {"a": None, "b": [1.0, None, [None, 2.0]], "c": {"d": [{"e": None}]}}


def test_other_render_errors_still_raise() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        NonFiniteSafeJSONResponse({"a": object()})


def test_circular_content_raises_value_error_not_recursion_error() -> None:
    content: dict[str, Any] = {}
    content["self"] = content

    with pytest.raises(ValueError, match="Circular reference detected"):
        NonFiniteSafeJSONResponse(content)


def test_cycle_through_a_list_raises_value_error() -> None:
    inner: list[Any] = []
    inner.append(inner)

    with pytest.raises(ValueError, match="Circular reference detected"):
        NonFiniteSafeJSONResponse({"x": inner})


def test_cycle_with_non_finite_values_still_raises_cleanly() -> None:
    node: dict[str, Any] = {"score": [math.nan]}
    node["next"] = node

    with pytest.raises(ValueError, match="Circular reference detected"):
        NonFiniteSafeJSONResponse(node)


def test_shared_references_are_not_mistaken_for_cycles() -> None:
    shared = [1.0, math.nan]
    content = {"a": shared, "b": shared}

    body = json.loads(bytes(NonFiniteSafeJSONResponse(content).body))

    assert body == {"a": [1.0, None], "b": [1.0, None]}


@pytest.mark.asyncio
async def test_get_verify_with_nan_in_report_is_200() -> None:
    output = _make_verify_output(owner_sub=None)
    output.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[],
        comparison_statistics={DATASET: [[ComparisonStatistics(
            dataset_name=DATASET, simulator_version_i="copasi:4.45.296", simulator_version_j="copasi:4.45.296",
            var_names=["A", "B", "C"], score=[0.0, math.nan, math.inf], is_close=[True, False, False],
        )]]},
        sim_run_data=[RunData(run_id="r1", dataset_name=DATASET, var_names=["A", "B", "C"],
                              data=Hdf5DataValues(shape=[3, 2], values=[0.0, 1.0, math.nan, math.nan, 2.0, 3.0]))],
    )
    temporal = _temporal_with_describe(query_result=output)

    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/verify/wf", headers={"origin": "http://localhost:3000"})

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    results = response.json()["workflow_results"]
    assert results["comparison_statistics"][DATASET][0][0]["score"] == [0.0, None, None]
    assert results["sim_run_data"][0]["data"]["values"] == [0.0, 1.0, None, None, 2.0, 3.0]
