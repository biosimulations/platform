"""Unit tests for parsing biosimulations.org /runs/{id} responses.

No network: drives _sim_run_from_response against a captured payload
(tests/fixtures/local_data/biosim_run_response.json) and a minimal one.
"""

import json
import os
from pathlib import Path
from typing import Mapping
from unittest.mock import patch

import pytest

from biosim_server.biosim_runs import BiosimulatorVersion
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest, _sim_run_from_response
from biosim_server.biosim_runs.models import BiosimSimulationRunStatus
from biosim_server.config import Settings, get_settings


def _simulator_version_from(res: Mapping[str, object]) -> BiosimulatorVersion:
    return BiosimulatorVersion(
        id=str(res["simulator"]),
        name=str(res["simulator"]),
        version=str(res["simulatorVersion"]),
        image_url="",
        image_digest=str(res["simulatorDigest"]),
        created="",
        updated="",
    )


def test_sim_run_from_response_parses_run_metadata(fixture_data_dir: Path) -> None:
    """A full /runs/{id} payload populates the run-metadata fields."""
    res = json.loads((fixture_data_dir / "biosim_run_response.json").read_text())
    sim_run = _sim_run_from_response(res, _simulator_version_from(res))

    assert sim_run.id == "67817a2e1f52f47f628af971"
    assert sim_run.status == BiosimSimulationRunStatus.SUCCEEDED
    assert sim_run.cpus == 1
    assert sim_run.memory == 8
    assert sim_run.max_time == 600
    assert sim_run.env_vars == []
    assert sim_run.purpose == "other"
    assert sim_run.project_size == 283848
    assert sim_run.results_size == 747060
    assert sim_run.runtime == 29872
    assert sim_run.submitted == "2025-01-10T19:51:11.934Z"
    assert sim_run.updated == "2025-01-10T19:51:41.807Z"
    assert sim_run.email is None


def test_sim_run_from_minimal_response_leaves_metadata_none() -> None:
    """A minimal payload (older/in-flight run) leaves the new fields None."""
    res = {
        "id": "abc123",
        "name": "n",
        "simulator": "copasi",
        "simulatorVersion": "4.34.251",
        "simulatorDigest": "sha256:x",
        "status": "SUCCEEDED",
    }
    sim_run = _sim_run_from_response(res, _simulator_version_from(res))

    assert sim_run.cpus is None
    assert sim_run.env_vars is None
    assert sim_run.runtime is None
    assert sim_run.email is None

# --- audit P1 item 4: get_sim_run honours the configured API base URL --- #


class _FakeRunResponse:
    """Canned ``/runs/{id}`` payload plus the URL it was requested from."""

    def __init__(self, payload: dict[str, object], url: str) -> None:
        self._payload = payload
        self.url = url

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self._payload

    async def __aenter__(self) -> "_FakeRunResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeRunSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> "_FakeRunSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def get(self, url: str, **kwargs: object) -> _FakeRunResponse:
        self.requested_urls.append(url)
        return _FakeRunResponse(self._payload, url)


@pytest.mark.asyncio
async def test_get_sim_run_uses_configured_api_base_url() -> None:
    """The polling path must use the configured base URL, not a hardcoded/env-var one."""
    payload: dict[str, object] = {
        "id": "abc123",
        "name": "n",
        "simulator": "copasi",
        "simulatorVersion": "4.34.251",
        "simulatorDigest": "sha256:x",
        "status": "SUCCEEDED",
    }
    session = _FakeRunSession(payload)
    service = BiosimServiceRest()
    simulator_version = BiosimulatorVersion(
        id="copasi", name="copasi", version="4.34.251", image_url="", image_digest="sha256:x",
        created="", updated="",
    )

    async def fake_get_simulator_version(sim_id: str, sim_ver: str, sim_digest: str) -> BiosimulatorVersion:
        return simulator_version

    service._get_simulator_version = fake_get_simulator_version  # type: ignore[method-assign]

    settings = get_settings()
    with (
        patch("biosim_server.biosim_runs.biosim_service.aiohttp.ClientSession", return_value=session),
        patch("biosim_server.biosim_runs.biosim_service.get_settings") as mock_get_settings,
    ):
        mock_get_settings.return_value = Settings(biosimulations_api_base_url="https://staging.example.org")
        sim_run = await service.get_sim_run("abc123")

    assert session.requested_urls == ["https://staging.example.org/runs/abc123"]
    assert sim_run.id == "abc123"
    # The legacy env var is no longer consulted at all.
    with patch.dict(os.environ, {"API_BASE_URL": "https://ignored.example.org"}):
        session.requested_urls.clear()
        with (
            patch("biosim_server.biosim_runs.biosim_service.aiohttp.ClientSession", return_value=session),
            patch("biosim_server.biosim_runs.biosim_service.get_settings") as mock_get_settings,
        ):
            mock_get_settings.return_value = Settings(biosimulations_api_base_url="https://staging.example.org")
            await service.get_sim_run("abc123")
    assert session.requested_urls == ["https://staging.example.org/runs/abc123"]
    assert settings.biosimulations_api_base_url  # default is still the real API
