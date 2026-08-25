import os

# #20: the /api/v1/demo/* RBAC router is gated behind ENABLE_RBAC_DEMO (default
# off). The demo and Keycloak integration suites need it mounted, so enable it
# here -- BEFORE any biosim_server import constructs the lru_cached Settings and
# before biosim_server.api.main builds `app` and reads the flag.
os.environ.setdefault("ENABLE_RBAC_DEMO", "true")

from typing import Iterator

import pytest  # noqa: F401
import pytest_asyncio  # noqa: F401
from _pytest.config.argparsing import Parser

from biosim_server.common.ratelimit import _reset_rate_limit_state

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
    omex_database_service_mongo,
    simulation_run_database_service_mongo,
    project_database_service_mongo,
    project_search_service_mongo
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
from tests.fixtures.auth_fixtures import (  # noqa: F401
    authenticated_user,
)

# Fixtures for Keycloak integration tests: a Keycloak container, a test realm,
# and clients/tokens for testing authentication and authorization.
from tests.fixtures.keycloak.container import (  # noqa: F401
    keycloak_container,
    keycloak_realm,
)
from tests.fixtures.keycloak.client import (  # noqa: F401
    keycloak_auth_settings,
    keycloak_async_client,
    alice_token,
    bob_token,
    charlie_token,
    alice_token_namespaced_email,
    alice_token_no_email_claim,
)


@pytest.fixture(autouse=True)
def _reset_ratelimit_buckets_between_tests() -> Iterator[None]:
    """
    Session-wide isolation for the workflow rate limiter (TODO P1 #10).

    common/ratelimit.py keeps its counters in a module-level dict, keyed by
    caller identity. Every test that drives a workflow-starting endpoint
    through TestClient (e.g. /simulations/run, /verify/omex, /verify/runs)
    does so as the same anonymous "testclient" IP identity -- without a
    reset, those counters accumulate across the whole test session and an
    unrelated later test can trip the real 429 once the default
    anonymous_per_window quota is exhausted.
    """
    _reset_rate_limit_state()
    yield
    _reset_rate_limit_state()


# Add the --workflow-environment option
def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--workflow-environment",
        action="store",
        default="local",
        help="Specify the workflow environment"
    )

# If you need to redefine or extend any fixtures, you can do so here.
