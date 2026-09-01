from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, field_validator

from biosim_server.biosim_omex import OmexFile

ATTRIBUTE_VALUE_TYPE = int | float | str | bool | list[str] | list[int] | list[float] | list[bool]


class HDF5Attribute(BaseModel):
    key: str
    value: ATTRIBUTE_VALUE_TYPE


class HDF5Dataset(BaseModel):
    name: str
    shape: list[int]
    attributes: list[HDF5Attribute]

    @property
    def sedml_labels(self) -> list[str]:
        for attr in self.attributes:
            if attr.key == "sedmlDataSetLabels":
                if isinstance(attr.value, list) and all(isinstance(v, str) for v in attr.value):
                    return attr.value  # type: ignore
        return []


class HDF5Group(BaseModel):
    name: str
    attributes: list[HDF5Attribute]
    datasets: list[HDF5Dataset]


class HDF5File(BaseModel):
    filename: str
    id: str
    uri: str
    groups: list[HDF5Group]

    @property
    def datasets(self) -> dict[str, HDF5Dataset]:
        dataset_dict: dict[str, HDF5Dataset] = {}
        for group in self.groups:
            for dataset in group.datasets:
                dataset_dict[dataset.name] = dataset
        return dataset_dict


class Hdf5DataValues(BaseModel):
    # simulation_run_id: str
    # dataset_name: str
    shape: list[int]
    values: list[float]



class BiosimulatorVersion(BaseModel):
    id: str
    name: str
    version: str
    image_url: str
    image_digest: str
    created: str
    updated: str

class BiosimSimulationRunStatus(StrEnum):
    CREATED = 'CREATED'
    QUEUED = 'QUEUED',
    RUNNING = 'RUNNING',
    SKIPPED = 'SKIPPED',
    PROCESSING = 'PROCESSING',
    SUCCEEDED = 'SUCCEEDED',
    FAILED = 'FAILED',
    RUN_ID_NOT_FOUND = 'RUN_ID_NOT_FOUND',
    UNKNOWN = 'UNKNOWN'


class BiosimSimulationRun(BaseModel):
    """ data returned by api.biosimulations.org/runs/{run_id} """
    id: str
    name: str
    simulator_version: BiosimulatorVersion
    status: BiosimSimulationRunStatus
    error_message: str | None = None
    # Run metadata returned by biosimulations.org. Optional because older cached
    # documents predate these fields and some values are only populated once a run
    # completes. Field names are snake_case here; the API uses camelCase (parsed in
    # BiosimServiceRest). See tests/fixtures/local_data/biosim_run_response.json.
    cpus: Optional[int] = None             # number of CPUs
    memory: Optional[int] = None           # (in GB)
    max_time: Optional[int] = None         # (in minutes)
    env_vars: Optional[list[str]] = None   # environment variables (e.g., ["OMP_NUM_THREADS=4"])
    purpose: Optional[str] = None          # e.g. "academic", "other"
    submitted: Optional[str] = None        # datetime string (e.g. 2025-01-10T19:51:11.934Z)
    updated: Optional[str] = None          # datetime string (e.g. 2025-01-10T19:51:41.807Z)
    project_size: Optional[int] = None     # (in bytes)
    results_size: Optional[int] = None     # (in bytes)
    runtime: Optional[int] = None          # (in milliseconds)
    email: Optional[str] = None
    # Upstream /runs/{id} reports the simulator as a slug plus a bare version
    # string. `simulator_version` above is already taken by the *resolved*
    # BiosimulatorVersion object (name + image url + digest, looked up against
    # api.biosimulators.org), so the raw string cannot reuse that name without
    # making the model actively misleading.
    simulator_id: Optional[str] = None              # e.g. "copasi" (an id, not a display name)
    simulator_version_string: Optional[str] = None  # e.g. "4.34.251"

    @field_validator('id')
    def validate_id(cls, v: str) -> str:
        if v.find("-") >= 0:
            raise ValueError("id must not contain any dashes, looks like a uuid")
        return v


class BiosimulatorWorkflowRun(BaseModel):
    workflow_id: str
    file_hash_md5: str
    image_digest: str
    cache_buster: str
    omex_file: OmexFile
    simulator_version: BiosimulatorVersion
    biosim_run: BiosimSimulationRun | None = None
    hdf5_file: HDF5File | None = None
    database_id: Optional[str] = None


class BiosimSimulationRunApiRequest(BaseModel):
    name: str  # what does this correspond to?
    simulator: str
    simulatorVersion: str
    maxTime: int  # in minutes
    # email: Optional[str] = None
    # cpus: Optional[int] = None
    # memory: Optional[int] = None (in GB)
