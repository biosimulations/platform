"""Fetch identity once, then fetch independent satellite resources concurrently."""

import asyncio
import logging

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from biosim_server.common.upstream import fetch_upstream_json, fetch_upstream_json_value, upstream_url
from biosim_server.pages.mapping import map_project_page, map_run_page, parse_project, parse_run
from biosim_server.pages.models import ProjectsPagePayload, RunsPagePayload

logger = logging.getLogger(__name__)


async def _satellite(client: httpx.AsyncClient, resource: str, run_id: str) -> object:
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


async def assemble_project_page(client: httpx.AsyncClient, project_id: str) -> ProjectsPagePayload:
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


async def assemble_run_page(client: httpx.AsyncClient, run_id: str) -> RunsPagePayload:
    payload = await fetch_upstream_json(
        client, upstream_url("runs", run_id, "summary"), resource="run summary",
    )
    try:
        run = parse_run(payload)
        files, specifications, logs = await asyncio.gather(
            _satellite(client, "files", run_id),
            _satellite(client, "specifications", run_id),
            _satellite(client, "logs", run_id),
        )
        return map_run_page(run, files, specifications, logs)
    except ValidationError as exc:
        logger.warning("Invalid upstream run page: %s", exc.errors(include_input=False))
        raise HTTPException(502, "The upstream service returned an unexpected run page.") from exc
