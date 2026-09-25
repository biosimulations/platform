"""Assemble platform-owned pages from an identity resource and its satellites.

The project page must read its identity first, because its satellites need the run
id embedded in the project summary. The run page's satellites need only the route's
run id, so they start alongside its identity. Both pages have a total time budget.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from biosim_server.common.upstream import (
    UPSTREAM_TIMEOUT_SECONDS,
    fetch_upstream_json,
    fetch_upstream_json_value,
    upstream_url,
)
from biosim_server.pages.mapping import map_project_page, map_run_page, parse_project, parse_run
from biosim_server.pages.models import ProjectsPagePayload, RunsPagePayload

logger = logging.getLogger(__name__)

_PROJECT_SATELLITES = ("files", "specifications")
_RUN_SATELLITES = ("files", "specifications", "logs")

# Slack added to a page budget on top of the upstream time it has to wait through:
# local validation/projection/dump work after the last response, and scheduler
# jitter between the awaits. Bounded and explicit so the budget stays a real
# ceiling rather than a number nobody can justify.
_LOCAL_WORK_SLACK_SECONDS = 10.0

# The budgets below are derived from the real serial depth of each assembler, not
# chosen independently of it. Every upstream call is bounded per phase by the shared
# client's httpx timeout (UPSTREAM_TIMEOUT_SECONDS = 30 s, and it is a *per-phase*
# bound, not a total deadline), so:
#
#   project: identity -> run id -> satellites            = 2 serial hops
#   run:     identity ∥ satellites (parallel branches)   = 1 hop
#
# The run page used to carry a flat 60 s, which was dead code: its four calls all
# run in parallel behind 30 s per-phase timeouts, so assembly could not reach 60 s
# and the budget could never fire. It is now one hop plus slack.
_PAGE_TIMEOUTS = {
    "project": 2 * UPSTREAM_TIMEOUT_SECONDS + _LOCAL_WORK_SLACK_SECONDS,
    "run": UPSTREAM_TIMEOUT_SECONDS + _LOCAL_WORK_SLACK_SECONDS,
}


@dataclass
class _PageTimings:
    """Phase durations for one page assembly, in milliseconds.

    Written by the assembler as it goes, so a page that ends in a timeout or a
    cancellation still reports the phases that did complete (0 means "never
    finished", which is exactly the information a stuck page needs).

    ``identity_ms`` is time spent awaiting identity; ``satellites_ms`` is time
    spent waiting for the satellite requests (scheduled after identity on the
    project page, concurrently with it on the run page, where the two phases may
    overlap and the sum is therefore not the wall-clock total). Neither includes
    response serialization, which happens outside the assembly budget.
    """

    identity_ms: int = 0
    satellites_ms: int = 0


def _log_page(page: str, outcome: str, status_code: int | None, started: float, timings: _PageTimings) -> None:
    """One bounded record per page request (SHARED-MIN-001).

    Phase durations, an outcome from a fixed vocabulary, and the status the caller
    received. Deliberately no resource id, no URL, no payload content -- the same
    discipline the auth path applies to claims -- so the fields stay low-cardinality
    and safe to keep.
    """
    logger.info(
        "Page assembly finished",
        extra={
            "page": page,
            "page_outcome": outcome,
            "page_status": status_code,
            "page_duration_ms": int((time.monotonic() - started) * 1000),
            "page_identity_duration_ms": timings.identity_ms,
            "page_satellites_duration_ms": timings.satellites_ms,
        },
    )


async def _satellite(client: httpx.AsyncClient, resource: str, resource_id: str, *, page: str) -> object:
    # Local path rejection is not an upstream resource-missing response.
    path = upstream_url(resource, resource_id)
    try:
        return await fetch_upstream_json_value(
            client, path, resource=resource, page=page,
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


async def _assemble_project_page_inner(
    client: httpx.AsyncClient, project_id: str, timings: _PageTimings
) -> ProjectsPagePayload:
    identity_started = time.monotonic()
    payload = await fetch_upstream_json(
        client, upstream_url("projects", project_id, "summary"),
        resource="project summary", page="project",
    )
    timings.identity_ms = int((time.monotonic() - identity_started) * 1000)
    try:
        project = parse_project(payload)
    except ValidationError as exc:
        logger.warning("Invalid upstream project page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected project page.") from exc
    # Identity first is a hard dependency, not an ordering preference: the
    # satellite paths embed the run id from this response. Once it is parsed the
    # two satellites run concurrently.
    satellites = [
        asyncio.create_task(_satellite(client, resource, project.simulation_run.id, page="project"))
        for resource in _PROJECT_SATELLITES
    ]
    satellites_started = time.monotonic()
    try:
        files, specifications = await asyncio.gather(*satellites)
        return map_project_page(project, files, specifications)
    except ValidationError as exc:
        logger.warning("Invalid upstream project page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected project page.") from exc
    finally:
        timings.satellites_ms = int((time.monotonic() - satellites_started) * 1000)
        # Anything that ends assembly early -- a failed or drifted satellite, or
        # the page budget cancelling this coroutine -- must not leave the sibling
        # upstream request running after the response is decided. On success every
        # task has already finished.
        await _cancel_and_drain(satellites)


async def assemble_project_page(client: httpx.AsyncClient, project_id: str) -> ProjectsPagePayload:
    started = time.monotonic()
    timings = _PageTimings()
    try:
        page = await asyncio.wait_for(
            _assemble_project_page_inner(client, project_id, timings),
            timeout=_PAGE_TIMEOUTS["project"],
        )
    except asyncio.TimeoutError:
        _log_page("project", "timeout", 504, started, timings)
        raise HTTPException(504, "Timed out while loading the project page.")
    except asyncio.CancelledError:
        # The caller went away. Log it, then keep propagating: swallowing a
        # cancellation here would leave the request task unable to shut down.
        _log_page("project", "cancelled", None, started, timings)
        raise
    except HTTPException as exc:
        _log_page("project", "error", exc.status_code, started, timings)
        raise
    _log_page("project", "ok", 200, started, timings)
    return page


async def _assemble_run_page_inner(
    client: httpx.AsyncClient, run_id: str, timings: _PageTimings
) -> RunsPagePayload:
    # Satellites need only the route's run_id, never a field of the identity response,
    # so they start alongside it: the critical path is max(identity, satellites)
    # rather than identity + max(satellites).
    identity_started = time.monotonic()
    identity = asyncio.create_task(fetch_upstream_json(
        client, upstream_url("runs", run_id, "summary"), resource="run summary", page="run",
    ))
    satellites = [
        asyncio.create_task(_satellite(client, resource, run_id, page="run"))
        for resource in _RUN_SATELLITES
    ]
    satellites_started = time.monotonic()
    try:
        run = parse_run(await identity)
        timings.identity_ms = int((time.monotonic() - identity_started) * 1000)
        try:
            files, specifications, logs = await asyncio.gather(*satellites)
            return map_run_page(run, files, specifications, logs)
        except ValidationError as exc:
            logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
            raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc
        finally:
            timings.satellites_ms = int((time.monotonic() - satellites_started) * 1000)
    except ValidationError as exc:
        logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc
    finally:
        # Anything that ends assembly early -- a failed or drifted identity, a failed
        # satellite, or the page budget cancelling this coroutine -- must not leave
        # upstream requests running. On success every task has already finished.
        await _cancel_and_drain([identity, *satellites])


async def assemble_run_page(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    started = time.monotonic()
    timings = _PageTimings()
    try:
        page = await asyncio.wait_for(
            _assemble_run_page_inner(client, run_id, timings),
            timeout=_PAGE_TIMEOUTS["run"],
        )
    except asyncio.TimeoutError:
        _log_page("run", "timeout", 504, started, timings)
        raise HTTPException(504, "Timed out while loading the run page.")
    except asyncio.CancelledError:
        _log_page("run", "cancelled", None, started, timings)
        raise
    except HTTPException as exc:
        _log_page("run", "error", exc.status_code, started, timings)
        raise
    _log_page("run", "ok", 200, started, timings)
    return page
