"""Unit tests for parsing biosimulations.org /runs/{id} responses.

No network: drives _sim_run_from_response against a captured payload
(tests/fixtures/local_data/biosim_run_response.json) and a minimal one.
"""

import json
from pathlib import Path
from typing import Mapping

from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.biosim_runs.biosim_service import _sim_run_from_response
from biosim_server.biosim_runs.models import BiosimSimulationRunStatus


def _simulator_version_from(res: Mapping[str, object]) -> BiosimulatorVersion:
    return BiosimulatorVersion(
        id=str(res["simulator"]),
        name=str(res["simulator"]),
        version=str(res["simulatorVersion"]),
        image_url="",
        image_digest=str(res["simulatorDigest"]),
        created="",
        updated="",
    )


def test_sim_run_from_response_parses_run_metadata(fixture_data_dir: Path) -> None:
    """A full /runs/{id} payload populates the run-metadata fields."""
    res = json.loads((fixture_data_dir / "biosim_run_response.json").read_text())
    sim_run = _sim_run_from_response(res, _simulator_version_from(res))

    assert sim_run.id == "67817a2e1f52f47f628af971"
    assert sim_run.status == BiosimSimulationRunStatus.SUCCEEDED
    assert sim_run.cpus == 1
    assert sim_run.memory == 8
    assert sim_run.max_time == 600
    assert sim_run.env_vars == []
    assert sim_run.purpose == "other"
    assert sim_run.project_size == 283848
    assert sim_run.results_size == 747060
    assert sim_run.runtime == 29872
    assert sim_run.submitted == "2025-01-10T19:51:11.934Z"
    assert sim_run.updated == "2025-01-10T19:51:41.807Z"
    assert sim_run.email is None


def test_sim_run_from_minimal_response_leaves_metadata_none() -> None:
    """A minimal payload (older/in-flight run) leaves the new fields None."""
    res = {
        "id": "abc123",
        "name": "n",
        "simulator": "copasi",
        "simulatorVersion": "4.34.251",
        "simulatorDigest": "sha256:x",
        "status": "SUCCEEDED",
    }
    sim_run = _sim_run_from_response(res, _simulator_version_from(res))

    assert sim_run.cpus is None
    assert sim_run.env_vars is None
    assert sim_run.runtime is None
    assert sim_run.email is None
