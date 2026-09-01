import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncGenerator

from pydantic import TypeAdapter
from urllib.parse import quote

import aiofiles
import aiohttp
from aiocache import SimpleMemoryCache, cached  # type: ignore
from aiohttp import FormData
from typing_extensions import override

from biosim_server.biosim_runs.models import BiosimulatorVersion, BiosimSimulationRun, \
    BiosimSimulationRunStatus, HDF5File, Hdf5DataValues, BiosimSimulationRunApiRequest
from biosim_server.config import get_settings
from biosim_server.common.biosim_api import KisaoTerm, OutputResults, ProjectFile, ProjectSummary, \
    RunLog, SedDocumentSpec, SimulationRunSummary, local_kisao_term, upstream_kisao_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Reused per call: building a TypeAdapter compiles a validator, so hoist it out
# of the request path.
_PROJECT_FILES_ADAPTER = TypeAdapter(list[ProjectFile])
_SED_DOCUMENTS_ADAPTER = TypeAdapter(list[SedDocumentSpec])


def _sim_run_from_response(res: dict[str, Any], simulator_version: BiosimulatorVersion) -> BiosimSimulationRun:
    """Build a BiosimSimulationRun from a biosimulations.org /runs/{id} response.

    Run metadata (cpus/memory/maxTime/...) is populated when present; older or
    in-flight runs may omit some fields, hence the .get() defaults. See
    tests/fixtures/local_data/biosim_run_response.json for a sample payload."""
    return BiosimSimulationRun(
        id=res["id"],
        name=res["name"],
        simulator_version=simulator_version,
        status=BiosimSimulationRunStatus(res["status"]),
        cpus=res.get("cpus"),
        memory=res.get("memory"),
        max_time=res.get("maxTime"),
        env_vars=res.get("envVars"),
        purpose=res.get("purpose"),
        submitted=res.get("submitted"),
        updated=res.get("updated"),
        project_size=res.get("projectSize"),
        results_size=res.get("resultsSize"),
        runtime=res.get("runtime"),
        email=res.get("email"),
        simulator_id=res.get("simulator"),
        simulator_version_string=res.get("simulatorVersion"),
    )


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
    async def get_sim_run_logs(self, simulation_run_id: str) -> dict[str, Any]:
        pass

    @abstractmethod
    async def get_project_summary(self, project_id: str) -> ProjectSummary:
        pass

    @abstractmethod
    async def get_run_summary(self, simulation_run_id: str) -> SimulationRunSummary:
        pass

    @abstractmethod
    async def get_run_files(self, simulation_run_id: str) -> list[ProjectFile]:
        pass

    @abstractmethod
    async def get_run_specifications(self, simulation_run_id: str) -> list[SedDocumentSpec]:
        pass

    @abstractmethod
    async def get_run_log(self, simulation_run_id: str) -> RunLog:
        pass

    @abstractmethod
    async def get_output_results(self, simulation_run_id: str, output_id: str) -> OutputResults:
        pass

    @abstractmethod
    async def get_kisao_term(self, kisao_id: str) -> KisaoTerm:
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
        simulator_version = await self._get_simulator_version(sim_id=sim_id, sim_ver=sim_ver, sim_digest=sim_digest)
        sim_run = _sim_run_from_response(res, simulator_version)
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

        simulator_version = await self._get_simulator_version(sim_id=sim_id, sim_ver=sim_ver, sim_digest=sim_digest)
        sim_run = _sim_run_from_response(res, simulator_version)

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
    async def get_sim_run_logs(self, simulation_run_id: str) -> dict[str, Any]:
        api_base_url = get_settings().biosimulations_api_base_url
        assert (api_base_url is not None)

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{api_base_url}/logs/{simulation_run_id}") as resp:
                resp.raise_for_status()
                logs: dict[str, Any] = await resp.json()
                return logs

    async def _get_biosim_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET `path` on the biosimulations.org API and return the decoded JSON.

        `path` must already be percent-encoded: every id reaching these endpoints
        is caller-supplied and may contain '/' or '?', which would otherwise
        reshape the upstream URL.

        raises ClientResponseError if the response status is not 2xx.
        """
        api_base_url = get_settings().biosimulations_api_base_url
        assert (api_base_url is not None)

        # Omit `params` entirely when there are none, so the outgoing call is
        # session.get(url) rather than session.get(url, params=None).
        kwargs: dict[str, Any] = {} if params is None else {"params": params}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{api_base_url}{path}", **kwargs) as resp:
                resp.raise_for_status()
                payload: Any = await resp.json()
                return payload

    @override
    async def get_project_summary(self, project_id: str) -> ProjectSummary:
        """ raises ClientResponseError if the response status is not 2xx """
        payload = await self._get_biosim_json(f"/projects/{quote(project_id, safe='')}/summary")
        return ProjectSummary.model_validate(payload)

    @override
    async def get_run_summary(self, simulation_run_id: str) -> SimulationRunSummary:
        """``GET /runs/{id}/summary``.

        Returns the same object that a project summary already embeds under
        `simulationRun` -- callers holding a ProjectSummary must not call this.

        raises ClientResponseError if the response status is not 2xx.
        """
        payload = await self._get_biosim_json(
            f"/runs/{quote(simulation_run_id, safe='')}/summary"
        )
        return SimulationRunSummary.model_validate(payload)

    @override
    async def get_run_files(self, simulation_run_id: str) -> list[ProjectFile]:
        """``GET /files/{run_id}`` -- every file in the run's archive.

        raises ClientResponseError if the response status is not 2xx.
        """
        payload = await self._get_biosim_json(f"/files/{quote(simulation_run_id, safe='')}")
        if not isinstance(payload, list):
            # The contract is a bare array. Anything else is an upstream change we
            # cannot represent -- log it and degrade to "no files" rather than 500.
            logger.warning(
                f"Expected a JSON array from /files/{simulation_run_id}, got {type(payload).__name__}"
            )
            return []
        return _PROJECT_FILES_ADAPTER.validate_python(payload)

    @override
    async def get_run_specifications(self, simulation_run_id: str) -> list[SedDocumentSpec]:
        """``GET /specifications/{run_id}`` -- the run's SED-ML documents.

        Upstream returns an **array**: an archive may hold more than one SED-ML
        document. A lone object is still accepted and wrapped, so either shape
        parses.

        raises ClientResponseError if the response status is not 2xx.
        """
        payload = await self._get_biosim_json(
            f"/specifications/{quote(simulation_run_id, safe='')}"
        )
        if isinstance(payload, dict):
            payload = [payload]
        elif not isinstance(payload, list):
            logger.warning(
                f"Expected a JSON array from /specifications/{simulation_run_id}, "
                f"got {type(payload).__name__}"
            )
            return []
        return _SED_DOCUMENTS_ADAPTER.validate_python(payload)

    @override
    async def get_run_log(self, simulation_run_id: str) -> RunLog:
        """``GET /logs/{run_id}``, typed.

        Distinct from `get_sim_run_logs`, which returns the same payload as a raw
        dict and is what `GET /simulations/{id}/logs` passes through untouched.

        raises ClientResponseError if the response status is not 2xx.
        """
        payload = await self._get_biosim_json(f"/logs/{quote(simulation_run_id, safe='')}")
        return RunLog.model_validate(payload)

    @override
    async def get_output_results(self, simulation_run_id: str, output_id: str) -> OutputResults:
        """``GET /results/{run_id}/{output_id}?includeData=true``.

        `output_id` is composite ("{sedDocLocation}/{outputId}") and so contains a
        '/', which must be percent-encoded as data rather than left to split the
        upstream path.

        raises ClientResponseError if the response status is not 2xx.
        """
        payload = await self._get_biosim_json(
            f"/results/{quote(simulation_run_id, safe='')}/{quote(output_id, safe='')}",
            params={"includeData": "true"},
        )
        return OutputResults.model_validate(payload)

    @override
    async def get_kisao_term(self, kisao_id: str) -> KisaoTerm:
        """``GET /ontologies/KISAO/{id}``, falling back to the vendored table.

        Accepts either id spelling. If upstream is unreachable or does not know
        the term, a locally-known term still yields a name and an OLS link --
        degrading a log's algorithm label is better than failing the lookup.
        An id unknown both upstream and locally re-raises, so the route 404s.

        raises ClientResponseError if the term cannot be resolved at all.
        """
        try:
            # Annotated because the aiocache decorator is untyped, so the awaited
            # call would otherwise widen to Any and slip past strict checking.
            term: KisaoTerm = await self._fetch_kisao_term(upstream_kisao_id(kisao_id))
            return term
        except aiohttp.ClientError as e:
            local = local_kisao_term(kisao_id)
            if local is None:
                raise
            logger.info(f"KISAO lookup for {kisao_id} fell back to the local table: {e}")
            return local

    # Cached, not the public method above: caching the *successful* upstream
    # response avoids pinning a degraded fallback for an hour. Keyed on the
    # normalized id, so both spellings share one entry. Mirrors the TTL cache on
    # get_simulator_versions.
    @cached(ttl=3600, cache=SimpleMemoryCache)  # type: ignore
    async def _fetch_kisao_term(self, upstream_id: str) -> KisaoTerm:
        payload = await self._get_biosim_json(
            f"/ontologies/KISAO/{quote(upstream_id, safe='')}"
        )
        return KisaoTerm.model_validate(payload)

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
