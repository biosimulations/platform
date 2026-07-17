"""Phase 1 ranked search: a platform-owned materialized ``project_search``
collection with a MongoDB ``$text`` index we own.

Why a separate collection (option 1B in ``docs/search-engine-meilisearch-design.md``):
the searchable text lives in the biosimulations-owned ``Metadata`` collection, and
``$text`` needs a text index on the queried collection. Rather than index that
shared 272k-doc collection, the **indexer** (``rebuild_index``) reads
Projects + Metadata + Specifications (the enrichment already in ``database.py``)
and writes ~1392 flat search documents into *our* collection, where we own the
``$text`` index. This keeps us decoupled from biosimulations and is exactly the
indexer→index shape that Phase 2 (Meilisearch) will reuse.

The read path is then a single-collection query: ``$text`` + ``textScore`` sort
when searching, recency sort otherwise — no per-request ``$lookup``.
"""

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING, TEXT
from typing_extensions import override

from biosim_server.config import get_settings
from biosim_server.projects.database import (
    ProjectDatabaseService,
    _join_stages,
    _label_values,
    _META,
    _MODELS,
    _stub_from_agg,
    _TtlCache,
)
from biosim_server.projects.models import (
    ProjectQueryStat,
    ProjectSearchFilter,
    ProjectStub,
    ValueFrequency,
)

logger = logging.getLogger(__name__)

# Facet targets are flat top-level array fields on the search document (unlike the
# nested `_meta0.<field>` paths of the live-aggregation path).
_FACET_TARGETS = ("taxa", "keywords")

# Name of the (single) $text index; the searchable fields + weights come from
# settings.project_search_text_weights.
_TEXT_INDEX_NAME = "project_text"


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat()).replace("+00:00", "Z")
    return str(value or "")


def _search_document(doc: dict[str, Any], api_base: str) -> dict[str, Any]:
    """Build one flat, indexable search document from an enriched aggregation row.

    Reuses ``_stub_from_agg`` for the rendered display fields (name/summary/
    model_format/image_url) and carries the raw text + facet fields alongside for
    the ``$text`` index and filtering."""
    meta = doc.get(_META) or {}
    stub = _stub_from_agg(doc, api_base)
    return {
        "id": stub.id,
        "simulationRun": stub.simulation_run,
        # Stored as native datetimes for recency sort; formatted to ISO on read.
        "created": doc.get("created"),
        "updated": doc.get("updated"),
        "name": stub.name,
        "summary": stub.summary,
        "model_format": stub.model_format,
        "image_url": stub.image_url,
        # Searchable text (indexed).
        "title": str(meta.get("title") or ""),
        "abstract": str(meta.get("abstract") or ""),
        "description": str(meta.get("description") or ""),
        # Facets (filterable + counted).
        "taxa": _label_values(meta.get("taxa")),
        "keywords": _label_values(meta.get("keywords")),
    }


def _stub_from_search(doc: dict[str, Any]) -> ProjectStub:
    return ProjectStub(
        id=str(doc.get("id", "")),
        simulation_run=str(doc.get("simulationRun", "")),
        created=_iso(doc.get("created")),
        updated=_iso(doc.get("updated")),
        name=str(doc.get("name") or doc.get("id", "")),
        summary=str(doc.get("summary") or ""),
        model_format=str(doc.get("model_format") or ""),
        image_url=doc.get("image_url"),
    )


def _filter_query(filters: list[ProjectSearchFilter]) -> dict[str, Any]:
    """AND across targets, OR within a target — on the flat facet arrays."""
    query: dict[str, Any] = {}
    for f in filters:
        if f.target in _FACET_TARGETS and f.allowable_values:
            query[f.target] = {"$in": f.allowable_values}
    return query


class ProjectSearchServiceMongo(ProjectDatabaseService):
    """Query the materialized ``project_search`` collection; also builds it."""

    _db_client: AsyncIOMotorClient
    _search_col: AsyncIOMotorCollection

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        settings = get_settings()
        self._db_client = db_client
        database = self._db_client.get_database(settings.mongodb_database)
        self._projects_col = database.get_collection(settings.mongodb_collection_projects)
        self._search_col = database.get_collection(settings.mongodb_collection_project_search)
        self._metadata_collection_name = settings.mongodb_collection_metadata
        self._specifications_collection_name = settings.mongodb_collection_specifications
        self._api_base = settings.biosimulations_api_base_url.rstrip("/")
        self._stats_cache = _TtlCache(settings.project_stats_cache_ttl_seconds)

    # ---- indexer (write path) ----

    def _enrichment_pipeline(self) -> list[dict[str, Any]]:
        """Projects joined to Metadata + Specifications, projecting the fields the
        search document needs. Same joins as the live read path, no pagination."""
        stages = _join_stages(self._metadata_collection_name)
        stages += [
            {
                "$lookup": {
                    "from": self._specifications_collection_name,
                    "localField": "simulationRun",
                    "foreignField": "simulationRun",
                    "as": "_spec",
                }
            },
            {"$addFields": {_MODELS: {"$ifNull": [{"$arrayElemAt": ["$_spec.models", 0]}, []]}}},
            {"$project": {"id": 1, "simulationRun": 1, "created": 1, "updated": 1, _META: 1, _MODELS: 1}},
        ]
        return stages

    async def rebuild_index(self) -> int:
        """Rebuild the whole search collection from the source collections.

        Full replace — simple and correct at this corpus size (~1392). Returns the
        document count. Phase 2 swaps the write target for Meilisearch."""
        docs: list[dict[str, Any]] = []
        async for row in self._projects_col.aggregate(self._enrichment_pipeline()):
            docs.append(_search_document(dict(row), self._api_base))
        await self._search_col.delete_many({})
        if docs:
            await self._search_col.insert_many(docs)
        await self.ensure_indexes()
        self._stats_cache = _TtlCache(get_settings().project_stats_cache_ttl_seconds)
        logger.info(f"Rebuilt project search index: {len(docs)} documents")
        return len(docs)

    async def rebuild_index_if_empty(self) -> int:
        """Populate on first run (fresh deploy) without clobbering an existing
        index on every start. Returns docs written (0 if already populated)."""
        if await self._search_col.estimated_document_count() > 0:
            return 0
        return await self.rebuild_index()

    @override
    async def ensure_indexes(self) -> None:
        # A collection allows only one text index, and Mongo rejects re-creating it
        # with different fields/weights — so drop & recreate when the configured
        # searchable set changes. Rebuilding indexes existing docs automatically;
        # no data reindex needed for a weights/field-toggle change.
        weights = dict(get_settings().project_search_text_weights)
        existing = await self._search_col.index_information()
        current = existing.get(_TEXT_INDEX_NAME)
        if current is not None and current.get("weights") != weights:
            await self._search_col.drop_index(_TEXT_INDEX_NAME)
            current = None
        if current is None:
            await self._search_col.create_index(
                [(field, TEXT) for field in weights],
                weights=weights,
                name=_TEXT_INDEX_NAME,
            )
        await self._search_col.create_index("taxa")
        await self._search_col.create_index("keywords")
        await self._search_col.create_index([("updated", DESCENDING), ("id", ASCENDING)])
        await self._search_col.create_index("id", unique=True)

    # ---- query (read path) ----

    @override
    async def query_project_stubs(
        self,
        *,
        page: int,
        per_page: int,
        filters: list[ProjectSearchFilter],
        search_term: str,
    ) -> tuple[list[ProjectStub], int]:
        query = _filter_query(filters)
        page = max(page, 1)
        per_page = max(per_page, 1)
        skip = (page - 1) * per_page

        if search_term:
            query["$text"] = {"$search": search_term}
            projection = {"_txt_score": {"$meta": "textScore"}}
            cursor = (
                self._search_col.find(query, projection)
                .sort([("_txt_score", {"$meta": "textScore"})])
                .skip(skip)
                .limit(per_page)
            )
        else:
            cursor = (
                self._search_col.find(query)
                .sort([("updated", DESCENDING), ("id", ASCENDING)])
                .skip(skip)
                .limit(per_page)
            )

        total = await self._search_col.count_documents(query)
        documents = await cursor.to_list(length=per_page)
        return [_stub_from_search(dict(d)) for d in documents], total

    @override
    async def query_project_stats(
        self, *, filters: list[ProjectSearchFilter], search_term: str
    ) -> list[ProjectQueryStat]:
        # Facet counts reflect the free-text search but NOT the active structured
        # filters, so the facet menu stays stable as a user toggles filters.
        cache_key = repr(search_term)
        cached = self._stats_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        match: dict[str, Any] = {}
        if search_term:
            match["$text"] = {"$search": search_term}

        counts: dict[str, dict[str, int]] = {t: {} for t in _FACET_TARGETS}
        cursor = self._search_col.find(match, {"taxa": 1, "keywords": 1})
        async for doc in cursor:
            for target in _FACET_TARGETS:
                for value in doc.get(target) or []:
                    counts[target][value] = counts[target].get(value, 0) + 1

        stats: list[ProjectQueryStat] = []
        for target in _FACET_TARGETS:
            value_frequencies = [
                ValueFrequency(value=v, count=c)
                for v, c in sorted(counts[target].items(), key=lambda kv: kv[1], reverse=True)
            ]
            stats.append(ProjectQueryStat(target=target, value_frequencies=value_frequencies))
        self._stats_cache.set(cache_key, stats)
        return stats

    @override
    async def close(self) -> None:
        self._db_client.close()
