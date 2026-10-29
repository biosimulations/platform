from datetime import datetime
from enum import StrEnum
from typing import Final, Optional

from pydantic import AwareDatetime, BaseModel, Field

from biosim_server.biosim_runs import BiosimSimulationRun, HDF5File, Hdf5DataValues

# Per-request selection bounds (PR #120, B2). One request is one workflow-start
# quota unit, so these bound the work a single unit can buy: N run ids -> N result
# downloads and an N x N comparison per dataset; N simulators -> N child
# workflows, each a biosimulations.org simulation job. Same for every caller.
# Module constants, not settings: they are published as OpenAPI `maxItems`.
MAX_VERIFY_RUN_IDS: Final = 10
MAX_VERIFY_SIMULATORS: Final = 10


class ComparisonStatistics(BaseModel):
    dataset_name: str
    simulator_version_i: str  # version of simulator used for run i <simulator_name>:<version>
    simulator_version_j: str  # version of simulator used for run j <simulator_name>:<version>
    var_names: list[str]
    score: Optional[list[float]] = None
    is_close: Optional[list[bool]] = None
    error_message: Optional[str] = None


class CompareSettings(BaseModel):
    user_description: str
    include_outputs: bool
    rel_tol: float
    abs_tol_min: float
    abs_tol_scale: float
    observables: Optional[list[str]] = None


# class CompareReport(BaseModel):
#     omex_file: OmexFile
#     simulator1: BiosimulatorVersion
#     simulator2: BiosimulatorVersion
#     stats: ComparisonStatistics
#
# class SimulatorComparison(BaseModel):
#     simRun1: BiosimSimulationRun
#     simRun2: BiosimSimulationRun
#     equivalent: bool

class SimulationRunInfo(BaseModel):
    biosim_sim_run: BiosimSimulationRun
    hdf5_file: HDF5File



class RunData(BaseModel):
    run_id: str
    dataset_name: str
    var_names: list[str]  # list of variables found this run for this dataset
    data: Hdf5DataValues  # n-dim array of data for this run, shape=(len(dataset_vars), len(times))



class GenerateStatisticsActivityOutput(BaseModel):
    sims_run_info: list[SimulationRunInfo]
    comparison_statistics: dict[str, list[list[ComparisonStatistics]]]  # matrix of comparison statistics per dataset
    sim_run_data: Optional[list[RunData]] = None


class GenerateStatisticsArtifactReference(BaseModel):
    reference_schema_version: int = 1
    artifact_reference: str
    content_sha256: str
    encoding: str
    uncompressed_bytes: int


class GenerateStatisticsActivityResult(BaseModel):
    output: Optional[GenerateStatisticsActivityOutput] = None
    reference: Optional[GenerateStatisticsArtifactReference] = None


class SimulatorFailure(BaseModel):
    """A requested simulator that produced no results: its run failed on biosimulations.org, or could not be
    submitted. Reported instead of being dropped silently, so a verification with no usable runs says why."""
    simulator_version: str  # <simulator_name>:<version>
    biosimulations_run_id: Optional[str] = None  # absent when the run was never created
    error_message: Optional[str] = None


class VerifyWorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RUN_ID_NOT_FOUND = "RUN_ID_NOT_FOUND"

    @property
    def is_done(self) -> bool:
        return self in [VerifyWorkflowStatus.COMPLETED, VerifyWorkflowStatus.FAILED,
                        VerifyWorkflowStatus.RUN_ID_NOT_FOUND]


class ReportPersistenceState(StrEnum):
    PENDING = "pending"
    STORED = "stored"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class VerifyWorkflowOutput(BaseModel):
    workflow_id: str
    compare_settings: CompareSettings
    workflow_status: VerifyWorkflowStatus
    timestamp: str
    workflow_run_id: Optional[str] = None
    workflow_error: Optional[str] = None
    # Requested simulators that produced no results (OmexVerifyWorkflow); empty when every one ran
    failed_simulators: list[SimulatorFailure] = []
    workflow_results: Optional[GenerateStatisticsActivityOutput] = None
    workflow_results_reference: Optional[GenerateStatisticsArtifactReference] = None
    # Auth0 `sub` of the caller who started the workflow. Optional so in-flight
    # Temporal histories that predate this field still deserialize.
    owner_sub: Optional[str] = None
    persistence_state: ReportPersistenceState = ReportPersistenceState.PENDING
    report_id: Optional[str] = None
    report_available: Optional[bool] = None


class VerificationProvenance(BaseModel):
    schema_version: int = 1
    verification_id: str
    workflow_run_id: Optional[str] = None
    owner_sub: Optional[str] = None
    created_at: Optional[datetime] = None
    computed_at: Optional[datetime] = None
    requested_settings: dict[str, object] = Field(default_factory=dict)
    workflow_input: dict[str, object] = Field(default_factory=dict)
    origin: str = "workflow"
    comparator_version: str | None = None
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    actual_runs: list[SimulationRunInfo] = Field(default_factory=list)


def sanitize_workflow_error(error: object) -> str:
    """Keep terminal errors useful without persisting payloads, paths, or URLs."""
    text = (str(error).splitlines() or ["verification failed"])[0]
    text = text.replace("\\", "/")
    text = __import__("re").sub(r"https?://\S+", "[redacted-url]", text)
    text = __import__("re").sub(r"(?<!\w)/(?:Users|home|tmp|var)/\S+", "[redacted-path]", text)
    return str(text[:1000])


class VerificationReportEnvelope(BaseModel):
    report_id: str
    workflow_id: str
    workflow_run_id: Optional[str] = None
    owner_sub: Optional[str] = None
    report_schema_version: int = 1
    provenance_schema_version: int = 1
    report_kind: str = "verification"
    workflow_status: VerifyWorkflowStatus
    computed_at: datetime
    saved_at: datetime
    content_sha256: str
    encoding: str
    uncompressed_bytes: int
    provenance: VerificationProvenance
    inline_payload: Optional[bytes] = None
    artifact_reference: Optional[str] = None


# ---------------------------------------------------------------------------
# Verification ledger (BiosimCompare collection)
# ---------------------------------------------------------------------------

class VerificationType(StrEnum):
    OMEX = "omex"
    RUNS = "runs"


# MD5 hexdigest of an OMEX archive, as `OmexFile.file_hash_md5` and
# `/compatibility/check`'s `omex_id` already spell it. Published as the OpenAPI
# `pattern` of every `omex_hash` query parameter, so `?omex_hash=` (empty),
# uppercase hex or an over-long value is a 422 before any database read.
OMEX_HASH_PATTERN: Final = r"^[0-9a-f]{32}$"


class VerificationRecord(BaseModel):
    """One ledger row (BiosimCompare collection)."""

    workflow_id: str
    verify_type: VerificationType
    # Caller's verified sub, or None for an anonymous (legacy API) submission,
    # which makes the verification publicly readable.
    owner_sub: Optional[str] = None
    created: datetime
    # MD5 of the archive the verification is about. Always set by POST
    # /verify/omex (computed server-side from the upload); for POST /verify/runs
    # it is derived from the platform's cached runs when possible, else taken
    # from an authenticated caller, else None.
    omex_hash: Optional[str] = None
    # Terminal workflow status, written once by GET /verify/{workflow_id} the
    # first time it observes the workflow finished. None until then -- also for
    # verifications nobody polled and for ones whose Temporal history expired.
    status: Optional[VerifyWorkflowStatus] = None
    request_schema_version: int = 1
    workflow_input: dict[str, object] | None = None
    request_hash: str | None = None
    report_id: str | None = None
    persistence_state: ReportPersistenceState = ReportPersistenceState.PENDING
    persistence_attempted_at: datetime | None = None
    persisted_at: datetime | None = None
    workflow_run_id: str | None = None
    persistence_error: str | None = None


class VerificationSummary(BaseModel):
    """Public listing item for GET /verification_ids: a ledger row without its owner."""

    workflow_id: str = Field(description="Pass to GET /verify/{workflow_id} for the report.")
    verify_type: VerificationType
    created: AwareDatetime = Field(description="When the verification was started (UTC).")
    omex_hash: Optional[str] = Field(
        default=None,
        description="MD5 hexdigest of the verified OMEX archive, when known. Group by it client-side.",
    )
    status: Optional[VerifyWorkflowStatus] = Field(
        default=None,
        description=(
            "Terminal status recorded the first time GET /verify/{workflow_id} saw the workflow "
            "finish. null means not yet observed as finished: still running, never polled, or "
            "polled only after its Temporal history expired."
        ),
    )


# GET /verification_ids page sizes (PR #120, B3). IDs are ~40-70 bytes, so a full
# page is ~70 KB and one bounded index range read, however long the ledger grows.
VERIFICATION_IDS_DEFAULT_PAGE_SIZE: Final = 100
VERIFICATION_IDS_MAX_PAGE_SIZE: Final = 1000

# Temporal's default `limit.maxIDLength`, in UTF-8 bytes. No longer workflow ID
# can be started, so this bounds every ID the ledger keeps -- including rows
# written before WORKFLOW_ID_PREFIX_MAX_LENGTH existed -- and therefore the
# GET /verification_ids cursor (database.VERIFICATION_CURSOR_MAX_LENGTH).
MAX_WORKFLOW_ID_BYTES: Final = 1000
# Caller-chosen `workflow_id_prefix`, in characters. At 4 UTF-8 bytes per
# character plus the 36-character uuid4 suffix, a new ID stays within
# MAX_WORKFLOW_ID_BYTES.
WORKFLOW_ID_PREFIX_MAX_LENGTH: Final = 200


class VerificationIdsResponse(BaseModel):
    verification_ids: list[str] = Field(
        description=(
            "One page of workflow IDs that can be passed to GET /verify/{workflow_id}, "
            "newest first (created descending, then workflow_id ascending). Anonymous callers "
            "see ownerless verifications; authenticated callers see ownerless plus their own. "
            "Follow `next_cursor` with the same identity for older visible IDs."
        )
    )
    verifications: list[VerificationSummary] = Field(
        default_factory=list,
        description=(
            "The same rows as `verification_ids`, in the same order, with their type, start time, "
            "OMEX hash and terminal status. A flat list: one archive's verifications can straddle "
            "pages, so group by `omex_hash` client-side."
        ),
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Opaque continuation token: pass it as `cursor` to get the next (older) page. "
            "null when this is the last page."
        ),
    )
