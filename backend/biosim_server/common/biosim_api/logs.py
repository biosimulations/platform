"""Mirror of the biosimulations.org ``GET /logs/{run_id}`` contract.

The payload repeats one structure at four nesting levels — run, SED document,
task, output — each carrying the same ``status`` / ``algorithm`` / ``output`` /
``skipReason`` / ``exception`` quintuple. ``LogEntry`` holds those once; the
concrete types add only what distinguishes them.

Statuses here describe *execution* of one element and are deliberately NOT the
run-lifecycle ``BiosimSimulationRunStatus``: a run can be SUCCEEDED while an
individual task is SKIPPED. They stay plain strings for the same reason the
SED-ML style vocabularies do — an unrecognised value must not 500 a proxy.
"""

from typing import Any

from biosim_server.common.biosim_api.common import LogMessage, UpstreamModel
from pydantic import Field


class LogEntry(UpstreamModel):
    """Fields every level of the execution log carries."""

    status: str | None = None
    # A KISAO id string (e.g. "KISAO_0000019"), not an object -- this is the only
    # bridge from a log to GET /ontologies/KISAO/{id}.
    algorithm: str | None = None
    # Raw captured stdout, ANSI escapes included. Can be very large.
    output: str | None = None
    # None means the key was absent, i.e. "not skipped" / "did not raise".
    skip_reason: LogMessage | None = Field(default=None, alias="skipReason")
    exception: LogMessage | None = None


class SedTaskLog(LogEntry):
    """``sedDocuments[].tasks[]``. ``id`` joins to the specification's task ids."""

    id: str | None = None


class SedOutputLog(LogEntry):
    """``sedDocuments[].outputs[]``. ``id`` joins to the specification's output ids."""

    id: str | None = None
    # Left untyped: the required surface names this field but not its shape, and
    # the only consumer behavior observed is a *presence* check separating report
    # logs from plot logs. Guessing a schema would risk rejecting the real one.
    data_sets: Any = Field(default=None, alias="dataSets")


class SedDocumentLog(LogEntry):
    """``sedDocuments[]``. ``location`` joins to SedDocumentSpec.id."""

    location: str | None = None
    tasks: list[SedTaskLog] = Field(default_factory=list)
    outputs: list[SedOutputLog] = Field(default_factory=list)


class RunLog(LogEntry):
    """``GET /logs/{run_id}`` -- the whole execution log for one run."""

    sed_documents: list[SedDocumentLog] = Field(default_factory=list, alias="sedDocuments")
