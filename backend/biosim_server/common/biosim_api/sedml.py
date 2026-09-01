"""Mirror of the biosimulations.org ``GET /specifications/{run_id}`` contract.

A SED-ML document: which models were simulated (and in what language) and what
outputs — reports and plots — the run produces.

Two recurring shapes drive the modeling here, both observed in the frontend's
own types and runtime handling (``frontend/app/models/sedml.ts``):

* **serialized vs expanded references.** Data generators and styles come back
  either as an id string or as the expanded object, so those fields are unions.
  The frontend branches on ``typeof`` at runtime for exactly this reason.
* **polymorphic outputs.** ``_type`` names the output class. Rather than a
  Pydantic discriminated union — which raises on a tag it has not been told
  about — the union is resolved left-to-right with a permissive member last, so
  an output type biosimulations.org adds later passes through instead of
  turning this proxy into a 500.
"""

from typing import Annotated, Any, Literal, Union

from biosim_server.common.biosim_api.common import UpstreamModel
from pydantic import Field


class SedModelLanguage(UpstreamModel):
    """``tasks[].model.language``. Consumers read acronym -> name -> sedmlUrn."""

    acronym: str | None = None          # e.g. "SBML"
    name: str | None = None             # e.g. "Systems Biology Markup Language"
    sedml_urn: str | None = Field(default=None, alias="sedmlUrn")  # urn:sedml:language:sbml...


class SedModelRef(UpstreamModel):
    """A task's model reference."""

    id: str | None = None
    name: str | None = None
    source: str | None = None
    # The specifications endpoint returns an object here, but the same field is a
    # bare URN string in the Specifications Mongo documents. Accept either rather
    # than 500 on the other shape.
    language: str | SedModelLanguage | None = None


class SedTaskSpec(UpstreamModel):
    """One entry of ``tasks``. ``id`` joins to the execution log's task ids."""

    id: str | None = None
    name: str | None = None
    # Serialized or expanded, like data generators and styles. Live payloads use
    # the serialized form -- a bare id referencing SedDocumentSpec.models[].id --
    # so the model language is reached via `models`, not through this field.
    model: str | SedModelRef | None = None
    simulation: str | None = None


class SedDataGeneratorRef(UpstreamModel):
    """Expanded form of a curve's x/y data generator."""

    id: str | None = None
    name: str | None = None


class SedLineStyle(UpstreamModel):
    """``style.line``. Absence is meaningful: no line object means "draw a line
    with renderer defaults", whereas ``type == "none"`` means "draw no line"."""

    color: str | None = None       # hex
    thickness: float | None = None
    # A known but open vocabulary (none/solid/dash/dot/dashDot/dashDotDot). Kept
    # as str: the renderer already maps unknown values to a default, so a strict
    # enum would turn an upstream addition into a proxy 500.
    type_: str | None = Field(default=None, alias="type")


class SedMarkerStyle(UpstreamModel):
    """``style.marker``. `line_color`/`line_thickness` are the *marker's* outline
    and are distinct from SedLineStyle.color/.thickness."""

    # Vocabulary: none/square/circle/diamond/xCross/plus/star/triangle*/hDash/vDash.
    # str for the same reason as SedLineStyle.type_.
    type_: str | None = Field(default=None, alias="type")
    size: float | None = None
    fill_color: str | None = Field(default=None, alias="fillColor")
    line_color: str | None = Field(default=None, alias="lineColor")
    line_thickness: float | None = Field(default=None, alias="lineThickness")


class SedStyle(UpstreamModel):
    """A curve's style. ``base`` is recursive and, like data generators, arrives
    either as a style id or as the expanded style object."""

    id: str | None = None
    name: str | None = None
    base: "str | SedStyle | None" = None
    line: SedLineStyle | None = None
    marker: SedMarkerStyle | None = None


SedStyle.model_rebuild()


class SedDataSet(UpstreamModel):
    """One entry of a report's ``dataSets``. Display falls back label -> name -> id."""

    id: str | None = None
    label: str | None = None
    name: str | None = None


class SedCurve(UpstreamModel):
    """One curve of a 2D plot."""

    id: str | None = None
    name: str | None = None
    x_data_generator: str | SedDataGeneratorRef | None = Field(
        default=None, alias="xDataGenerator"
    )
    y_data_generator: str | SedDataGeneratorRef | None = Field(
        default=None, alias="yDataGenerator"
    )
    style: str | SedStyle | None = None


class _SedOutputBase(UpstreamModel):
    id: str | None = None
    name: str | None = None


class SedReport(_SedOutputBase):
    type_: Literal["SedReport"] = Field(alias="_type")
    data_sets: list[SedDataSet] = Field(default_factory=list, alias="dataSets")


class SedPlot2D(_SedOutputBase):
    type_: Literal["SedPlot2D"] = Field(alias="_type")
    x_scale: str | None = Field(default=None, alias="xScale")  # "linear" | "log"
    y_scale: str | None = Field(default=None, alias="yScale")
    curves: list[SedCurve] = Field(default_factory=list)


class SedPlot3D(_SedOutputBase):
    type_: Literal["SedPlot3D"] = Field(alias="_type")
    x_scale: str | None = Field(default=None, alias="xScale")
    y_scale: str | None = Field(default=None, alias="yScale")
    # Left untyped: no 3D-plot sample has been observed, and guessing a schema
    # here would risk rejecting the real one. extra="allow" carries zScale.
    surfaces: list[Any] = Field(default_factory=list)


class SedUnknownOutput(_SedOutputBase):
    """Fallback for an output ``_type`` this package does not model yet.

    Must stay last in ``SedOutput``: it accepts any object, so a left-to-right
    union reaches it only after every typed member has rejected the payload.
    """

    type_: str | None = Field(default=None, alias="_type")


# left_to_right, not discriminator=: a tagged union raises `union_tag_invalid`
# on an unrecognised `_type`, which is precisely the upstream change a proxy
# must survive. Each typed member rejects fast on its Literal tag, so ordering
# is unambiguous.
SedOutput = Annotated[
    Union[SedReport, SedPlot2D, SedPlot3D, SedUnknownOutput],
    Field(union_mode="left_to_right"),
]


class SedDocumentSpec(UpstreamModel):
    """One SED-ML document. ``GET /specifications/{run_id}`` returns an array of these.

    ``id`` is the SED-ML document *location* inside the archive (it may lead with
    "./"), and is what joins to an execution log's ``sedDocuments[].location``.
    """

    id: str | None = None
    simulation_run: str | None = Field(default=None, alias="simulationRun")
    level: int | None = None
    version: int | None = None
    # Where the model language actually lives once tasks reference models by id.
    models: list[SedModelRef] = Field(default_factory=list)
    tasks: list[SedTaskSpec] = Field(default_factory=list)
    outputs: list[SedOutput] = Field(default_factory=list)
    created: str | None = None
    updated: str | None = None
