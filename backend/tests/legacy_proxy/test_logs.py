"""GET /logs/{run_id} — execution logs at four nesting levels.

Also guards the compatibility line: the raw `get_sim_run_logs` used by
`GET /simulations/{id}/logs` keeps returning a plain dict.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimService, BiosimServiceRest
from biosim_server.common.biosim_api import LogEntry, RunLog
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

_RUN_ID = "61fea483f499ccf25faafc4d"

# Raw stdout carries ANSI escapes (the frontend runs it through Anser).
_ANSI_OUTPUT = "[32mRunning simulation...[0m"

_LOG_JSON: dict[str, Any] = {
    "status": "SUCCEEDED",
    "output": _ANSI_OUTPUT,
    "algorithm": None,
    "sedDocuments": [
        {
            "location": "./simulation.sedml",
            "status": "SUCCEEDED",
            "algorithm": "KISAO_0000019",
            "output": "doc output",
            "tasks": [
                {
                    "id": "task_1",
                    "status": "SUCCEEDED",
                    "algorithm": "KISAO_0000019",
                    "output": "task output",
                },
                {
                    "id": "task_2",
                    "status": "SKIPPED",
                    "skipReason": {"type": "NotImplemented", "message": "unsupported"},
                },
                {
                    "id": "task_3",
                    "status": "FAILED",
                    "exception": {"type": "ValueError", "message": "boom"},
                },
            ],
            "outputs": [
                {"id": "report_1", "status": "SUCCEEDED", "dataSets": ["ds_time", "ds_x"]},
                {"id": "plot_1", "status": "SUCCEEDED", "output": "plot output"},
            ],
        }
    ],
}


def test_log_entry_fields_are_shared_at_every_level() -> None:
    """One LogEntry base, four levels — the same five fields reachable on each."""
    log = RunLog.model_validate(_LOG_JSON)
    doc = log.sed_documents[0]
    task = doc.tasks[0]
    output = doc.outputs[0]
    for entry in (log, doc, task, output):
        assert isinstance(entry, LogEntry)
        # Every level answers all five without a level-specific special case.
        _ = (entry.status, entry.algorithm, entry.output, entry.skip_reason, entry.exception)
    assert log.status == "SUCCEEDED"
    assert log.output == _ANSI_OUTPUT
    assert doc.location == "./simulation.sedml"
    assert doc.algorithm == "KISAO_0000019"
    assert task.id == "task_1"
    assert output.id == "report_1"


def test_skip_reason_and_exception_are_none_when_absent() -> None:
    """Absence drives rendering: a synthesized empty object would show a spurious
    'Skipped'/'Exception' block."""
    log = RunLog.model_validate(_LOG_JSON)
    tasks = {t.id: t for t in log.sed_documents[0].tasks}
    assert tasks["task_1"].skip_reason is None
    assert tasks["task_1"].exception is None

    skipped = tasks["task_2"]
    assert skipped.skip_reason is not None
    assert skipped.skip_reason.type_ == "NotImplemented"
    assert skipped.skip_reason.message == "unsupported"
    assert skipped.exception is None

    failed = tasks["task_3"]
    assert failed.exception is not None
    assert failed.exception.type_ == "ValueError"
    assert failed.exception.message == "boom"
    # A sibling failure must not leak onto the other tasks.
    assert failed.skip_reason is None
    assert tasks["task_1"].status == "SUCCEEDED"


def test_output_datasets_presence_separates_reports_from_plots() -> None:
    log = RunLog.model_validate(_LOG_JSON)
    report, plot = log.sed_documents[0].outputs
    assert report.data_sets == ["ds_time", "ds_x"]
    assert plot.data_sets is None


def test_running_simulation_has_no_documents_yet() -> None:
    """A log that exists before any SED document ran must parse."""
    log = RunLog.model_validate({"status": "RUNNING"})
    assert log.status == "RUNNING"
    assert log.sed_documents == []
    assert log.output is None
    assert log.exception is None


def test_empty_task_and_output_arrays_behave_like_absent_ones() -> None:
    absent = RunLog.model_validate({"sedDocuments": [{"location": "a.sedml"}]})
    empty = RunLog.model_validate(
        {"sedDocuments": [{"location": "a.sedml", "tasks": [], "outputs": []}]}
    )
    assert absent.sed_documents[0].tasks == empty.sed_documents[0].tasks == []
    assert absent.sed_documents[0].outputs == empty.sed_documents[0].outputs == []


def test_unknown_log_status_is_accepted() -> None:
    """Execution statuses stay strings; an unfamiliar value must not 500."""
    log = RunLog.model_validate({"status": "PARTIALLY_SUCCEEDED"})
    assert log.status == "PARTIALLY_SUCCEEDED"


@pytest.mark.asyncio
async def test_rest_client_requests_the_logs_url() -> None:
    patcher, session = stub_session(_LOG_JSON)
    with patcher:
        log = await BiosimServiceRest().get_run_log(_RUN_ID)
    session.get.assert_called_once_with(f"https://api.biosimulations.org/logs/{_RUN_ID}")
    assert log.sed_documents[0].tasks[1].skip_reason is not None


@pytest.mark.asyncio
async def test_rest_client_quotes_the_run_id() -> None:
    patcher, session = stub_session(_LOG_JSON)
    with patcher:
        await BiosimServiceRest().get_run_log("../secret?x=1")
    session.get.assert_called_once_with(
        "https://api.biosimulations.org/logs/..%2Fsecret%3Fx%3D1"
    )


@pytest.mark.asyncio
async def test_raw_get_sim_run_logs_still_returns_a_dict() -> None:
    """Compatibility: GET /simulations/{id}/logs passes this payload through
    untouched, so retyping it would break JobLogs.logs."""
    patcher, _ = stub_session(_LOG_JSON)
    with patcher:
        logs = await BiosimServiceRest().get_sim_run_logs(_RUN_ID)
    assert isinstance(logs, dict)
    assert logs == _LOG_JSON
    # Both methods exist side by side on the abstraction.
    assert hasattr(BiosimService, "get_sim_run_logs")
    assert hasattr(BiosimService, "get_run_log")


def test_logs_route_serializes_upstream_keys() -> None:
    biosim = AsyncMock()
    biosim.get_run_log.return_value = RunLog.model_validate(_LOG_JSON)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/logs/{_RUN_ID}")
    assert response.status_code == 200
    body = response.json()
    doc = body["sedDocuments"][0]
    assert doc["location"] == "./simulation.sedml"
    assert doc["tasks"][1]["skipReason"]["type"] == "NotImplemented"
    assert doc["tasks"][2]["exception"]["message"] == "boom"
    assert doc["outputs"][0]["dataSets"] == ["ds_time", "ds_x"]
    biosim.get_run_log.assert_awaited_once_with(_RUN_ID)


def test_logs_route_maps_upstream_404() -> None:
    """No log yet is a normal state for a run that has not started."""
    biosim = AsyncMock()
    biosim.get_run_log.side_effect = upstream_error(404)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/logs/{_RUN_ID}")
    assert response.status_code == 404
    assert _RUN_ID in response.json()["detail"]
