"""Assemble platform-owned pages from an identity resource and its satellites.

The project page must read its identity first, because its satellites need the run
id embedded in the project summary. The run page's satellites need only the route's
run id, so they start alongside its identity. Both pages have a total time budget.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from biosim_server.common.upstream import fetch_upstream_json, fetch_upstream_json_value, upstream_url
from biosim_server.pages.mapping import map_project_page, map_run_page, parse_project, parse_run
from biosim_server.pages.models import ProjectsPagePayload, RunsPagePayload

logger = logging.getLogger(__name__)

_PAGE_TIMEOUTS = {
    "project": 45.0,
    "run": 60.0,
}
_RUN_SATELLITES = ("files", "specifications", "logs")


async def _satellite(client: httpx.AsyncClient, resource: str, resource_id: str) -> object:
    # Local path rejection is not an upstream resource-missing response.
    path = upstream_url(resource, resource_id)
    try:
        return await fetch_upstream_json_value(
            client, path, resource=resource,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None if resource == "logs" else []


async def _cancel_and_drain(tasks: Sequence[asyncio.Task[Any]]) -> None:
    """Cancel whatever is still running and wait for it, retrieving every outcome."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _assemble_project_page_inner(client: httpx.AsyncClient, project_id: str) -> ProjectsPagePayload:
    payload = await fetch_upstream_json(
        client, upstream_url("projects", project_id, "summary"), resource="project summary",
    )
    try:
        project = parse_project(payload)
        files, specifications = await asyncio.gather(
            _satellite(client, "files", project.simulation_run.id),
            _satellite(client, "specifications", project.simulation_run.id),
        )
        return map_project_page(project, files, specifications)
    except ValidationError as exc:
        logger.warning("Invalid upstream project page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected project page.") from exc


async def assemble_project_page(client: httpx.AsyncClient, project_id: str) -> ProjectsPagePayload:
    try:
        return await asyncio.wait_for(
            _assemble_project_page_inner(client, project_id),
            timeout=_PAGE_TIMEOUTS["project"],
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timed out while loading the project page.")


async def _assemble_run_page_inner(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    # Satellites need only the route's run_id, never a field of the identity response,
    # so they start alongside it: the critical path is max(identity, satellites)
    # rather than identity + max(satellites).
    identity = asyncio.create_task(fetch_upstream_json(
        client, upstream_url("runs", run_id, "summary"), resource="run summary",
    ))
    satellites = [asyncio.create_task(_satellite(client, resource, run_id)) for resource in _RUN_SATELLITES]
    try:
        run = parse_run(await identity)
        files, specifications, logs = await asyncio.gather(*satellites)
        return map_run_page(run, files, specifications, logs)
    except ValidationError as exc:
        logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc
    finally:
        # Anything that ends assembly early -- a failed or drifted identity, a failed
        # satellite, or the page budget cancelling this coroutine -- must not leave
        # upstream requests running. On success every task has already finished.
        await _cancel_and_drain([identity, *satellites])


async def assemble_run_page(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    try:
        return await asyncio.wait_for(
            _assemble_run_page_inner(client, run_id),
            timeout=_PAGE_TIMEOUTS["run"],
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timed out while loading the run page.")
