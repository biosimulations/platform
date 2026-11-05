"""The owned simulators router: envelopes, statuses, caching headers, rate limit (plan §4.5, §4.6)."""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.simulators.models import SimulatorList, SimulatorSpec, SimulatorVersionList
from biosim_server.simulators.router import get_simulators_service
from biosim_server.simulators.service import BiosimulatorsService

FIXTURES = Path(__file__).parent / "fixtures"
BASE_UPSTREAM = "https://biosimulators.test"

ClientHolder = Callable[[], AbstractAsyncContextManager[AsyncClient]]


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Mount a MockTransport-backed simulators service and hand the test its request record."""
    record: dict[str, Any] = {"seen": [], "status_by_path": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        record["seen"].append(str(request.url))
        status = record["status_by_path"].get(path)
        if status is not None:
            return httpx.Response(status, json={"detail": "upstream failure"})
        if path.startswith("/ontologies/"):
            return httpx.Response(200, json=_load("ontology_terms.json")[path.rsplit("/", 1)[-1]])
        if path.count("/") == 2:  # /simulators/{id}: the versions array
            return httpx.Response(200, json=_load("simulators_latest.json"))
        return httpx.Response(200, json=_load("simulator_detail.json"))

    service = BiosimulatorsService(
        httpx.AsyncClient(base_url=BASE_UPSTREAM, transport=httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_simulators_service] = lambda: service
    yield record
    app.dependency_overrides.pop(get_simulators_service, None)


def _mount_handler(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    service = BiosimulatorsService(
        httpx.AsyncClient(base_url=BASE_UPSTREAM, transport=httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_simulators_service] = lambda: service


@pytest.fixture
def client_holder() -> ClientHolder:
    @asynccontextmanager
    async def holder() -> AsyncIterator[AsyncClient]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    return holder


@pytest.mark.asyncio
async def test_latest_serves_the_owned_envelope_with_caching(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    async with client_holder() as client:
        response = await client.get("/simulators/latest")

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "public, max-age=300"
    body = response.json()
    assert set(body) == {"simulators"}
    assert {simulator["id"] for simulator in body["simulators"]} == {"copasi", "tellurium"}
    assert set(body["simulators"][0]) >= {"biosimulators", "supportedProgrammingLanguages", "pythonApi"}
    assert SimulatorList.model_validate(body).simulators[0].algorithms is not None


@pytest.mark.asyncio
async def test_versions_envelope_uses_the_camel_case_id_and_no_cache_header(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    async with client_holder() as client:
        response = await client.get("/simulators/copasi")

    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") is None
    body = response.json()
    assert body["simulatorId"] == "copasi"
    assert SimulatorVersionList.model_validate(body).versions


@pytest.mark.asyncio
async def test_detail_forwards_include_tests_and_sets_no_cache_header(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    async with client_holder() as client:
        response = await client.get("/simulators/copasi/4.42.284", params={"include_tests": "true"})
        await client.get("/simulators/copasi/4.42.284")

    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") is None
    assert registry["seen"] == [
        f"{BASE_UPSTREAM}/simulators/copasi/4.42.284?includeTests=true",
        f"{BASE_UPSTREAM}/simulators/copasi/4.42.284",
    ]
    spec = SimulatorSpec.model_validate(response.json())
    assert spec.biosimulators is not None
    assert spec.biosimulators.validation_tests is not None


@pytest.mark.asyncio
async def test_ontology_serves_the_trimmed_terms_with_caching(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    async with client_holder() as client:
        response = await client.get("/ontologies/EDAM")

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "public, max-age=300"
    body = response.json()
    assert body["namespace"] == "EDAM"
    assert all(set(term) == {"id", "name"} for term in body["terms"])


@pytest.mark.asyncio
async def test_unknown_simulator_maps_to_404(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    registry["status_by_path"]["/simulators/unknown"] = 404
    async with client_holder() as client:
        response = await client.get("/simulators/unknown")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_ontology_namespace_is_422(
    client_holder: ClientHolder
) -> None:
    async with client_holder() as client:
        response = await client.get("/ontologies/NOPE")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dot_segments_are_rejected_before_any_upstream_url(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    """%2E%2E survives httpx URL normalization and reaches the route as a dot segment."""
    async with client_holder() as client:
        first = await client.get("/simulators/%2E%2E")
        second = await client.get("/simulators/copasi/%2E%2E")

    assert first.status_code == 404
    assert second.status_code == 404
    assert registry["seen"] == []  # no upstream URL was ever built


@pytest.mark.asyncio
@pytest.mark.parametrize(("upstream_status", "expected_status"), [(500, 502), (503, 502)])
async def test_upstream_5xx_maps_to_502(
    registry: dict[str, Any],
    client_holder: ClientHolder,
    upstream_status: int,
    expected_status: int,
) -> None:
    registry["status_by_path"]["/simulators/latest"] = upstream_status
    async with client_holder() as client:
        response = await client.get("/simulators/latest")

    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_upstream_timeout_maps_to_504(
    client_holder: ClientHolder
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream")

    _mount_handler(handler)
    async with client_holder() as client:
        response = await client.get("/simulators/latest")

    assert response.status_code == 504


@pytest.mark.asyncio
async def test_invalid_body_upstream_is_a_sanitized_502(
    client_holder: ClientHolder
) -> None:
    _mount_handler(lambda request: httpx.Response(200, json={"id": "copasi", "algorithms": "not-a-list"}))
    async with client_holder() as client:
        response = await client.get("/simulators/copasi/4.42.284")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_oversize_body_maps_to_502(
    client_holder: ClientHolder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "biosim_server.common.upstream.get_settings",
        lambda: SimpleNamespace(upstream_max_response_bytes=64),
    )
    _mount_handler(lambda request: httpx.Response(200, json=_load("simulators_latest.json")))
    async with client_holder() as client:
        response = await client.get("/simulators/latest")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_simulators_budget_returns_429_with_retry_after(
    client_holder: ClientHolder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "biosim_server.common.ratelimit.get_settings",
        lambda: SimpleNamespace(
            ratelimit=SimpleNamespace(
                enabled=True, simulators_per_window=2, simulators_window_seconds=60
            )
        ),
    )
    _mount_handler(lambda request: httpx.Response(200, json=[]))
    async with client_holder() as client:
        assert (await client.get("/simulators/latest")).status_code == 200
        assert (await client.get("/simulators/latest")).status_code == 200
        exhausted = await client.get("/simulators/latest")

    assert exhausted.status_code == 429
    assert exhausted.headers["Retry-After"]


@pytest.mark.asyncio
async def test_an_invalid_bearer_token_still_gets_200(
    registry: dict[str, Any], client_holder: ClientHolder
) -> None:
    headers = {"Authorization": "Bearer definitely-invalid"}
    async with client_holder() as client:
        latest = await client.get("/simulators/latest", headers=headers)
        versions = await client.get("/simulators/copasi", headers=headers)
        detail = await client.get("/simulators/copasi/4.42.284", headers=headers)
        ontology = await client.get("/ontologies/EDAM", headers=headers)

    assert latest.status_code == versions.status_code == detail.status_code == ontology.status_code == 200
