"""Pure-unit tests for find_common_datasets (Step 9 compatibility preflight).

Tests 31-35 from the plan.  No I/O, no fixtures — just plain pytest.
"""

from biosim_server.biosim_runs.models import HDF5File, HDF5Group, HDF5Dataset, HDF5Attribute
from biosim_server.biosim_verify.compatibility import find_common_datasets


def _file(run_id: str, datasets: dict[str, list[str]]) -> HDF5File:
    """Build a minimal HDF5File with the given {dataset_name: sedml_labels} mapping."""
    groups = []
    for name, labels in datasets.items():
        attr = HDF5Attribute(key="sedmlDataSetLabels", value=labels)
        ds = HDF5Dataset(name=name, shape=[len(labels), 10], attributes=[attr])
        groups.append(HDF5Group(name="g", attributes=[], datasets=[ds]))
    return HDF5File(filename="f.h5", id=run_id, uri=f"uri/{run_id}", groups=groups)


# ---------------------------------------------------------------------------
# Test 31 — identical datasets and labels → all common
# ---------------------------------------------------------------------------

def test_same_datasets_all_common() -> None:
    files = {
        "r1": _file("r1", {"ds/A": ["t", "x"], "ds/B": ["t", "y"]}),
        "r2": _file("r2", {"ds/A": ["t", "x"], "ds/B": ["t", "y"]}),
    }
    common = find_common_datasets(files)
    assert set(common) == {"ds/A", "ds/B"}
    assert common["ds/A"] == ["t", "x"]


# ---------------------------------------------------------------------------
# Test 32 — disjoint dataset names → empty (different model case)
# ---------------------------------------------------------------------------

def test_disjoint_datasets_empty() -> None:
    files = {
        "r1": _file("r1", {"ds/A": ["t", "x"]}),
        "r2": _file("r2", {"ds/B": ["t", "y"]}),
    }
    assert find_common_datasets(files) == {}


# ---------------------------------------------------------------------------
# Test 33 — same dataset name, different labels → excluded
# ---------------------------------------------------------------------------

def test_same_name_different_labels_excluded() -> None:
    files = {
        "r1": _file("r1", {"ds/A": ["t", "x"]}),
        "r2": _file("r2", {"ds/A": ["t", "z"]}),  # different label
    }
    assert find_common_datasets(files) == {}


# ---------------------------------------------------------------------------
# Test 34 — partial overlap → only shared datasets returned
# ---------------------------------------------------------------------------

def test_partial_overlap() -> None:
    files = {
        "r1": _file("r1", {"ds/shared": ["t", "x"], "ds/only-r1": ["t"]}),
        "r2": _file("r2", {"ds/shared": ["t", "x"], "ds/only-r2": ["t"]}),
    }
    common = find_common_datasets(files)
    assert set(common) == {"ds/shared"}


# ---------------------------------------------------------------------------
# Test 35 — a run with no groups → empty result
# ---------------------------------------------------------------------------

def test_run_with_no_datasets() -> None:
    empty = HDF5File(filename="f.h5", id="r-empty", uri="uri/empty", groups=[])
    files = {
        "r1": _file("r1", {"ds/A": ["t", "x"]}),
        "r-empty": empty,
    }
    assert find_common_datasets(files) == {}


# ---------------------------------------------------------------------------
# Edge: single run → that run's datasets returned as-is
# ---------------------------------------------------------------------------

def test_single_run_returns_all() -> None:
    files = {"r1": _file("r1", {"ds/A": ["t", "x"], "ds/B": ["t"]})}
    common = find_common_datasets(files)
    assert set(common) == {"ds/A", "ds/B"}


# ---------------------------------------------------------------------------
# Edge: empty input → empty result
# ---------------------------------------------------------------------------

def test_empty_input() -> None:
    assert find_common_datasets({}) == {}
