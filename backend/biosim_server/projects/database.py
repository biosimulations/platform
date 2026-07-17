"""Async persistence for the BioSim DB project search API.

Assembles project summaries live from the authoritative ``Projects`` collection
joined to per-run ``Metadata`` (keyed by ``simulationRun``) in
``biosimulations-prod`` — the same live-assembly the biosimulations API does,
covering all published projects and always current.

NOT used: the ``projectSummary`` collection is a dead pre-2022 materialization
(subset, frozen, nothing writes it). See ``docs/project-search-api-plan.md``.

Facet stats (a full scan of the matched set) are cached in a platform-owned,
in-process TTL cache so paging stays cheap. All DB access lives behind the
``ProjectDatabaseService`` ABC. Pagination is 1-indexed and computed in Mongo
(``$skip``/``$limit`` + a real count via ``$facet``), fixing the legacy
``summary_filtered`` bug where totals and page windows disagreed past page 1.
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from typing_extensions import override

from biosim_server.config import get_settings
from biosim_server.projects.models import (
    ProjectQueryStat,
    ProjectSearchFilter,
    ProjectStub,
    ValueFrequency,
)

logger = logging.getLogger(__name__)

# Facet target -> field name inside a metadata item. Each resolver pulls the set
# of string values a project carries for that target; used for both filtering and
# facet counts. Kept small on purpose for the first cut — model format / simulator
# need a Specifications join and land later.
_FACET_TARGETS: dict[str, str] = {
    "taxa": "taxa",
    "keywords": "keywords",
}

# metadata fields searched by `searchTerm` (case-insensitive substring).
_SEARCH_FIELDS = ("title", "abstract", "description")

# The first metadata item of the joined Metadata doc is surfaced as `_meta0` by
# the aggregation; all match/project expressions reference that. The joined
# Specifications models (for model_format) are surfaced as `_models`.
_META = "_meta0"
_MODELS = "_models"


def _iso(value: Any) -> str:
    """ISO-8601 with trailing Z for UTC, matching the frontend date format."""
    if hasattr(value, "isoformat"):
        return str(value.isoformat()).replace("+00:00", "Z")
    return str(value or "")


def _label_values(raw: Any) -> list[str]:
    """Normalize a metadata field to a flat list of string values.

    Handles both plain strings and ``{uri, label}`` objects (taxa, keywords,
    encodes, ... are stored either way across records)."""
    values: list[str] = []
    if not isinstance(raw, list):
        return values
    for item in raw:
        if isinstance(item, str):
            if item:
                values.append(item)
        elif isinstance(item, dict):
            label = item.get("label")
            if isinstance(label, str) and label:
                values.append(label)
    return values


def _search_clause(search_term: str) -> dict[str, Any]:
    """Case-insensitive substring match across the searched metadata fields."""
    pattern = {"$regex": re.escape(search_term), "$options": "i"}
    return {"$or": [{f"{_META}.{field}": pattern} for field in _SEARCH_FIELDS]}


def _filter_clauses(filters: list[ProjectSearchFilter]) -> list[dict[str, Any]]:
    """Translate active filters into Mongo clauses (AND across targets,
    OR within a target's allowable_values). Skips unknown/empty targets."""
    clauses: list[dict[str, Any]] = []
    for f in filters:
        field = _FACET_TARGETS.get(f.target)
        if field is None or not f.allowable_values:
            continue
        # Value may be stored as a bare string or as {label}; match either.
        clauses.append(
            {
                "$or": [
                    {f"{_META}.{field}": {"$in": f.allowable_values}},
                    {f"{_META}.{field}.label": {"$in": f.allowable_values}},
                ]
            }
        )
    return clauses


def build_match_stage(
    filters: list[ProjectSearchFilter], search_term: str
) -> dict[str, Any] | None:
    """Assemble the ``$match`` (over joined metadata) for filters + free text.

    Returns ``None`` when there is nothing to match (no filters, no search)."""
    and_clauses: list[dict[str, Any]] = _filter_clauses(filters)
    if search_term:
        and_clauses.append(_search_clause(search_term))
    if not and_clauses:
        return None
    if len(and_clauses) == 1:
        return and_clauses[0]
    return {"$and": and_clauses}


def _join_stages(metadata_collection: str) -> list[dict[str, Any]]:
    """Join each Project to the first metadata item of its run's Metadata doc,
    surfaced as ``_meta0`` (``{}`` when the run has no Metadata)."""
    return [
        {
            "$lookup": {
                "from": metadata_collection,
                "localField": "simulationRun",
                "foreignField": "simulationRun",
                "as": "_md",
            }
        },
        {"$addFields": {"_mddoc": {"$arrayElemAt": ["$_md", 0]}}},
        {
            "$addFields": {
                _META: {
                    "$ifNull": [
                        {"$arrayElemAt": [{"$ifNull": ["$_mddoc.metadata", []]}, 0]},
                        {},
                    ]
                }
            }
        },
    ]


def _format_from_language_urn(urn: Any) -> str:
    """Map a SED-ML model-language URN to a short format label.

    ``urn:sedml:language:sbml.level-2.version-3`` -> ``SBML``;
    ``urn:sedml:language:cellml.1_0`` -> ``CELLML``."""
    if not isinstance(urn, str) or not urn:
        return ""
    tail = urn.split(":")[-1]        # sbml.level-2.version-3
    base = tail.split(".")[0]        # sbml
    return base.upper()


def _model_format(models: Any) -> str:
    """First non-empty model-language label across a run's SED-ML models."""
    if not isinstance(models, list):
        return ""
    for model in models:
        if isinstance(model, dict):
            label = _format_from_language_urn(model.get("language"))
            if label:
                return label
    return ""


def _image_url(run: str, thumbnails: Any, api_base: str) -> str | None:
    """Resolve the first thumbnail to a URL.

    Absolute URLs pass through; a bare archive filename becomes the
    biosimulations file-download endpoint (verified 200 for a `browse`
    thumbnail): ``{api}/files/{run}/{file}/download/?thumbnail=browse``."""
    if not isinstance(thumbnails, list) or not thumbnails:
        return None
    first = thumbnails[0]
    if not isinstance(first, str) or not first:
        return None
    if first.startswith("http://") or first.startswith("https://"):
        return first
    if not run:
        return None
    return f"{api_base}/files/{run}/{quote(first, safe='')}/download/?thumbnail=browse"


def _stub_from_agg(doc: dict[str, Any], api_base: str) -> ProjectStub:
    meta = doc.get(_META) or {}
    run = str(doc.get("simulationRun", ""))
    summary = meta.get("abstract") or meta.get("description") or ""
    return ProjectStub(
        id=str(doc.get("id", "")),
        simulation_run=run,
        created=_iso(doc.get("created")),
        updated=_iso(doc.get("updated")),
        name=str(meta.get("title") or doc.get("id", "")),
        summary=str(summary),
        model_format=_model_format(doc.get(_MODELS)),
        image_url=_image_url(run, meta.get("thumbnails"), api_base),
    )


class ProjectDatabaseService(ABC):
    @abstractmethod
    async def query_project_stubs(
        self,
        *,
        page: int,
        per_page: int,
        filters: list[ProjectSearchFilter],
        search_term: str,
    ) -> tuple[list[ProjectStub], int]:
        """Return a page of matching project stubs and the total match count."""

    @abstractmethod
    async def query_project_stats(
        self, *, filters: list[ProjectSearchFilter], search_term: str
    ) -> list[ProjectQueryStat]:
        """Return facet counts (tags/categories) for the current query."""

    async def ensure_indexes(self) -> None:
        """Create or refresh indexes. Default: no-op (read-only source)."""
        return

    @abstractmethod
    async def close(self) -> None:
        pass


class _TtlCache:
    """Tiny in-process TTL cache. Platform-owned so paging never triggers a full
    facet rescan; decoupled from any external cache/collection."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)


class ProjectDatabaseServiceMongo(ProjectDatabaseService):
    _db_client: AsyncIOMotorClient
    _projects_col: AsyncIOMotorCollection
    _metadata_collection_name: str

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        settings = get_settings()
        self._db_client = db_client
        database = self._db_client.get_database(settings.mongodb_database)
        self._projects_col = database.get_collection(settings.mongodb_collection_projects)
        self._metadata_collection_name = settings.mongodb_collection_metadata
        self._specifications_collection_name = settings.mongodb_collection_specifications
        self._api_base = settings.biosimulations_api_base_url.rstrip("/")
        self._stats_cache = _TtlCache(settings.project_stats_cache_ttl_seconds)

    def _pipeline_prefix(
        self, filters: list[ProjectSearchFilter], search_term: str
    ) -> list[dict[str, Any]]:
        stages = _join_stages(self._metadata_collection_name)
        match = build_match_stage(filters, search_term)
        if match is not None:
            stages.append({"$match": match})
        return stages

    @override
    async def query_project_stubs(
        self,
        *,
        page: int,
        per_page: int,
        filters: list[ProjectSearchFilter],
        search_term: str,
    ) -> tuple[list[ProjectStub], int]:
        page = max(page, 1)
        per_page = max(per_page, 1)
        skip = (page - 1) * per_page

        pipeline = self._pipeline_prefix(filters, search_term)
        pipeline.append(
            {
                "$facet": {
                    # The Specifications join (for model_format) is scoped INSIDE
                    # this sub-pipeline, after $skip/$limit, so it resolves specs
                    # only for the ~per_page items on the page, not every match.
                    "items": [
                        {"$sort": {"updated": -1, "id": 1}},
                        {"$skip": skip},
                        {"$limit": per_page},
                        {
                            "$lookup": {
                                "from": self._specifications_collection_name,
                                "localField": "simulationRun",
                                "foreignField": "simulationRun",
                                "as": "_spec",
                            }
                        },
                        {
                            "$addFields": {
                                _MODELS: {
                                    "$ifNull": [{"$arrayElemAt": ["$_spec.models", 0]}, []]
                                }
                            }
                        },
                        {
                            "$project": {
                                "id": 1,
                                "simulationRun": 1,
                                "created": 1,
                                "updated": 1,
                                _META: 1,
                                _MODELS: 1,
                            }
                        },
                    ],
                    "total": [{"$count": "n"}],
                }
            }
        )
        result = await self._projects_col.aggregate(pipeline).to_list(length=1)
        if not result:
            return [], 0
        facet = result[0]
        stubs = [_stub_from_agg(dict(doc), self._api_base) for doc in facet.get("items", [])]
        total_list = facet.get("total", [])
        total = int(total_list[0]["n"]) if total_list else 0
        return stubs, total

    @override
    async def query_project_stats(
        self, *, filters: list[ProjectSearchFilter], search_term: str
    ) -> list[ProjectQueryStat]:
        cache_key = repr((sorted((f.target, tuple(f.allowable_values)) for f in filters), search_term))
        cached = self._stats_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        pipeline = self._pipeline_prefix(filters, search_term)
        pipeline.append({"$project": {_META: 1}})

        counts: dict[str, dict[str, int]] = {t: {} for t in _FACET_TARGETS}
        async for doc in self._projects_col.aggregate(pipeline):
            meta = dict(doc).get(_META) or {}
            for target, field in _FACET_TARGETS.items():
                for value in _label_values(meta.get(field)):
                    counts[target][value] = counts[target].get(value, 0) + 1

        stats: list[ProjectQueryStat] = []
        for target, freqs in counts.items():
            value_frequencies = [
                ValueFrequency(value=v, count=c)
                for v, c in sorted(freqs.items(), key=lambda kv: kv[1], reverse=True)
            ]
            stats.append(ProjectQueryStat(target=target, value_frequencies=value_frequencies))
        self._stats_cache.set(cache_key, stats)
        return stats

    @override
    async def close(self) -> None:
        self._db_client.close()
