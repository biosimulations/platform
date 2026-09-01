"""Mirror of the biosimulations.org ``GET /files/{id}`` contract.

Note the id is a **simulation run** id, not a file id: the endpoint lists every
file in that run's COMBINE/OMEX archive. The response body is a bare JSON array.
"""

from biosim_server.common.biosim_api.common import UpstreamModel
from pydantic import Field


class ProjectFile(UpstreamModel):
    """One file inside a run's archive."""

    id: str | None = None
    name: str | None = None
    # A media-type URI, e.g. "http://purl.org/NET/mediatypes/application/vnd.vega.v5+json".
    # Deliberately a str: the vocabulary is open, and consumers substring-match it.
    format: str | None = None
    # Archive-relative path; may lead with "./". Split on "/" to build the file tree.
    location: str | None = None
    size: int | None = None  # bytes, for this file alone -- not the run's projectSize
    url: str | None = None   # absolute download URL
    master: bool | None = None
    simulation_run: str | None = Field(default=None, alias="simulationRun")
    created: str | None = None
    updated: str | None = None
