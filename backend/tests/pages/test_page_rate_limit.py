"""The two page routes are metered (an addition to the audit's own plan).

`GET /projects/{id}/page` and `GET /runs/{id}/page` are public and each fans out
to 3-4 GETs against a third-party API this project does not own, so an unmetered
caller could amplify load onto biosimulations.org at no cost to themselves.
These tests pin the mounted behavior: one shared per-IP bucket, a 429 with
Retry-After once it is spent, no upstream traffic for a denied request, and no
way to buy a larger budget with a bearer token.

The bucket's own ceiling/window semantics live in tests/common/test_ratelimit.py;
this file proves the dependency is actually wired onto both routes.
"""

from collections.abc import Iterator

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.common import ratelimit as ratelimit_module
from biosim_server.config import get_settings
from biosim_server.dependencies import get_http_client
from tests.fixtures.jwks_fixtures import FakeClock
from tests.pages.test_mapping import satellite
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
PROJECT_PAGE = "/projects/example/page"
RUN_PAGE = "/runs/example/page"
BOTH_PAGES = [PROJECT_PAGE, RUN_PAGE]


@pytest.fixture(autouse=True)
def _isolate_page_budget() -> Iterator[None]:
    """Start every test with an empty bucket set and restore the policy after."""
    ratelimit_module._reset_rate_limit_state()
    settings = get_settings().ratelimit
    original = (settings.enabled, settings.page_per_window, settings.page_window_seconds)
    yield
    (settings.enabled, settings.page_per_window, settings.page_window_seconds) = original
    ratelimit_module._reset_rate_limit_state()


def _upstream(both_pages: list[str]) -> tuple[httpx.AsyncClient, list[str]]:
    """An upstream that answers both page shapes and records every request path."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload("project" if request.url.path.startswith("/projects/") else "run"))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test"), seen


@pytest.mark.parametrize("page", BOTH_PAGES)
async def test_page_route_is_metered_and_denies_with_retry_after(
    page: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().ratelimit
    settings.page_per_window = 2
    settings.page_window_seconds = 60
    clock = FakeClock(start=1_700_000_100.0)
    monkeypatch.setattr(ratelimit_module, "time", clock)
    upstream, seen = _upstream(BOTH_PAGES)

    async with upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            assert (await caller.get(page)).status_code == 200
            assert (await caller.get(page)).status_code == 200
            calls_before_denial = len(seen)
            clock.advance(20)

            denied = await caller.get(page, headers={"Authorization": "Bearer not-a-ticket-out"})

    assert denied.status_code == 429, denied.text
    assert denied.headers["retry-after"] == "41"
    # A denied page must not touch upstream: the point of the bucket is to bound
    # the fan-out, and rejecting after paying for it would not.
    assert len(seen) == calls_before_denial


async def test_both_page_routes_share_one_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-robining between the two routes must not recover throughput."""
    settings = get_settings().ratelimit
    settings.page_per_window = 2
    settings.page_window_seconds = 60
    monkeypatch.setattr(ratelimit_module, "time", FakeClock(start=1_700_000_100.0))
    upstream, _ = _upstream(BOTH_PAGES)

    async with upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            assert (await caller.get(PROJECT_PAGE)).status_code == 200
            assert (await caller.get(RUN_PAGE)).status_code == 200
            assert (await caller.get(PROJECT_PAGE)).status_code == 429
            assert (await caller.get(RUN_PAGE)).status_code == 429


async def test_page_metering_honours_the_kill_switch() -> None:
    settings = get_settings().ratelimit
    settings.enabled = False
    settings.page_per_window = 1
    upstream, seen = _upstream(BOTH_PAGES)

    async with upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
            for _ in range(4):
                assert (await caller.get(PROJECT_PAGE)).status_code == 200
    assert len(seen) == 4 * 3
