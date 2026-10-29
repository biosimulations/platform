"""Pairwise comparison of numerical datasets that are already in memory.

This module does not download simulation results and does not submit simulator jobs.
"""

import logging
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from biosim_server.biosim_runs import Hdf5DataValues
from biosim_server.biosim_verify.models import (
    CompareSettings,
    ComparisonStatistics,
    GenerateStatisticsActivityOutput,
    SimulationRunInfo,
)

logger = logging.getLogger(__name__)

NDArray1b: TypeAlias = np.ndarray[tuple[int], np.dtype[np.bool]]
NDArray1f: TypeAlias = np.ndarray[tuple[int], np.dtype[np.float64]]


def simulator_label(info: SimulationRunInfo) -> str:
    version = info.biosim_sim_run.simulator_version
    return f"{version.id}:{version.version}"


def calc_stats(
    arr1: NDArray[np.float64], arr2: NDArray[np.float64], rel_tol: float, abs_tol_min: float, atol_scale: float,
) -> tuple[NDArray1b, NDArray1f]:
    """Per-variable score and closeness, matching ``hdf5_compare.compare_arrays``."""
    assert arr1.shape == arr2.shape
    assert len(arr1.shape) == 2

    max1 = np.nanmax(a=arr1, axis=1)
    max2 = np.nanmax(a=arr2, axis=1)
    abs_tol_arrays = np.zeros((3, arr1.shape[0]))
    abs_tol_arrays[0, :] = np.multiply(np.ones(shape=arr1.shape[0]), abs_tol_min)
    abs_tol_arrays[1, :] = np.multiply(max1, atol_scale)
    abs_tol_arrays[2, :] = np.multiply(max2, atol_scale)
    atol_array = np.max(abs_tol_arrays, axis=0)

    rel_tol_array = np.multiply(np.ones(shape=arr1.shape[0]), rel_tol)
    numerator = np.abs(arr1 - arr2)
    scaled_arr2 = np.multiply(rel_tol_array[:, np.newaxis], np.abs(arr2))
    denominator = np.add(atol_array[:, np.newaxis], scaled_arr2)
    score: NDArray1f = np.nanmax(a=np.divide(numerator, denominator), axis=1)
    is_close: NDArray1b = np.less(score, 1.0)
    assert len(is_close.shape) == 1 and len(score.shape) == 1 and is_close.shape == score.shape
    return is_close, score


def compare_loaded_runs(
    sim_run_info_list: list[SimulationRunInfo],
    datasets: dict[str, dict[str, Hdf5DataValues]],
    compare_settings: CompareSettings,
) -> GenerateStatisticsActivityOutput:
    """Compare ``datasets`` already loaded for ``sim_run_info_list``.

    ``datasets`` maps run id to dataset name to values. Missing datasets become
    error cells. This function does not fetch data.
    """
    num_runs = len(sim_run_info_list)
    dataset_names: set[str] = set()
    for info in sim_run_info_list:
        for group in info.hdf5_file.groups:
            for dataset in group.datasets:
                dataset_names.add(dataset.name)
    logger.info("Found %s unique datasets", len(dataset_names))

    comparison_statistics: dict[str, list[list[ComparisonStatistics]]] = {}
    for dataset_name in dataset_names:
        ds_comparison: list[list[ComparisonStatistics]] = []
        for run_index_i in range(num_runs):
            sim_run_info_i = sim_run_info_list[run_index_i]
            run_id_i = sim_run_info_i.biosim_sim_run.id
            results_i = datasets.get(run_id_i, {})
            simulation_version_i = simulator_label(sim_run_info_i)
            ds_comparison_i: list[ComparisonStatistics] = []

            if dataset_name not in results_i or dataset_name not in sim_run_info_i.hdf5_file.datasets:
                for sim_run_info_j in sim_run_info_list:
                    simulation_version_j = simulator_label(sim_run_info_j)
                    err_msg = f"Dataset {dataset_name} not found in results for {simulation_version_i}"
                    logger.error(err_msg)
                    ds_comparison_i.append(ComparisonStatistics(
                        simulator_version_i=simulation_version_i,
                        simulator_version_j=simulation_version_j,
                        dataset_name=dataset_name,
                        var_names=[],
                        error_message=err_msg,
                    ))
                ds_comparison.append(ds_comparison_i)
                continue

            labels_i = sim_run_info_i.hdf5_file.datasets[dataset_name].sedml_labels
            data_i = results_i[dataset_name]
            array_i: NDArray[np.float64] = np.array(data_i.values, dtype=np.float64).reshape(data_i.shape)

            for sim_run_info_j in sim_run_info_list:
                run_id_j = sim_run_info_j.biosim_sim_run.id
                results_j = datasets.get(run_id_j, {})
                simulation_version_j = simulator_label(sim_run_info_j)
                stats_i_j = ComparisonStatistics(
                    simulator_version_i=simulation_version_i,
                    simulator_version_j=simulation_version_j,
                    dataset_name=dataset_name,
                    var_names=labels_i,
                )
                if dataset_name not in results_j or dataset_name not in sim_run_info_j.hdf5_file.datasets:
                    stats_i_j.error_message = f"Dataset {dataset_name} not found in results for {simulation_version_j}"
                    logger.error(stats_i_j.error_message)
                    ds_comparison_i.append(stats_i_j)
                    continue
                labels_j = sim_run_info_j.hdf5_file.datasets[dataset_name].sedml_labels
                if labels_i != labels_j:
                    stats_i_j.error_message = (
                        f"Variables of {simulation_version_i} and {simulation_version_j} do not match, "
                        f"{labels_i} != {labels_j}"
                    )
                    logger.error(stats_i_j.error_message)
                    ds_comparison_i.append(stats_i_j)
                    continue
                data_j = results_j[dataset_name]
                array_j: NDArray[np.float64] = np.array(data_j.values, dtype=np.float64).reshape(data_j.shape)
                if array_i.shape != array_j.shape:
                    stats_i_j.error_message = (
                        f"Shapes of {simulation_version_i} and {simulation_version_j} do not match, "
                        f"{array_i.shape} != {array_j.shape}"
                    )
                    logger.error(stats_i_j.error_message)
                    ds_comparison_i.append(stats_i_j)
                    continue
                is_close, score = calc_stats(
                    arr1=array_i, arr2=array_j,
                    rel_tol=compare_settings.rel_tol,
                    abs_tol_min=compare_settings.abs_tol_min,
                    atol_scale=compare_settings.abs_tol_scale,
                )
                stats_i_j.score = score.tolist()
                stats_i_j.is_close = is_close.tolist()
                stats_i_j.error_message = None
                ds_comparison_i.append(stats_i_j)
                logger.info(
                    "Comparing %s:run=%s and %s:run=%s for dataset %s",
                    simulation_version_i, run_id_i, simulation_version_j, run_id_j, dataset_name,
                )
            ds_comparison.append(ds_comparison_i)
        comparison_statistics[dataset_name] = ds_comparison
    return GenerateStatisticsActivityOutput(
        sims_run_info=sim_run_info_list, comparison_statistics=comparison_statistics,
    )
