"""A verification reports the simulators that produced no results, and why, instead of dropping them silently.

Before, a verification whose every simulator run failed on biosimulations.org (for example, an archive whose manifest
lists files it does not contain) answered COMPLETED with empty results and no hint of what went wrong."""

import json
from pathlib import Path

from biosim_server.biosim_runs import BiosimulatorVersion, OmexSimWorkflowOutput, OmexSimWorkflowStatus
from biosim_server.biosim_runs.activities import error_from_run_logs
from biosim_server.biosim_verify.omex_verify_workflow import no_results_error, split_simulator_outcomes

LOCAL_DATA = Path(__file__).parent.parent / "fixtures" / "local_data"


def _version(name: str, version: str) -> BiosimulatorVersion:
    return BiosimulatorVersion(id=name, name=name, version=version, image_url=f"ghcr.io/biosimulators/{name}",
                               image_digest="sha256:" + "0" * 64, created="2025-01-01", updated="2025-01-01")


def _failed(run_id: str | None, error: str | None) -> OmexSimWorkflowOutput:
    return OmexSimWorkflowOutput(workflow_id="w", workflow_status=OmexSimWorkflowStatus.FAILED,
                                 biosimulations_run_id=run_id, error_message=error)


def test_a_failed_runs_reason_is_read_from_its_logs() -> None:
    # A real biosimulations.org run (6ada2269041d31202094ca8f, 2026-10-22): COPASI on BioModels' BIOMD0000000012 archive,
    # whose manifest lists files the archive does not hold. The run's own `error` was empty.
    logs = json.loads((LOCAL_DATA / "biosim_failed_run_logs.json").read_text())
    reason = error_from_run_logs(logs)
    assert reason is not None
    assert reason.startswith("ValueError: `/root/archive.omex` is not a valid COMBINE/OMEX archive.")
    assert "BIOMD0000000012-biopax2.owl" in reason


def test_a_task_level_exception_is_used_when_the_archive_level_one_is_absent() -> None:
    logs = {"exception": None, "sedDocuments": [{"exception": None, "tasks": [
        {"exception": {"type": "RuntimeError", "message": "integration failed at t=3"}}]}]}
    assert error_from_run_logs(logs) == "RuntimeError: integration failed at t=3"
    assert error_from_run_logs({"status": "SUCCEEDED", "exception": None}) is None


def test_simulators_without_results_are_reported_not_dropped() -> None:
    requested = [_version("copasi", "4.45.296"), _version("tellurium", "2.2.10")]
    outputs = [_failed("6ada2269041d31202094ca8f", "ValueError: not a valid COMBINE/OMEX archive.\n  - details"),
               _failed(None, None)]
    runs, failures = split_simulator_outcomes(requested, outputs)
    assert runs == []
    assert [(f.simulator_version, f.biosimulations_run_id) for f in failures] == [
        ("copasi:4.45.296", "6ada2269041d31202094ca8f"), ("tellurium:2.2.10", None)]
    assert failures[1].error_message == "the run ended FAILED"
    error = no_results_error(requested, runs, failures)
    assert error is not None
    assert error.startswith("No requested simulator produced results (copasi:4.45.296: ValueError: not a valid")
    assert "tellurium:2.2.10: the run ended FAILED" in error


def test_a_verification_with_some_results_is_not_a_failure() -> None:
    assert no_results_error([_version("copasi", "1")], runs=[object()], failures=[]) is None  # type: ignore[list-item]
    assert no_results_error([], runs=[], failures=[]) is None
