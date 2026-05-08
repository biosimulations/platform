import os
import uuid
from pathlib import Path
from typing import Generator

import pytest
from testcontainers.mongodb import MongoDbContainer  # type: ignore

from biosim_server.biosim_omex import OmexFile
from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.models import VerifyWorkflowOutput
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput
from biosim_server.config import get_local_cache_dir

temp_data_dir = get_local_cache_dir() / "test_temp_dir"
temp_data_dir.mkdir(exist_ok=True)

@pytest.fixture(scope="session")
def fixture_data_dir() -> Path:
    return Path(os.path.dirname(__file__)) / "local_data"


@pytest.fixture(scope="function")
def omex_verify_workflow_id() -> str:
    return "omex-verification-" + str(uuid.uuid4())


@pytest.fixture(scope="function")
def runs_verify_workflow_id() -> str:
    return "runs-verification-" + str(uuid.uuid4())


@pytest.fixture(scope="function")
def simulator_version_copasi() -> BiosimulatorVersion:
    return BiosimulatorVersion.model_validate_json(
        '{"id":"copasi","name":"COPASI","version":"4.45.296","image_url":"ghcr.io/biosimulators/copasi:4.45.296",'
        '"image_digest":"sha256:7c9cd076eeec494a653353777e42561a2ec9be1bfcc647d0ea84d89fe18999df",'
        '"created":"2024-11-18T15:34:26.233Z","updated":"2024-11-18T15:34:26.233Z"}')


@pytest.fixture(scope="function")
def simulator_version_tellurium() -> BiosimulatorVersion:
    return BiosimulatorVersion.model_validate_json(
        '{"id":"tellurium","name":"tellurium","version":"2.2.10","image_url":"ghcr.io/biosimulators/tellurium:2.2.10",'
        '"image_digest":"sha256:0c22827b4682273810d48ea606ef50c7163e5f5289740951c00c64c669409eae",'
        '"created":"2024-10-10T22:00:50.110Z","updated":"2024-10-10T22:00:50.110Z"}')


@pytest.fixture(scope="session")
def compare_settings() -> CompareSettings:
    return CompareSettings(rel_tol=1e-4, abs_tol_min=1e-3, abs_tol_scale=1e-5, include_outputs=False,
                           observables=["time", "concentration"], user_description="description")


@pytest.fixture(scope="function")
def omex_verify_workflow_input(simulator_version_copasi: BiosimulatorVersion,
                               simulator_version_tellurium: BiosimulatorVersion,
                               compare_settings: CompareSettings) -> OmexVerifyWorkflowInput:
    path = "path/to/omex"
    omex_file = OmexFile(file_hash_md5="hash",
                         uploaded_filename="BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex",
                         file_size=100, omex_gcs_path=path, bucket_name="bucket")
    simulators = [simulator_version_copasi, simulator_version_tellurium]
    omex_input = OmexVerifyWorkflowInput(omex_file=omex_file, requested_simulators=simulators,
                                         compare_settings=compare_settings, cache_buster="0")
    return omex_input


@pytest.fixture(scope="function")
def omex_verify_workflow_output_file(fixture_data_dir: Path) -> Path:
    return fixture_data_dir / "OmexVerifyWorkflowOutput_expected.json"


@pytest.fixture(scope="function")
def omex_verify_workflow_output(omex_verify_workflow_input: OmexVerifyWorkflowInput,
                                omex_verify_workflow_output_file: Path,
                                omex_verify_workflow_id: str) -> VerifyWorkflowOutput:
    with open(omex_verify_workflow_output_file) as f:
        workflow_output = VerifyWorkflowOutput.model_validate_json(f.read())
        workflow_output.workflow_id = omex_verify_workflow_id
        return workflow_output


@pytest.fixture(scope="function")
def runs_verify_workflow_input(compare_settings: CompareSettings) -> RunsVerifyWorkflowInput:
    run_ids = ["67817a2e1f52f47f628af971", "67817a2eba5a3f02b9f2938d"]
    omex_input = RunsVerifyWorkflowInput(biosimulations_run_ids=run_ids, compare_settings=compare_settings)
    return omex_input


@pytest.fixture(scope="function")
def runs_verify_workflow_output_file(fixture_data_dir: Path) -> Path:
    return fixture_data_dir / "RunsVerifyWorkflowOutput_expected.json"


@pytest.fixture(scope="function")
def runs_verify_workflow_output(runs_verify_workflow_input: RunsVerifyWorkflowInput,
                                runs_verify_workflow_output_file: Path,
                                runs_verify_workflow_id: str) -> VerifyWorkflowOutput:
    with open(runs_verify_workflow_output_file) as f:
        workflow_output = VerifyWorkflowOutput.model_validate_json(f.read())
        workflow_output.workflow_id = runs_verify_workflow_id
        return workflow_output


@pytest.fixture(scope="function")
def omex_test_file(fixture_data_dir: Path) -> Path:
    return fixture_data_dir / "BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex"


@pytest.fixture(scope="function")
def hdf5_json_test_file(fixture_data_dir: Path) -> Path:
    return fixture_data_dir / "hdf5_file.json"


@pytest.fixture(scope="function")
def temp_test_data_dir() -> Generator[Path, None, None]:
    temp_data_dir.mkdir(exist_ok=True)

    yield temp_data_dir

    for file in temp_data_dir.iterdir():
        file.unlink()
    temp_data_dir.rmdir()
