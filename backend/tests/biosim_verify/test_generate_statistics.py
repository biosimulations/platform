"""Exercise the activity with real comparison math and mocked data downloads."""
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from biosim_server.biosim_runs.models import (
    BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorVersion, Hdf5DataValues,
)
from biosim_server.biosim_verify.activities import GenerateStatisticsActivityInput, generate_statistics_activity
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
