"""BiosimulatorsService: validation policy, status mapping, header isolation, cache wiring (plan §4.2/§4.3)."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from biosim_server.common.upstream import _PUBLIC_FETCH_HEADERS
from biosim_server.simulators.cache import SimulatorsCache
from biosim_server.simulators.models import OntologyNamespace, SimulatorList
from biosim_server.simulators.service import CACHEABLE_KEYS, BiosimulatorsService

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://api.biosimulators.test"

Handler = Callable[[httpx.Request], httpx.Response]


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))


def _skipped(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith("Skipped an invalid")]


@pytest.mark.asyncio
async def test_list_is_cached_within_the_fresh_window() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_load("simulators_latest.json"))

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        first = await service.get_simulators_latest()
        second = await service.get_simulators_latest()

    assert isinstance(first, SimulatorList)
    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_ontology_terms_are_trimmed_and_cached_per_namespace() -> None:
    calls: list[str] = []
    ontologies = _load("ontology_terms.json")

    def handler(request: httpx.Request) -> httpx.Response:
        namespace = request.url.path.rsplit("/", 1)[-1]
        calls.append(namespace)
        return httpx.Response(200, json=ontologies[namespace])

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        first = await service.get_ontology(OntologyNamespace.EDAM)
        second = await service.get_ontology(OntologyNamespace.EDAM)
        other = await service.get_ontology(OntologyNamespace.SBO)

    assert first == second
    assert [(term.id, term.name) for term in first.terms] == [(t["id"], t["name"]) for t in ontologies["EDAM"]]
    assert [term.id for term in other.terms] == [t["id"] for t in ontologies["SBO"]]
    assert calls == ["EDAM", "SBO"]


@pytest.mark.asyncio
async def test_detail_and_version_lists_are_never_cached() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # "/simulators/{id}" is the version list (an array); anything deeper is a detail.
        if request.url.path.count("/") == 2:
            return httpx.Response(200, json=[_load("simulator_detail.json")])
        return httpx.Response(200, json=_load("simulator_detail.json"))

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        await service.get_simulator_version("copasi", "latest", include_tests=True)
        await service.get_simulator_version("copasi", "latest", include_tests=True)
        versions = await service.get_simulator_versions("copasi")

    assert calls == 3
    assert versions.simulator_id == "copasi"
    assert versions.model_dump(by_alias=True)["simulatorId"] == "copasi"


@pytest.mark.asyncio
async def test_detail_forwards_include_tests_upstream_and_nothing_else() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_load("simulator_detail.json"))

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        detail = await service.get_simulator_version("copasi", "7.7.0", include_tests=True)
        await service.get_simulator_version("copasi", "7.7.0", include_tests=False)

    assert seen == [
        f"{BASE_URL}/simulators/copasi/7.7.0?includeTests=true",
        f"{BASE_URL}/simulators/copasi/7.7.0",
    ]
    # The fixture's validation tests survive the round trip when they were requested.
    assert detail.biosimulators is not None
    assert detail.biosimulators.validation_tests is not None
    assert len(detail.biosimulators.validation_tests.results or []) == 3


@pytest.mark.asyncio
async def test_upstream_sees_exactly_the_public_fetch_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=_load("simulators_latest.json"))

    client = httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer caller-token", "Cookie": "session=1", "Origin": "https://frontend.test"},
    )
    async with client:
        service = BiosimulatorsService(client)
        await service.get_simulators_latest()

    assert seen == {**_PUBLIC_FETCH_HEADERS, "host": "api.biosimulators.test"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("upstream_status", "expected_status"), [(404, 404), (400, 400), (500, 502)])
async def test_upstream_status_mapping(upstream_status: int, expected_status: int) -> None:
    async with _client(lambda request: httpx.Response(upstream_status, json={"detail": "x"})) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as raised:
            await service.get_simulators_latest()

    assert raised.value.status_code == expected_status
    assert "x" not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_upstream_timeout_maps_to_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream")

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as raised:
            await service.get_simulators_latest()

    assert raised.value.status_code == 504


@pytest.mark.asyncio
async def test_upstream_transport_failure_maps_to_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _client(handler) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as raised:
            await service.get_simulators_latest()

    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_oversize_body_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "biosim_server.common.upstream.get_settings",
        lambda: SimpleNamespace(upstream_max_response_bytes=64),
    )
    async with _client(lambda request: httpx.Response(200, json=_load("simulators_latest.json"))) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as raised:
            await service.get_simulators_latest()

    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_malformed_list_entry_is_skipped_with_a_warning_naming_id_and_version(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entries = _load("simulators_latest.json")
    broken = {"id": "broken", "name": "Broken", "version": "9.9", "algorithms": "not-a-list"}

    async with _client(lambda request: httpx.Response(200, json=[*entries, broken])) as client:
        service = BiosimulatorsService(client)
        with caplog.at_level(logging.WARNING):
            result = await service.get_simulators_latest()

    assert [spec.id for spec in result.simulators] == [entry["id"] for entry in entries]
    skipped = _skipped(caplog)
    assert len(skipped) == 1
    assert "broken" in skipped[0].getMessage() and "9.9" in skipped[0].getMessage()
    assert getattr(skipped[0], "upstream_outcome", None) == "entry_skipped"


@pytest.mark.asyncio
async def test_list_where_every_entry_is_invalid_maps_to_502() -> None:
    """A non-empty upstream list with zero valid entries is an invalid body, not a blank page."""
    broken = {"id": "broken", "name": "Broken", "version": "9.9", "algorithms": "not-a-list"}

    async with _client(lambda request: httpx.Response(200, json=[broken])) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as raised:
            await service.get_simulators_latest()

    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_empty_upstream_list_is_a_valid_empty_envelope() -> None:
    async with _client(lambda request: httpx.Response(200, json=[])) as client:
        service = BiosimulatorsService(client)
        result = await service.get_simulators_latest()

    assert result.simulators == []


@pytest.mark.asyncio
async def test_invalid_detail_maps_to_502_and_logs_the_outcome(caplog: pytest.LogCaptureFixture) -> None:
    async with _client(
        lambda request: httpx.Response(200, json={"id": "copasi", "name": "COPASI", "algorithms": "not-a-list"})
    ) as client:
        service = BiosimulatorsService(client)
        with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as raised:
            await service.get_simulator_version("copasi", "latest", include_tests=False)

    assert raised.value.status_code == 502
    assert any(getattr(record, "upstream_outcome", None) == "invalid_body" for record in caplog.records)


@pytest.mark.asyncio
async def test_non_list_detail_and_versions_bodies_map_to_502() -> None:
    async with _client(lambda request: httpx.Response(200, json=["unexpected"])) as client:
        service = BiosimulatorsService(client)
        with pytest.raises(HTTPException) as detail_error:
            await service.get_simulator_version("copasi", "latest", include_tests=False)
        with pytest.raises(HTTPException) as versions_error:
            await service.get_simulator_versions("copasi")

    assert detail_error.value.status_code == 502
    assert versions_error.value.status_code == 502


@pytest.mark.asyncio
async def test_cache_rejects_keys_outside_the_five_declared() -> None:
    async def loader() -> SimulatorList:
        raise AssertionError("a non-declared key must be rejected before any loader runs")

    cache = SimulatorsCache(CACHEABLE_KEYS)
    with pytest.raises(ValueError):
        await cache.get("simulators/copasi", loader)


def test_exactly_five_keys_are_declared() -> None:
    assert CACHEABLE_KEYS == frozenset(
        {
            "simulators/latest",
            "ontologies/EDAM",
            "ontologies/SBO",
            "ontologies/KISAO",
            "ontologies/SPDX",
        }
    )
