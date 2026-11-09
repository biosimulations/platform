"""Exercise the activity with real comparison math and mocked data downloads."""
import dataclasses
import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from biosim_server.biosim_runs.models import (
    BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorVersion, Hdf5DataValues,
)
from biosim_server.biosim_verify.activities import (
    STATISTICS_COMPUTE_ATTEMPT_TIMEOUT,
    STATISTICS_COMPUTE_FAILED_TYPE,
    STATISTICS_COMPUTE_MAX_ATTEMPTS,
    STATISTICS_HEARTBEAT_TIMEOUT,
    STATISTICS_RETRY,
    STATISTICS_SCHEDULE_TO_CLOSE,
    STATISTICS_STORAGE_UNAVAILABLE_EVENT,
    GenerateStatisticsActivityInput,
    generate_statistics_activity,
    generate_statistics_activity_v2,
    statistics_storage_retry_delay,
)
from biosim_server.biosim_verify.models import CompareSettings, SimulationRunInfo
from tests.biosim_verify.test_compatibility import _file


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset_names", [
    (["a"], ["b"]),
    (["shared", "a"], ["shared", "b"]),
    (["shared"], ["shared"]),
])
async def test_statistics_handles_dataset_overlap(dataset_names: tuple[list[str], list[str]]) -> None:
    runs = [
        SimulationRunInfo(
            biosim_sim_run=BiosimSimulationRun(
                id=f"run{i}", name=f"run{i}", status=BiosimSimulationRunStatus.SUCCEEDED,
                simulator_version=BiosimulatorVersion(
                    id=f"sim{i}", name=f"sim{i}", version="1", image_url="image", image_digest="digest",
                    created="2026-01-01", updated="2026-01-01",
                ),
            ),
            hdf5_file=_file(f"run{i}", {name: ["t", "x"] for name in names}),
        ) for i, names in enumerate(dataset_names)
    ]
    settings = CompareSettings(user_description="test", include_outputs=True, rel_tol=1e-4,
                               abs_tol_min=1e-3, abs_tol_scale=1e-5)
    data = Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])
    with patch("biosim_server.biosim_verify.activities.BiosimServiceRest.get_hdf5_data",
               new=AsyncMock(return_value=data)) as download:
        output = await ActivityEnvironment().run(generate_statistics_activity,
            GenerateStatisticsActivityInput(sim_run_info_list=runs, compare_settings=settings))
    assert download.await_count == sum(map(len, dataset_names))
    assert output.sim_run_data is not None
    assert len(output.sim_run_data) == download.await_count
    assert set(output.comparison_statistics) == set(dataset_names[0]) | set(dataset_names[1])
    for name, matrix in output.comparison_statistics.items():
        assert len(matrix) == 2
        for i, row in enumerate(matrix):
            assert len(row) == 2
            for j, cell in enumerate(row):
                if name not in dataset_names[i] or name not in dataset_names[j]:
                    assert cell.error_message is not None and "not found" in cell.error_message
                    assert cell.score is None and cell.is_close is None
                else:
                    assert cell.error_message is None
                    assert cell.score == [0.0, 0.0]
                    assert cell.is_close == [True, True]


@pytest.mark.asyncio
async def test_oversized_statistics_crosses_activity_boundary_as_reference() -> None:
    run = SimulationRunInfo(
        biosim_sim_run=BiosimSimulationRun(
            id="run", name="run", status=BiosimSimulationRunStatus.SUCCEEDED,
            simulator_version=BiosimulatorVersion(
                id="sim", name="sim", version="1", image_url="image", image_digest="digest",
                created="2026-01-01", updated="2026-01-01",
            ),
        ),
        hdf5_file=_file("run", {"a": ["t", "x"]}),
    )
    settings = CompareSettings(user_description="test", include_outputs=True, rel_tol=1e-4,
                               abs_tol_min=1e-3, abs_tol_scale=1e-5)
    data = Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])
    artifact_store = SimpleNamespace(put_if_absent=AsyncMock(return_value=True))
    with (
        patch("biosim_server.biosim_verify.activities.BiosimServiceRest.get_hdf5_data",
              new=AsyncMock(return_value=data)),
        # A one-byte inline limit makes any statistics "oversized" for the activity result.
        patch("biosim_server.biosim_verify.activities.get_settings",
              return_value=SimpleNamespace(verification_report_max_decompressed_bytes=64 * 1024 * 1024,
                                           verification_activity_inline_max_bytes=1)),
        patch("biosim_server.biosim_verify.activities.get_file_service",
              return_value=artifact_store),
    ):
        environment = ActivityEnvironment()
        result = await environment.run(
            generate_statistics_activity_v2,
            GenerateStatisticsActivityInput(
                sim_run_info_list=[run], compare_settings=settings, workflow_id="wf-oversized",
            ),
        )
    assert result.output is None
    assert result.reference is not None
    assert artifact_store.put_if_absent.await_count == 1
    uploaded, name = artifact_store.put_if_absent.await_args.args
    assert len(uploaded) > 1
    # Named by workflow and content digest, so a retried activity reuses the object.
    workflow_id = environment.info.workflow_id
    assert name == f"verification-activity/{workflow_id}/{result.reference.content_sha256}.json.gz"
    assert result.reference.artifact_reference == name


# --- Retrying: a storage outage never uses up the compute budget (I-1) ---------------------


def _statistics_input() -> GenerateStatisticsActivityInput:
    run = SimulationRunInfo(
        biosim_sim_run=BiosimSimulationRun(
            id="run", name="run", status=BiosimSimulationRunStatus.SUCCEEDED,
            simulator_version=BiosimulatorVersion(
                id="sim", name="sim", version="1", image_url="image", image_digest="digest",
                created="2026-01-01", updated="2026-01-01",
            ),
        ),
        hdf5_file=_file("run", {"a": ["t", "x"]}),
    )
    settings = CompareSettings(user_description="test", include_outputs=True, rel_tol=1e-4,
                               abs_tol_min=1e-3, abs_tol_scale=1e-5)
    return GenerateStatisticsActivityInput(sim_run_info_list=[run], compare_settings=settings, workflow_id="wf")


async def _run_v2(attempt: int, *, hdf5: AsyncMock, put_if_absent: AsyncMock,
                  max_decompressed_bytes: int = 64 * 1024 * 1024) -> Any:
    """Run the v2 activity as attempt *attempt*, with statistics that must be externalized."""
    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(environment.info, attempt=attempt)
    with (
        patch("biosim_server.biosim_verify.activities.BiosimServiceRest.get_hdf5_data", new=hdf5),
        patch("biosim_server.biosim_verify.activities.get_settings",
              return_value=SimpleNamespace(verification_report_max_decompressed_bytes=max_decompressed_bytes,
                                           verification_activity_inline_max_bytes=1)),
        patch("biosim_server.biosim_verify.activities.get_file_service",
              return_value=SimpleNamespace(put_if_absent=put_if_absent)),
    ):
        return await environment.run(generate_statistics_activity_v2, _statistics_input())


_DATA = Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5])


def test_statistics_storage_backoff_caps_at_five_minutes() -> None:
    assert statistics_storage_retry_delay(1) == timedelta(seconds=1)
    assert statistics_storage_retry_delay(4) == timedelta(seconds=8)
    assert statistics_storage_retry_delay(STATISTICS_COMPUTE_MAX_ATTEMPTS + 50) == timedelta(minutes=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [1, STATISTICS_COMPUTE_MAX_ATTEMPTS + 50])
async def test_a_storage_outage_retries_inside_the_attempt_without_recomputing(
    attempt: int, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Storage retries keep the computed bytes. Temporal's attempt number does not matter."""
    delays: list[int] = []

    def record_delay(storage_attempt: int) -> timedelta:
        delays.append(storage_attempt)
        return timedelta(0)

    monkeypatch.setattr("biosim_server.biosim_verify.activities.statistics_storage_retry_delay", record_delay)
    writes = 0

    async def flaky_put(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal writes
        writes += 1
        if writes < 3:
            raise ConnectionError("storage unreachable")
        return True

    hdf5 = AsyncMock(return_value=_DATA)
    with caplog.at_level(logging.WARNING):
        result = await _run_v2(attempt, hdf5=hdf5, put_if_absent=AsyncMock(side_effect=flaky_put))
    assert hdf5.await_count == 1
    assert writes == 3
    assert delays == [1, 2]
    assert result.reference is not None
    assert caplog.text.count(STATISTICS_STORAGE_UNAVAILABLE_EVENT) == 2


@pytest.mark.asyncio
async def test_a_compute_failure_keeps_the_budget_it_had() -> None:
    upstream_down = AsyncMock(side_effect=ConnectionError("biosimulations.org unreachable"))
    with pytest.raises(ConnectionError):  # retried by the policy, as before
        await _run_v2(STATISTICS_COMPUTE_MAX_ATTEMPTS - 1, hdf5=upstream_down, put_if_absent=AsyncMock())
    with pytest.raises(ApplicationError) as raised:
        await _run_v2(STATISTICS_COMPUTE_MAX_ATTEMPTS, hdf5=upstream_down, put_if_absent=AsyncMock())
    assert raised.value.type == STATISTICS_COMPUTE_FAILED_TYPE and raised.value.non_retryable


@pytest.mark.asyncio
async def test_statistics_over_the_archival_limit_are_still_permanent() -> None:
    put_if_absent = AsyncMock()
    with pytest.raises(ApplicationError) as raised:
        await _run_v2(1, hdf5=AsyncMock(return_value=_DATA), put_if_absent=put_if_absent, max_decompressed_bytes=1)
    assert raised.value.non_retryable
    assert raised.value.message == "Statistics exceed archival size limit"
    put_if_absent.assert_not_awaited()


def test_the_server_does_not_cap_statistics_attempts() -> None:
    assert STATISTICS_RETRY.maximum_attempts == 0
    assert STATISTICS_SCHEDULE_TO_CLOSE == timedelta(days=1)
    # A compute attempt must finish inside the heartbeat window, or a slow
    # comparison is failed for a missed heartbeat and recomputed.
    assert STATISTICS_HEARTBEAT_TIMEOUT > STATISTICS_COMPUTE_ATTEMPT_TIMEOUT
