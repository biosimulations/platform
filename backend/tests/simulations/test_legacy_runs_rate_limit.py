"""Mounted legacy routes share an IP budget before any proxy work."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio

from biosim_server.api.main import app
from biosim_server.biosim_runs import legacy_api
from biosim_server.common import ratelimit
from biosim_server.config import get_settings
from biosim_server.dependencies import get_http_client, get_legacy_http_client
from tests.fixtures.jwks_fixtures import FakeClock
from tests.pages.test_mapping import satellite
from tests.summaries.test_mapping import payload
from tests.simulations.test_legacy_runs_proxy import Chunks

pytestmark = pytest.mark.asyncio
ROUTES = [
    ("GET", "/runs/summary"),
    ("GET", "/runs/example"),
    ("PATCH", "/runs/example"),
    ("DELETE", "/runs/example"),
    ("GET", "/runs/example/download"),
    ("GET", "/runs/example/validate"),
]


@pytest_asyncio.fixture
async def proxy(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[httpx.AsyncClient, list[httpx.Request], FakeClock]]:
    settings = get_settings().ratelimit
    monkeypatch.setattr(settings, "enabled", True)
    monkeypatch.setattr(settings, "legacy_per_window", 2)
    monkeypatch.setattr(settings, "legacy_window_seconds", 60)
    monkeypatch.setattr(settings, "page_per_window", 1)
    clock = FakeClock(start=1_700_000_100.0)
    monkeypatch.setattr(ratelimit, "time", clock)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("throttle"):
            return httpx.Response(429, content=b"upstream busy", headers={"Retry-After": "120"})
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("run"))
        if request.url.path.startswith(("/files/", "/specifications/", "/logs/")):
            return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))
        return httpx.Response(200, stream=Chunks([b"ok"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        monkeypatch.setitem(app.dependency_overrides, get_legacy_http_client, lambda: upstream)
        monkeypatch.setitem(app.dependency_overrides, get_http_client, lambda: upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            yield caller, seen, clock


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_each_route_denies_before_upstream(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock], method: str, path: str,
) -> None:
    caller, seen, clock = proxy
    for _ in range(2):
        assert (await caller.request(method, path)).status_code == 200
    clock.advance(20)
    denied = await caller.request(method, path)
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "41"
    assert len(seen) == 2


async def test_routes_ids_queries_and_tokens_share_one_budget(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller, seen, _ = proxy
    monkeypatch.setattr(get_settings().ratelimit, "legacy_per_window", len(ROUTES))
    for index, (method, path) in enumerate(ROUTES):
        headers = {"Authorization": f"Bearer malformed-{index}"} if index else {}
        response = await caller.request(method, path.replace("example", f"run-{index}"), params={"label": str(index)}, headers=headers)
        assert response.status_code == 200
        assert seen[-1].headers.get("authorization") == headers.get("Authorization")
    for method, path in ROUTES:
        assert (await caller.request(method, path, headers={"Authorization": "another-token"})).status_code == 429
    assert len(seen) == len(ROUTES)


async def test_denial_precedes_patch_body_and_download_slot(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller, seen, _ = proxy
    for _ in range(2):
        assert (await caller.get("/runs/example")).status_code == 200
    body_reader = AsyncMock(side_effect=AssertionError("blocked PATCH read its body"))
    claim_slot = Mock(side_effect=AssertionError("blocked download claimed a slot"))
    monkeypatch.setattr(legacy_api, "_patch_body", body_reader)
    monkeypatch.setattr(legacy_api, "_claim_download_slot", claim_slot)
    assert (await caller.patch("/runs/example", content=b"body")).status_code == 429
    assert (await caller.get("/runs/example/download")).status_code == 429
    body_reader.assert_not_called()
    claim_slot.assert_not_called()
    assert len(seen) == 2


async def test_upstream_429_is_relayed_and_still_charged(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock],
) -> None:
    caller, seen, _ = proxy
    for _ in range(2):
        response = await caller.get("/runs/example?throttle=1")
        assert response.status_code == 429
        assert response.content == b"upstream busy"
        assert response.headers["Retry-After"] == "120"
    response = await caller.get("/runs/example")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "61"
    assert len(seen) == 2


async def test_kill_switch(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller, seen, _ = proxy
    monkeypatch.setattr(get_settings().ratelimit, "enabled", False)
    for method, path in ROUTES:
        assert (await caller.request(method, path)).status_code == 200
    assert len(seen) == len(ROUTES)


@pytest.mark.parametrize("legacy_first", [True, False])
async def test_owned_routes_have_independent_allowances(
    proxy: tuple[httpx.AsyncClient, list[httpx.Request], FakeClock], legacy_first: bool,
) -> None:
    caller, _, _ = proxy

    async def exhaust_legacy() -> None:
        for _ in range(2):
            assert (await caller.get("/runs/example")).status_code == 200
        assert (await caller.get("/runs/example")).status_code == 429

    if legacy_first:
        await exhaust_legacy()
    for path in ("/runs/example/page", "/projects/example/page"):
        # Both pages share a budget; consume it with the run page.
        expected = 200 if path.startswith("/runs/") else 429
        assert (await caller.get(path)).status_code == expected
    assert (await caller.get("/runs/example/page")).status_code == 429
    assert (await caller.get("/runs/example/summary")).status_code == 200
    if not legacy_first:
        await exhaust_legacy()
