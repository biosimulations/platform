from pathlib import Path

import numpy as np
import pytest

from biosim_server.biosim_verify.activities import calc_stats
from biosim_server.biosim_verify.hdf5_compare import get_results, compare_arrays, compare_datasets


@pytest.mark.asyncio
async def test_compare_arrays(fixture_data_dir: Path) -> None:
    results_zip: Path = fixture_data_dir / "modeldb-206365-outputs.zip"
    results1 = get_results(results_zip_file=results_zip)
    results2 = get_results(results_zip_file=results_zip)

    arr1 = results1["outputs/reports.h5"]["/Fig. 2/B/Bazh_PY_altKCC2_Ko_Cli_min_burst_Ko_Cli_fix_NEW.sedml/plot"]
    arr2 = results2["outputs/reports.h5"]["/Fig. 2/B/Bazh_PY_altKCC2_Ko_Cli_min_burst_Ko_Cli_fix_NEW.sedml/plot"]
    assert compare_arrays(arr1, arr2) == (True, 0.0)
    assert compare_datasets(results1, results2) == (True, 0.0)

    arr1[0][0] = 999.0
    arr1[1][0] = 52.0

    assert compare_arrays(arr1, arr2) == (False, 99900.0)
    assert compare_datasets(results1, results2) == (False, 99900.0)

    is_close, score = calc_stats(arr1=arr1, arr2=arr2, rel_tol=1e-4, abs_tol_min=1e-3, atol_scale=1e-5)
    assert is_close.tolist() == [False, False]
    assert float(np.max(score)) == 99900.0
    assert score.tolist() == [99900.0, 15742.038666806353]

    print(results1.keys())

def test_calc_stats() -> None:
    # test calc_stats with two 2D arrays of the same shape whose elements are randomly generated
    # set the random seed for reproducibility
    np.random.seed(0)

    for i in range(20):
        # choose scale and offset randomly with scale on a log scale from 1e-6 to 1e6 and offset from -1e3 to 1e3
        scale = 10**np.random.uniform(-6, 6)
        offset = np.random.uniform(-1e3, 1e3)

        arr1 = np.random.rand(10, 10)*scale+offset
        arr2 = np.random.rand(10, 10)*scale+offset
        is_close_arr, score_arr = calc_stats(arr1=arr1, arr2=arr2, rel_tol=1e-4, abs_tol_min=1e-3, atol_scale=1e-5)
        assert is_close_arr.shape == (10,)
        assert score_arr.shape == (10,)
        assert is_close_arr.dtype == np.bool
        assert score_arr.dtype == np.float64
        is_close = bool(np.all(is_close_arr))
        score = float(np.max(score_arr))

        is_close_prev, score_prev = compare_arrays(arr1, arr2)
        assert is_close_prev == is_close
        if not is_close:
            print(i)
        assert score_prev == score
