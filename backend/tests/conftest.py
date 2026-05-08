import pytest  # noqa: F401
import pytest_asyncio  # noqa: F401
from _pytest.config.argparsing import Parser

from tests.fixtures.biosim_fixtures import (  # noqa: F401
    biosim_service_mock,
    biosim_service_rest
)
from tests.fixtures.database_fixtures import (  # noqa: F401
    mongodb_container,
    mongo_test_client,
    mongo_test_database,
    mongo_test_collection,
    database_service_mongo,
    omex_database_service_mongo
)
from tests.fixtures.gcs_fixtures import (  # noqa: F401
    file_service_gcs,
    file_service_local,
    file_service_gcs_test_base_path,
    gcs_token
)
from tests.fixtures.temporal_fixtures import (  # noqa: F401
    temporal_env,
    temporal_client,
    temporal_verify_worker
)
from tests.fixtures.workflow_fixtures import (  # noqa: F401
    omex_verify_workflow_id,
    omex_verify_workflow_input,
    omex_verify_workflow_output,
    omex_verify_workflow_output_file,
    runs_verify_workflow_id,
    runs_verify_workflow_input,
    runs_verify_workflow_output,
    runs_verify_workflow_output_file,
    compare_settings,
    omex_test_file,
    hdf5_json_test_file,
    temp_test_data_dir,
    simulator_version_tellurium,
    simulator_version_copasi,
    fixture_data_dir
)
from tests.fixtures.slurm_fixtures import (  # noqa: F401
    slurm_service,
    ssh_service,
    slurm_template_hello,
)


# Add the --workflow-environment option
def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--workflow-environment",
        action="store",
        default="local",
        help="Specify the workflow environment"
    )

# If you need to redefine or extend any fixtures, you can do so here.
