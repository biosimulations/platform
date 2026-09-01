"""Mirror of the biosimulations.org ``GET /results/{run_id}/{outputId}`` contract.

Fetched **lazily, one output at a time**, and deliberately never embedded in a
project or run response: a SED-ML document can hold many plots, so aggregating
these would fan one page load out into N upstream calls carrying the run's
entire numeric output.
"""

from typing import Any

from biosim_server.common.biosim_api.common import UpstreamModel
from pydantic import Field


class ResultDatum(UpstreamModel):
    """One data generator's values within an output."""

    # The *data generator* id, joined by consumers as
    # "{sedDocLocation}/{outputId}/{id}". Not a dataSet id.
    id: str | None = None
    label: str | None = None
    # Deliberately list[Any], not list[float]: a repeated task nests its results,
    # so this array is ragged and arbitrarily deep (the frontend flattens it
    # before plotting). list[float] would reject real payloads.
    values: list[Any] = Field(default_factory=list)


class OutputResults(UpstreamModel):
    """``GET /results/{run_id}/{outputId}?includeData=true``."""

    # Composite: "{sedDocLocation}/{outputId}" -- it contains a '/'.
    output_id: str | None = Field(default=None, alias="outputId")
    data: list[ResultDatum] = Field(default_factory=list)
