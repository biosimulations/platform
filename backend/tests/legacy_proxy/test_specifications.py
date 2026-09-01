"""GET /specifications/{run_id} — SED-ML document parsing.

The riskiest model in the passthrough set: polymorphic outputs, a recursive
style, and fields that arrive either as an id string or as an expanded object.
These tests pin the permissive behavior, because over-constraining any of it
turns an upstream addition into a proxy 500.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.common.biosim_api import (
    SedDocumentSpec,
    SedModelLanguage,
    SedModelRef,
    SedPlot2D,
    SedReport,
    SedStyle,
    SedUnknownOutput,
)
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

_RUN_ID = "61fea483f499ccf25faafc4d"

_SPEC_JSON: dict[str, Any] = {
    "id": "./simulation.sedml",
    "simulationRun": _RUN_ID,
    "level": 1,
    "version": 3,
    # Live payloads serialize a task's model as a bare id into `models` below;
    # the expanded form is exercised separately.
    "models": [
        {"id": "model_wt", "source": "model.xml", "language": "urn:sedml:language:sbml"}
    ],
    "tasks": [{"id": "task_1", "model": "model_wt", "simulation": "simulation"}],
    "outputs": [
        {
            "_type": "SedReport",
            "id": "report_1",
            "name": "Report 1",
            "dataSets": [
                {"id": "ds_time", "label": "time", "name": "Time"},
                {"id": "ds_x", "label": "X", "name": None},
            ],
        },
        {
            "_type": "SedPlot2D",
            "id": "plot_1",
            "name": "Plot 1",
            "xScale": "linear",
            "yScale": "log",
            "curves": [
                {
                    "id": "curve_1",
                    "name": "X vs time",
                    "xDataGenerator": "gen_time",
                    "yDataGenerator": "gen_x",
                    "style": {
                        "base": "base_style",
                        "line": {"color": "#ff0000", "thickness": 2.0, "type": "solid"},
                        "marker": {
                            "type": "circle",
                            "size": 4.0,
                            "fillColor": "#00ff00",
                            "lineColor": "#0000ff",
                            "lineThickness": 1.5,
                        },
                    },
                }
            ],
        },
    ],
}


# Upstream returns an array of documents.
_SPEC_BODY: list[dict[str, Any]] = [_SPEC_JSON]


def _spec(**overrides: Any) -> SedDocumentSpec:
    return SedDocumentSpec.model_validate({**_SPEC_JSON, **overrides})


# --------------------------------------------------------------------------
# outputs: polymorphic dispatch
# --------------------------------------------------------------------------


def test_outputs_dispatch_to_report_and_plot() -> None:
    spec = _spec()
    report, plot = spec.outputs
    assert isinstance(report, SedReport)
    assert [d.label for d in report.data_sets] == ["time", "X"]
    assert report.data_sets[0].name == "Time"
    assert report.data_sets[1].name is None
    assert isinstance(plot, SedPlot2D)
    assert plot.x_scale == "linear"
    assert plot.y_scale == "log"
    assert len(plot.curves) == 1


def test_unknown_output_type_falls_back_instead_of_raising() -> None:
    """An output type biosimulations.org adds later must pass through, not 500."""
    spec = _spec(outputs=[{"_type": "SedPlot4D", "id": "p4", "hyperCubes": [1, 2]}])
    output = spec.outputs[0]
    assert isinstance(output, SedUnknownOutput)
    assert output.type_ == "SedPlot4D"
    assert output.id == "p4"
    # extra="allow" retains the unmodeled payload.
    assert output.model_dump(by_alias=True)["hyperCubes"] == [1, 2]


def test_output_without_a_type_tag_falls_back() -> None:
    spec = _spec(outputs=[{"id": "mystery"}])
    assert isinstance(spec.outputs[0], SedUnknownOutput)
    assert spec.outputs[0].id == "mystery"


def test_plot3d_is_recognised_with_untyped_surfaces() -> None:
    spec = _spec(
        outputs=[{"_type": "SedPlot3D", "id": "p3", "xScale": "linear", "surfaces": [{"id": "s"}]}]
    )
    plot3d = spec.outputs[0]
    assert type(plot3d).__name__ == "SedPlot3D"
    assert plot3d.model_dump(by_alias=True)["surfaces"] == [{"id": "s"}]


def test_empty_outputs_and_datasets_and_curves() -> None:
    assert _spec(outputs=[], tasks=[]).outputs == []
    empty_report = _spec(outputs=[{"_type": "SedReport", "id": "r"}]).outputs[0]
    assert isinstance(empty_report, SedReport)
    assert empty_report.data_sets == []
    empty_plot = _spec(outputs=[{"_type": "SedPlot2D", "id": "p"}]).outputs[0]
    assert isinstance(empty_plot, SedPlot2D)
    assert empty_plot.curves == []
    assert empty_plot.x_scale is None


# --------------------------------------------------------------------------
# curves, styles, data generators
# --------------------------------------------------------------------------


def _curve(curve: dict[str, Any]) -> Any:
    plot = _spec(outputs=[{"_type": "SedPlot2D", "id": "p", "curves": [curve]}]).outputs[0]
    assert isinstance(plot, SedPlot2D)
    return plot.curves[0]


def test_curve_style_and_marker_fields() -> None:
    curve = _curve(_SPEC_JSON["outputs"][1]["curves"][0])
    assert isinstance(curve.style, SedStyle)
    assert curve.style.base == "base_style"
    assert curve.style.line is not None
    assert (curve.style.line.color, curve.style.line.thickness) == ("#ff0000", 2.0)
    assert curve.style.line.type_ == "solid"
    assert curve.style.marker is not None
    assert curve.style.marker.type_ == "circle"
    assert curve.style.marker.size == 4.0
    assert curve.style.marker.fill_color == "#00ff00"
    # The marker's outline is a distinct field from the line's own color/thickness.
    assert curve.style.marker.line_color == "#0000ff"
    assert curve.style.marker.line_thickness == 1.5
    assert curve.style.marker.line_color != curve.style.line.color


def test_data_generators_accept_id_string_or_expanded_object() -> None:
    as_string = _curve({"id": "c", "xDataGenerator": "gen_x", "yDataGenerator": "gen_y"})
    assert as_string.x_data_generator == "gen_x"
    expanded = _curve(
        {"id": "c", "xDataGenerator": {"id": "gen_x", "name": "X"}, "yDataGenerator": {"id": "gen_y"}}
    )
    assert expanded.x_data_generator is not None
    assert not isinstance(expanded.x_data_generator, str)
    assert expanded.x_data_generator.id == "gen_x"
    assert expanded.x_data_generator.name == "X"


def test_curve_style_absent_stays_none() -> None:
    """No style object means renderer defaults; a synthesized empty style would
    change how the curve draws."""
    curve = _curve({"id": "c", "name": "no style"})
    assert curve.style is None


def test_curve_style_may_be_an_id_string() -> None:
    curve = _curve({"id": "c", "style": "style_1"})
    assert curve.style == "style_1"


def test_partial_style_leaves_the_missing_half_none() -> None:
    curve = _curve({"id": "c", "style": {"line": {"color": "#f00"}}})
    assert isinstance(curve.style, SedStyle)
    assert curve.style.line is not None
    assert curve.style.line.color == "#f00"
    assert curve.style.line.type_ is None
    # `marker is None` is load-bearing: the renderer decides marker-vs-no-marker
    # from absence.
    assert curve.style.marker is None


def test_style_base_is_recursive() -> None:
    curve = _curve(
        {"id": "c", "style": {"base": {"id": "parent", "line": {"color": "#abc"}}, "marker": {"size": 2}}}
    )
    assert isinstance(curve.style, SedStyle)
    base = curve.style.base
    assert isinstance(base, SedStyle)
    assert base.id == "parent"
    assert base.line is not None
    assert base.line.color == "#abc"


def test_unknown_style_type_vocabulary_is_accepted() -> None:
    """Line/marker `type` is an open vocabulary; a strict enum would 500 here."""
    curve = _curve({"id": "c", "style": {"line": {"type": "squiggle"}, "marker": {"type": "blob"}}})
    assert isinstance(curve.style, SedStyle)
    assert curve.style.line is not None and curve.style.line.type_ == "squiggle"
    assert curve.style.marker is not None and curve.style.marker.type_ == "blob"


# --------------------------------------------------------------------------
# tasks / model language
# --------------------------------------------------------------------------


def test_task_model_may_be_a_serialized_id() -> None:
    """Live payloads reference the model by id; the language lives in `models`."""
    spec = _spec()
    assert spec.tasks[0].model == "model_wt"
    assert spec.models[0].id == "model_wt"
    # A bare URN string, not an object -- both shapes are accepted.
    assert spec.models[0].language == "urn:sedml:language:sbml"


def test_task_model_may_be_expanded() -> None:
    spec = _spec(
        tasks=[
            {
                "id": "t",
                "model": {
                    "id": "model_1",
                    "language": {
                        "acronym": "SBML",
                        "name": "Systems Biology Markup Language",
                        "sedmlUrn": "urn:sedml:language:sbml.level-2.version-3",
                    },
                },
            }
        ]
    )
    model = spec.tasks[0].model
    assert isinstance(model, SedModelRef)
    assert isinstance(model.language, SedModelLanguage)
    assert model.language.acronym == "SBML"
    assert model.language.sedml_urn == "urn:sedml:language:sbml.level-2.version-3"


def test_model_language_fallback_chain() -> None:
    """Consumers read acronym -> name -> sedmlUrn; each is independently optional."""
    for present in ("acronym", "name", "sedmlUrn"):
        one = _spec(models=[{"id": "m", "language": {present: "value"}}])
        language = one.models[0].language
        assert isinstance(language, SedModelLanguage)
        assert language.model_dump(by_alias=True)[present] == "value"


def test_task_without_a_model_is_none() -> None:
    spec = _spec(tasks=[{"id": "t"}])
    assert spec.tasks[0].model is None


# --------------------------------------------------------------------------
# client + route
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_client_requests_the_specifications_url() -> None:
    """Upstream returns an array -- an archive may hold several SED documents."""
    patcher, session = stub_session(_SPEC_BODY)
    with patcher:
        specs = await BiosimServiceRest().get_run_specifications(_RUN_ID)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/specifications/{_RUN_ID}"
    )
    assert len(specs) == 1
    assert specs[0].id == "./simulation.sedml"
    assert len(specs[0].outputs) == 2


@pytest.mark.asyncio
async def test_rest_client_keeps_every_document_in_the_array() -> None:
    second = {**_SPEC_JSON, "id": "./second.sedml"}
    patcher, _ = stub_session([_SPEC_JSON, second])
    with patcher:
        specs = await BiosimServiceRest().get_run_specifications(_RUN_ID)
    assert [s.id for s in specs] == ["./simulation.sedml", "./second.sedml"]


@pytest.mark.asyncio
async def test_rest_client_wraps_a_lone_object_body() -> None:
    patcher, _ = stub_session(_SPEC_JSON)
    with patcher:
        specs = await BiosimServiceRest().get_run_specifications(_RUN_ID)
    assert len(specs) == 1 and specs[0].id == "./simulation.sedml"

    patcher, _ = stub_session([])
    with patcher:
        assert await BiosimServiceRest().get_run_specifications(_RUN_ID) == []


@pytest.mark.asyncio
async def test_rest_client_quotes_the_run_id() -> None:
    patcher, session = stub_session(_SPEC_BODY)
    with patcher:
        await BiosimServiceRest().get_run_specifications("../secret?x=1")
    session.get.assert_called_once_with(
        "https://api.biosimulations.org/specifications/..%2Fsecret%3Fx%3D1"
    )


def test_specifications_route_serializes_upstream_keys() -> None:
    biosim = AsyncMock()
    biosim.get_run_specifications.return_value = [_spec()]
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/specifications/{_RUN_ID}")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    body = response.json()[0]
    assert body["simulationRun"] == _RUN_ID
    assert body["tasks"][0]["model"] == "model_wt"
    assert body["models"][0]["language"] == "urn:sedml:language:sbml"
    assert body["outputs"][0]["_type"] == "SedReport"
    assert body["outputs"][0]["dataSets"][0]["label"] == "time"
    plot = body["outputs"][1]
    assert plot["_type"] == "SedPlot2D"
    assert (plot["xScale"], plot["yScale"]) == ("linear", "log")
    curve = plot["curves"][0]
    assert curve["xDataGenerator"] == "gen_time"
    assert curve["style"]["marker"]["fillColor"] == "#00ff00"
    assert curve["style"]["marker"]["lineThickness"] == 1.5
    assert curve["style"]["line"]["type"] == "solid"


def test_specifications_route_maps_upstream_404() -> None:
    biosim = AsyncMock()
    biosim.get_run_specifications.side_effect = upstream_error(404)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/specifications/{_RUN_ID}")
    assert response.status_code == 404
