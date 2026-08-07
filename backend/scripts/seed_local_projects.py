#!/usr/bin/env python3
"""Seed local dev Mongo with real project data from the public biosimulations.org API.

`GET /projects` reads a live join of the platform's `Projects` + `Metadata` +
`Specifications` (+ `Simulation Runs`) collections (see
`biosim_server/projects/database.py`), normally pointed at the hosted
`biosimulations-prod` Mongo. Without hosted read-only credentials, those
collections are empty locally and every query returns nothing.

This script is the credential-free alternative for local dev: it pulls real,
already-published project summaries from the legacy public endpoint
(`https://api.biosimulations.org/projects/summary_filtered`, no auth required)
and reshapes them into documents matching the same collections/schema the
platform backend expects, written to your local `MONGODB_URI`.

Usage:
    uv run python scripts/seed_local_projects.py [--count N] [--page-size N]

After seeding, (re)start the API -- `rebuild_index_if_empty` runs at startup
and will build the served `PlatformProjectSearch` collection from what this
writes, so `GET /projects` / `GET /projects/stats` return real data.

Refuses to run against a non-local MONGODB_URI (it deletes existing rows in
the four collections before reseeding) unless --force is passed.
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient

from biosim_server.config import get_settings
from biosim_server.log_config import setup_logging

logger = logging.getLogger(__name__)

LEGACY_API_BASE = "https://api.biosimulations.org"
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _transform(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Reshape one legacy `summary_filtered` item into the four local docs it maps to."""
    sr = summary.get("simulationRun") or {}
    run_id = sr.get("id")
    if not run_id:
        return None

    project_doc = {
        "id": summary.get("id"),
        "simulationRun": run_id,
        "owner": summary.get("owner"),
        "created": _parse_iso(summary.get("created")) or datetime.now(timezone.utc),
        "updated": _parse_iso(summary.get("updated")) or datetime.now(timezone.utc),
    }

    metadata_doc = {
        "simulationRun": run_id,
        "metadata": sr.get("metadata") or [],
    }

    models: list[dict[str, Any]] = []
    simulations: list[dict[str, Any]] = []
    for task in sr.get("tasks") or []:
        model = task.get("model") or {}
        language = (model.get("language") or {}).get("sedmlUrn")
        if language:
            models.append({"language": language})
        simulation = task.get("simulation") or {}
        simulations.append({
            "_type": (simulation.get("type") or {}).get("id", ""),
            "algorithm": {"kisaoId": (simulation.get("algorithm") or {}).get("kisaoId", "")},
        })
    outputs = [{"_type": (o.get("type") or {}).get("id", "")} for o in (sr.get("outputs") or [])]

    specifications_doc = {
        "simulationRun": run_id,
        "models": models,
        "simulations": simulations,
        "outputs": outputs,
    }

    simulator = (sr.get("run") or {}).get("simulator") or {}
    run_doc = {
        "id": run_id,
        "simulator": simulator.get("name") or simulator.get("id") or "",
    }

    return project_doc, metadata_doc, specifications_doc, run_doc


async def _fetch_summaries(session: aiohttp.ClientSession, count: int, page_size: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    page = 0
    while len(summaries) < count:
        params = {"pageIndex": page, "pageSize": min(page_size, count - len(summaries))}
        async with session.get(f"{LEGACY_API_BASE}/projects/summary_filtered", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        batch = data.get("projectSummaries") or []
        if not batch:
            break
        summaries.extend(batch)
        logger.info(f"fetched page {page}: {len(batch)} summaries ({len(summaries)}/{count} so far)")
        page += 1
        await asyncio.sleep(0.2)  # be polite to a service we don't own
    return summaries[:count]


async def _seed(count: int, page_size: int, force: bool) -> None:
    settings = get_settings()
    if not force and not any(host in settings.mongodb_uri for host in _LOCAL_HOSTS):
        raise SystemExit(
            f"Refusing to run: MONGODB_URI does not look local ({settings.mongodb_uri!r}). "
            "This script deletes existing rows in the Projects/Metadata/Specifications/"
            "Simulation Runs collections before reseeding. Pass --force if you're sure."
        )

    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client.get_database(settings.mongodb_database)
    projects_col = db.get_collection(settings.mongodb_collection_projects)
    metadata_col = db.get_collection(settings.mongodb_collection_metadata)
    specifications_col = db.get_collection(settings.mongodb_collection_specifications)
    runs_col = db.get_collection(settings.mongodb_collection_biosimulations_runs)
    search_col = db.get_collection(settings.mongodb_collection_project_search)

    async with aiohttp.ClientSession() as session:
        summaries = await _fetch_summaries(session, count, page_size)

    project_docs, metadata_docs, specifications_docs, run_docs = [], [], [], []
    for summary in summaries:
        transformed = _transform(summary)
        if transformed is None:
            continue
        project_doc, metadata_doc, specifications_doc, run_doc = transformed
        project_docs.append(project_doc)
        metadata_docs.append(metadata_doc)
        specifications_docs.append(specifications_doc)
        run_docs.append(run_doc)

    logger.info(f"seeding {len(project_docs)} projects into database {settings.mongodb_database!r}")
    for col in (projects_col, metadata_col, specifications_col, runs_col, search_col):
        await col.delete_many({})
    if project_docs:
        await projects_col.insert_many(project_docs)
        await metadata_col.insert_many(metadata_docs)
        await specifications_col.insert_many(specifications_docs)
        await runs_col.insert_many(run_docs)

    client.close()
    logger.info(
        f"seed complete: {len(project_docs)} projects written. "
        "Restart the API to rebuild the search index (rebuild_index_if_empty runs at startup)."
    )


def main() -> None:
    setup_logging(logger)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=300, help="Number of projects to import (default 300, max 1392).")
    parser.add_argument("--page-size", type=int, default=100, help="Page size for the legacy API (default 100).")
    parser.add_argument("--force", action="store_true", help="Allow seeding a non-local MONGODB_URI.")
    args = parser.parse_args()
    asyncio.run(_seed(args.count, args.page_size, args.force))


if __name__ == "__main__":
    main()
