from datetime import datetime
from enum import StrEnum
from typing import Final, Optional

from pydantic import BaseModel, Field

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


class VerifyWorkflowOutput(BaseModel):
    workflow_id: str
    compare_settings: CompareSettings
    workflow_status: VerifyWorkflowStatus
    timestamp: str
    workflow_run_id: Optional[str] = None
    workflow_error: Optional[str] = None
    workflow_results: Optional[GenerateStatisticsActivityOutput] = None
    # Auth0 `sub` of the caller who started the workflow. Optional so in-flight
    # Temporal histories that predate this field still deserialize.
    owner_sub: Optional[str] = None


# ---------------------------------------------------------------------------
# Verification ledger (BiosimCompare collection)
# ---------------------------------------------------------------------------

class VerificationType(StrEnum):
    OMEX = "omex"
    RUNS = "runs"


class VerificationRun(BaseModel):
    id: str
    created: datetime
    status: str


class VerificationRecord(BaseModel):
    omex_hash: str
    run_ids: list[VerificationRun]


class VerificationLedgerRecord(BaseModel):
    workflow_id: str
    verify_type: VerificationType
    # Caller's verified sub, or None for an anonymous (legacy API) submission,
    # which makes the verification publicly readable.
    owner_sub: Optional[str] = None
    created: datetime
    omex_hash: Optional[str] = None
    status: Optional[str] = "PENDING"


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
        default_factory=list,
        description=(
            "One page of workflow IDs that can be passed to GET /verify/{workflow_id}, "
            "newest first (created descending, then workflow_id ascending). Anonymous callers "
            "see ownerless verifications; authenticated callers see ownerless plus their own. "
            "Follow `next_cursor` with the same identity for older visible IDs."
        ),
    )
    records: list[VerificationRecord] = Field(
        default_factory=list,
        description="Verification records grouped by OMEX archive hash with run metadata.",
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Opaque continuation token: pass it as `cursor` to get the next (older) page. "
            "null when this is the last page."
        ),
    )
