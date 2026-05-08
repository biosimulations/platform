from enum import StrEnum
from typing import Optional

from pydantic import BaseModel

from biosim_server.biosim_runs import BiosimSimulationRun, HDF5File, Hdf5DataValues


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
