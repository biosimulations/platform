from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from biosim_server.biosim_runs import HDF5File


class SimulatorSelection(BaseModel):
    id: str        # e.g., "copasi"
    version: str   # e.g., "4.34.251"


class RunSimulationRequest(BaseModel):
    omex_id: str
    name: str
    simulators: list[SimulatorSelection]
    is_commercial: bool = False
    email_address: str
    newsletter_consent: bool = False
    cache_buster: str | None = Field(
        default=None,
        description=(
            "Optional salt for the (omex, simulator, cache_buster) result cache. "
            "Omit (or null) to reuse cached results for identical submissions; pass "
            "a unique value to force fresh biosimulations.org runs."
        ),
    )


class SimulationJobStatus(BaseModel):
    job_id: str                                  # Our generated UUID
    simulator_id: str                            # e.g., "copasi"
    version: str                                 # e.g., "4.34.251"
    status: str                                  # "processing" | "success" | "failure"
    error: str | None = None
    biosimulations_run_id: str | None = None     # External reference


class ConglomerateStatus(BaseModel):
    processing_id: str                           # Temporal parent workflow ID
    jobs: list[SimulationJobStatus]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Frontend-facing run status. Internal SimulationJobStatus.status
# ("processing" | "success" | "failure" | "cancelled") maps onto these.
RunDisplayStatus = Literal["CREATED", "SUCCEEDED", "FAILED", "CANCELLED"]

_JOB_TO_DISPLAY_STATUS: dict[str, RunDisplayStatus] = {
    "processing": "CREATED",
    "success": "SUCCEEDED",
    "failure": "FAILED",
    "cancelled": "CANCELLED",
}


def job_status_to_display(job_status: str) -> RunDisplayStatus:
    """Map an internal SimulationJobStatus.status onto the listing status."""
    return _JOB_TO_DISPLAY_STATUS.get(job_status, "CREATED")


class SimulationRunRecord(BaseModel):
    """Persisted, queryable record of a single (submission x simulator) run.

    One ``POST /simulations/run`` submission produces one record per selected
    simulator. ``run_id`` is the per-simulator job UUID; ``processing_id`` is the
    parent Temporal workflow id (used by ``GET /simulations/{processing_id}``).

    Fields that the platform does not yet capture (``cpus``, ``memory``,
    ``max_time``, ``env_vars``, ``purpose``, ``runtime``, ``project_size``,
    ``results_size``) default to empty/zero until the submit path plumbs them.
    """

    run_id: str
    processing_id: str
    name: str
    simulator: str
    simulator_version: str
    simulator_digest: str = ""
    cache_buster: str = "0"  # salt used for this run's result-cache key
    cpus: int = 0
    memory: int = 0
    max_time: int = 0
    env_vars: list[str] = Field(default_factory=list)
    purpose: str = ""
    email: str
    status: RunDisplayStatus = "CREATED"
    runtime: int = 0
    project_size: int = 0
    results_size: int = 0
    submitted: datetime = Field(default_factory=_utcnow)
    updated: datetime = Field(default_factory=_utcnow)
    biosimulations_run_id: str | None = None
    database_id: str | None = None


class SimulationRun(BaseModel):
    """API representation of a run, matching the frontend ``SimulationRun``
    interface (``frontend/app/models/simulators.ts``). Serialized with camelCase
    aliases; FastAPI emits aliases by default (``response_model_by_alias=True``)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    # The biosimulations.org run id (24-char Mongo ObjectId). `id` above is our
    # internal per-simulator run_id (a 32-char uuid4 hex); detail pages that fetch
    # from the biosimulations API must key off this, not `id`. None until the run
    # has been submitted to biosimulations and assigned an id.
    biosimulations_run_id: str | None = Field(default=None, serialization_alias="biosimulationsRunId")
    name: str
    simulator: str
    simulator_version: str = Field(serialization_alias="simulatorVersion")
    simulator_digest: str = Field(serialization_alias="simulatorDigest")
    cpus: int
    memory: int
    max_time: int = Field(serialization_alias="maxTime")
    env_vars: list[str] = Field(serialization_alias="envVars")
    purpose: str
    email: str
    status: str
    runtime: int
    project_size: int = Field(serialization_alias="projectSize")
    results_size: int = Field(serialization_alias="resultsSize")
    submitted: str
    updated: str

    @classmethod
    def from_record(cls, record: SimulationRunRecord) -> "SimulationRun":
        return cls(
            id=record.run_id,
            biosimulations_run_id=record.biosimulations_run_id,
            name=record.name,
            simulator=record.simulator,
            simulator_version=record.simulator_version,
            simulator_digest=record.simulator_digest,
            cpus=record.cpus,
            memory=record.memory,
            max_time=record.max_time,
            env_vars=record.env_vars,
            purpose=record.purpose,
            email=record.email,
            status=record.status,
            runtime=record.runtime,
            project_size=record.project_size,
            results_size=record.results_size,
            submitted=_iso(record.submitted),
            updated=_iso(record.updated),
        )


def _iso(value: datetime) -> str:
    """ISO-8601 with a trailing ``Z`` for UTC, matching the frontend's format."""
    text = value.isoformat()
    return text.replace("+00:00", "Z")


class TableSort(BaseModel):
    id: str | None = None
    direction: Literal["asc", "desc"] | None = None


class TableFilter(BaseModel):
    id: str | None = None
    operator: str | None = None
    value: Any = None


class TablePagination(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int = 1
    per_page: int = Field(default=20, alias="perPage")
    total: int | None = Field(default=None, alias="_total")


class ListSimulationRunsRequest(BaseModel):
    """Body of ``POST /simulations/runs`` — table query for the runs listing."""

    type: Literal["all", "user"] = "all"
    user: str | None = None
    sort: TableSort | None = None
    filters: list[TableFilter] = Field(default_factory=list)
    pagination: TablePagination = Field(default_factory=TablePagination)


class ListSimulationRunsResponse(BaseModel):
    runs: list[SimulationRun]
    pagination: TablePagination


class JobResult(BaseModel):
    """One job's result-dataset catalog, part of GET /simulations/{id}/results.

    `hdf5_file` is None when the job has no `biosimulations_run_id` yet (still
    running) or its metadata couldn't be fetched."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(serialization_alias="jobId")
    simulator_id: str = Field(serialization_alias="simulatorId")
    hdf5_file: HDF5File | None = Field(default=None, serialization_alias="hdf5File")


class SimulationRunResults(BaseModel):
    processing_id: str = Field(serialization_alias="processingId")
    jobs: list[JobResult]

    model_config = ConfigDict(populate_by_name=True)


class JobLogs(BaseModel):
    """One job's logs, part of GET /simulations/{id}/logs.

    `logs` is the passthrough biosimulations.org /logs/{id} payload (untyped --
    no confirmed schema to model against); None when unavailable."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(serialization_alias="jobId")
    simulator_id: str = Field(serialization_alias="simulatorId")
    logs: dict[str, Any] | None = None


class SimulationRunLogs(BaseModel):
    processing_id: str = Field(serialization_alias="processingId")
    jobs: list[JobLogs]

    model_config = ConfigDict(populate_by_name=True)
