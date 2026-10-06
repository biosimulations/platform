"""Phase 0 baseline: the page assemblers as they were before the concurrency change.

The replicas below are verbatim copies of backend/biosim_server/pages/service.py at
23f406b, renamed with an ``_old`` prefix. Production has since changed, so these
tests pin down what the change was measured against: both pages issued their
satellites only after identity answered, costing two upstream round trips.
scripts/bench_pages.py measures the difference against a live baseline server.
"""

import asyncio
import logging
import time

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from biosim_server.common.upstream import fetch_upstream_json, fetch_upstream_json_value, upstream_url
from biosim_server.pages.mapping import map_project_page, map_run_page, parse_project, parse_run
from biosim_server.pages.models import ProjectsPagePayload, RunsPagePayload
from tests.pages.test_mapping import satellite
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
logger = logging.getLogger(__name__)


# --- Verbatim replicas of service.py at 23f406b (only the names are changed) ---

async def _old_satellite(client: httpx.AsyncClient, resource: str, run_id: str) -> object:
    # Local path rejection is not an upstream resource-missing response.
    path = upstream_url(resource, run_id)
    try:
        return await fetch_upstream_json_value(
            client, path, resource=resource,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None if resource == "logs" else []


async def _old_assemble_project_page(client: httpx.AsyncClient, project_id: str) -> ProjectsPagePayload:
    payload = await fetch_upstream_json(
        client, upstream_url("projects", project_id, "summary"), resource="project summary",
    )
    try:
        project = parse_project(payload)
        files, specifications = await asyncio.gather(
            _old_satellite(client, "files", project.simulation_run.id),
            _old_satellite(client, "specifications", project.simulation_run.id),
        )
        return map_project_page(project, files, specifications)
    except ValidationError as exc:
        logger.warning("Invalid upstream project page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected project page.") from exc


async def _old_assemble_run_page(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    payload = await fetch_upstream_json(
        client, upstream_url("runs", run_id, "summary"), resource="run summary",
    )
    try:
        run = parse_run(payload)
        files, specifications, logs = await asyncio.gather(
            _old_satellite(client, "files", run_id),
            _old_satellite(client, "specifications", run_id),
            _old_satellite(client, "logs", run_id),
        )
        return map_run_page(run, files, specifications, logs)
    except ValidationError as exc:
        logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc


# --- Baseline tests ---

async def test_phase0_run_page_satellites_wait_for_identity() -> None:
    """Before the change, no run-page satellite reached upstream while identity was unanswered.

    Contrast test_run_page_satellites_start_before_identity_completes, where the
    current implementation issues all three satellites before identity answers.
    """
    seen: list[str] = []
    seen_while_identity_pending: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/summary"):
            # Give any satellite started alongside identity every chance to arrive.
            for _ in range(10):
                await asyncio.sleep(0)
            seen_while_identity_pending.extend(seen[1:])
            return httpx.Response(200, json=payload("run"))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        await _old_assemble_run_page(upstream, "example")
    assert seen_while_identity_pending == []
    assert seen == ["/runs/example/summary", "/files/example", "/specifications/example", "/logs/example"]


async def test_phase0_project_page_satellites_need_identity_run_id() -> None:
    """The project page's identity barrier is necessary: satellites use the run id it embeds."""
    run_id = payload("project")["simulationRun"]["id"]
    seen: list[str] = []
    seen_while_identity_pending: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/summary"):
            for _ in range(10):
                await asyncio.sleep(0)
            seen_while_identity_pending.extend(seen[1:])
            return httpx.Response(200, json=payload("project"))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        await _old_assemble_project_page(upstream, "example")
    assert run_id != "example"
    assert seen_while_identity_pending == []
    assert seen == ["/projects/example/summary", f"/files/{run_id}", f"/specifications/{run_id}"]


@pytest.mark.parametrize("page", ["run", "project"])
async def test_phase0_latency_baseline(page: str) -> None:
    """Before the change, each page cost two upstream round trips.

    Every call takes ``delay``, so serial assembly cannot finish in under
    ``2 * delay`` (asyncio.sleep never returns early). Only that lower bound is
    asserted: an upper bound would test the machine, not the code.
    """
    delay = 0.1

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=payload(page))
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
        start = time.monotonic()
        if page == "run":
            await _old_assemble_run_page(upstream, "example")
        else:
            await _old_assemble_project_page(upstream, "example")
        elapsed = time.monotonic() - start
    assert elapsed >= 2 * delay, f"serial {page} page took {elapsed * 1000:.0f}ms with {delay * 1000:.0f}ms upstream calls"
