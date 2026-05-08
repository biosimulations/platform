import json
from pathlib import Path

from biosim_server.biosim_runs import HDF5File


def test_hdf5_json(hdf5_json_test_file: Path) -> None:
    with open(hdf5_json_test_file, 'r') as file:
        hdf5_file_dict = json.load(file)
        hdf5_file = HDF5File.model_validate(hdf5_file_dict)
        assert hdf5_file is not None

