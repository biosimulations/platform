"""Model-compatibility helpers for the verification preflight check.

Pure functions — no I/O, no dependencies on injected services.  Tested
directly in tests/biosim_verify/test_compatibility.py.
"""

from collections.abc import Mapping

from biosim_server.biosim_runs.models import HDF5File


def find_common_datasets(hdf5_files: Mapping[str, HDF5File]) -> dict[str, list[str]]:
    """Return dataset names present in *every* run with identical sedml_labels.

    Args:
        hdf5_files: mapping of run_id -> HDF5File, covering ≥ 2 runs.

    Returns:
        A dict of {dataset_name: sedml_labels} for datasets that appear in
        all runs with the same label list.  Empty dict means no overlap ⇒
        comparison would produce no meaningful cells.
    """
    if not hdf5_files:
        return {}

    runs = list(hdf5_files.values())

    # Seed with the first run's datasets and labels.
    candidate: dict[str, list[str]] = {
        name: dataset.sedml_labels
        for name, dataset in runs[0].datasets.items()
    }

    # Intersect with each remaining run.
    for run in runs[1:]:
        run_datasets = run.datasets
        to_remove = []
        for name, labels in candidate.items():
            if name not in run_datasets:
                to_remove.append(name)
            elif run_datasets[name].sedml_labels != labels:
                to_remove.append(name)
        for name in to_remove:
            del candidate[name]

    return candidate
