import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator

import aiofiles
import aiohttp
from aiocache import SimpleMemoryCache, cached  # type: ignore
from aiohttp import FormData
from typing_extensions import override

from biosim_server.biosim_runs.models import BiosimulatorVersion, BiosimSimulationRun, \
    BiosimSimulationRunStatus, HDF5File, Hdf5DataValues, BiosimSimulationRunApiRequest
from biosim_server.config import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BiosimService(ABC):

    @abstractmethod
    async def get_sim_run(self, simulation_run_id: str) -> BiosimSimulationRun:
        pass

    @abstractmethod
    async def run_biosim_sim(self, local_omex_path: str, omex_name: str, simulator_version: BiosimulatorVersion) -> BiosimSimulationRun:
        pass

    @abstractmethod
    async def get_hdf5_metadata(self, simulation_run_id: str) -> HDF5File:
        pass

    @abstractmethod
    async def get_hdf5_data(self, simulation_run_id: str, dataset_name: str) -> Hdf5DataValues:
        pass

    @abstractmethod
    async def get_simulator_versions(self) -> list[BiosimulatorVersion]:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass



class BiosimServiceRest(BiosimService):
    @override
    async def get_sim_run(self, simulation_run_id: str) -> BiosimSimulationRun:
        logger.info(f"Polling simulation with simulation run_id {simulation_run_id}")

        """ raises ClientResponseError if the response status is not 2xx """
        api_base_url = os.environ.get('API_BASE_URL') or "https://api.biosimulations.org"
        assert (api_base_url is not None)

        async with aiohttp.ClientSession() as session:
            async with session.get(api_base_url + "/runs/" + simulation_run_id) as resp:
                resp.raise_for_status()
                res = await resp.json()

        assert res["id"] == simulation_run_id

        sim_id: str = res['simulator']
        sim_ver: str = res['simulatorVersion']
        sim_digest: str = res['simulatorDigest']
        sim_status = BiosimSimulationRunStatus(res['status'])
        simulator_version = await self._get_simulator_version(sim_id=sim_id, sim_ver=sim_ver, sim_digest=sim_digest)
        sim_run = BiosimSimulationRun(id=res["id"], name=res["name"], simulator_version=simulator_version, status=sim_status)
        return sim_run


    @override
    async def run_biosim_sim(self, local_omex_path: str, omex_name: str,
                             simulator_version: BiosimulatorVersion) -> BiosimSimulationRun:
        logger.info(f"Submitting simulation for {omex_name} with local path {local_omex_path} with simulator {simulator_version.id}")

        simulation_run_request = BiosimSimulationRunApiRequest(name=omex_name, simulator=simulator_version.id,
                                                               simulatorVersion=simulator_version.version, maxTime=600)

        async with aiohttp.ClientSession() as session:
            with Path(local_omex_path).open('rb') as f:
                data = FormData()
                data.add_field(name='file', value=f, filename='omex.omex', content_type='multipart/form-data')
                data.add_field(name='simulationRun', value=simulation_run_request.model_dump_json(),
                               content_type='multipart/form-data')

                api_base_url = get_settings().biosimulations_api_base_url
                async with session.post(url=api_base_url + '/runs', data=data) as resp:
                    resp.raise_for_status()
                    res = await resp.json()

        sim_id: str = res['simulator']
        sim_ver: str = res['simulatorVersion']
        sim_digest: str = res['simulatorDigest']
        assert simulator_version.version == sim_ver
        assert simulator_version.id == sim_id
        assert simulator_version.image_digest == sim_digest

        sim_status = BiosimSimulationRunStatus(res['status'])
        simulator_version = await self._get_simulator_version(sim_id=sim_id, sim_ver=sim_ver, sim_digest=sim_digest)
        sim_run = BiosimSimulationRun(id=res["id"], name=res["name"], simulator_version=simulator_version, status=sim_status)

        # logger.info("Submitted " + omex_name + " on biosimulations with simulation id: " + sim_run.id)
        # logger.info("View:", api_base_url + "/runs/" + sim_run.id)
        return sim_run


    async def _get_simulator_version(self, sim_id: str, sim_ver: str, sim_digest: str) -> BiosimulatorVersion:
        simulator_version: BiosimulatorVersion
        for simulator_version in await self.get_simulator_versions():
            if simulator_version.id == sim_id and simulator_version.version == sim_ver and simulator_version.image_digest == sim_digest:
                return simulator_version
        raise Exception(f"Simulator version not found for simulator id: {sim_id}, version: {sim_ver}, digest: {sim_digest}")


    @override
    async def get_hdf5_metadata(self, simulation_run_id: str) -> HDF5File:
        api_base_url = get_settings().simdata_api_base_url
        assert (api_base_url is not None)

        async with aiohttp.ClientSession() as session:
            url = f"{api_base_url}/datasets/{simulation_run_id}/metadata"
            async with session.get(url) as resp:
                resp.raise_for_status()
                hdf5_metadata_json = await resp.text()
                hdf5_file: HDF5File = HDF5File.model_validate_json(hdf5_metadata_json)
                return hdf5_file

    @override
    async def get_hdf5_data(self, simulation_run_id: str, dataset_name: str) -> Hdf5DataValues:
        api_base_url = get_settings().simdata_api_base_url
        assert (api_base_url is not None)

        async with aiohttp.ClientSession() as session:
            url = f"{api_base_url}/datasets/{simulation_run_id}/data"
            async with session.get(url, params={"dataset_name": dataset_name}) as resp:
                resp.raise_for_status()
                hdf5_data_dict = await resp.json()
                logger.info(f"Got data for dataset: {dataset_name}")
                hdf5_data_values = Hdf5DataValues(shape=hdf5_data_dict['shape'], values=hdf5_data_dict['values'])
                return hdf5_data_values

    @override
    @cached(ttl=3600, cache=SimpleMemoryCache)  # type: ignore
    async def get_simulator_versions(self) -> list[BiosimulatorVersion]:
        api_base_url = get_settings().biosimulators_api_base_url
        assert (api_base_url is not None)

        async with aiohttp.ClientSession() as session:
            url = f"{api_base_url}/simulators?includeTests=false"
            async with session.get(url) as resp:
                resp.raise_for_status()
                simulation_versions_dict = await resp.json()
                simulation_versions: list[BiosimulatorVersion] = []
                for sim in simulation_versions_dict:
                    if 'image' in sim and 'url' and sim['image'] and 'url' in sim['image'] and 'digest' in sim['image'] \
                        and 'biosimulators' in sim and 'created' in sim['biosimulators'] and 'updated' in sim['biosimulators']:
                        sim_version = BiosimulatorVersion(id=sim["id"], name=sim["name"], version=sim["version"],
                                                          image_url=sim["image"]["url"], image_digest=sim["image"]["digest"],
                                                          created=sim["biosimulators"]["created"], updated=sim["biosimulators"]["updated"])
                        simulation_versions.append(sim_version)
                return simulation_versions

    @override
    async def close(self) -> None:
        pass


async def file_sender(file_name: str) -> AsyncGenerator[bytes, None]:
    async with aiofiles.open(file_name, 'rb') as f:
        chunk = await f.read(64 * 1024)
        while chunk:
            yield chunk
            chunk = await f.read(64 * 1024)
